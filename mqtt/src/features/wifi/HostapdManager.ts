// src/features/wifi/HostapdManager.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import { Validators } from '../../utils/validators';
import logger from '../../utils/logger';
import * as fs from 'fs';

interface HostapdManagerFeatureConfig extends FeatureConfig {
  config_paths?: string[]; // Changed from config_path to config_paths, array of paths
}

// Default is now an empty array, expecting paths from config.
const DEFAULT_HOSTAPD_CONFIG_PATHS: string[] = [];

interface SsidInfo {
  ssid: string;
  sourceFile: string; // To identify which config file it came from
  bss?: string;
}

// Security type enum
type SecurityType = 'WPA2-PSK' | 'WPA3-SAE' | 'WPA2/WPA3-Mixed' | 'Open';

// Complete network configuration
interface NetworkConfig {
  ssid: string;
  password: string;
  securityType: SecurityType;
  configFiles: string[]; // Files where this SSID appears (2.4GHz + 5GHz)
  isPrimary: boolean; // True if it's the main network (not a BSS)
  bssInterfaces?: string[]; // BSS virtual interface names if applicable
  bridge?: string; // Bridge this network is attached to (e.g., localBridge, publicBridge)
}

// Security type mapping to hostapd parameters
const SECURITY_TYPE_MAPPING: Record<SecurityType, (password: string) => Record<string, string>> = {
  'WPA2-PSK': (password: string) => ({
    auth_algs: '1',
    wpa: '2',
    wpa_key_mgmt: 'WPA-PSK',
    rsn_pairwise: 'CCMP',
    wpa_passphrase: password
  }),
  'WPA3-SAE': (password: string) => ({
    auth_algs: '1',
    wpa: '2',
    wpa_key_mgmt: 'SAE',
    rsn_pairwise: 'CCMP',
    sae_password: password,
    ieee80211w: '2' // Required for WPA3
  }),
  'WPA2/WPA3-Mixed': (password: string) => ({
    auth_algs: '1',
    wpa: '2',
    wpa_key_mgmt: 'WPA-PSK SAE',
    rsn_pairwise: 'CCMP',
    wpa_passphrase: password,
    sae_password: password,
    ieee80211w: '1' // Optional for mixed mode
  }),
  'Open': () => ({
    auth_algs: '1'
    // No WPA parameters for open network
  })
};

export class HostapdManager extends BaseFeature {
  protected featureConfig: HostapdManagerFeatureConfig;
  private networks: Map<string, NetworkConfig> = new Map(); // SSID -> NetworkConfig
  private pendingChanges: Map<string, Partial<NetworkConfig>> = new Map(); // SSID -> pending changes
  private networkIdCounter: number = 0; // Counter for numeric IDs
  private ssidToId: Map<string, number> = new Map(); // SSID -> numeric ID mapping

  constructor(mqttClient: MqttClient, featureName: string = 'hostapd_manager') {
    super(mqttClient, featureName);
    this.featureConfig = this.agentConfig.features[featureName] as HostapdManagerFeatureConfig ||
                         { enabled: true, update_interval_seconds: 3600, config_paths: DEFAULT_HOSTAPD_CONFIG_PATHS };
    // Ensure config_paths is always an array
    if (!Array.isArray(this.featureConfig.config_paths)) {
        this.featureConfig.config_paths = DEFAULT_HOSTAPD_CONFIG_PATHS;
    }
  }

  protected async setup(): Promise<void> {
    // Subscribe to command topics for WiFi management
    const commandTopic = this.prefixTopic(`${this.featureName}/command/#`);
    const inputTopic = this.prefixTopic(`${this.featureName}/*/set`);
    
    await this.mqttClient.subscribe(commandTopic);
    await this.mqttClient.subscribe(inputTopic);
    
    this.mqttClient.on('message', this.handleMqttMessage.bind(this));
  }

  private async handleMqttMessage(topic: string, payload: Buffer): Promise<void> {
    const message = payload.toString();

    // Handle "Add New Hotspot" form inputs
    if (topic === this.prefixTopic(`${this.featureName}/new_ssid_input/set`)) {
      await this.publishState(`${this.featureName}/new_ssid_input/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/new_password_input/set`)) {
      await this.publishState(`${this.featureName}/new_password_input/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/new_security_select/set`)) {
      await this.publishState(`${this.featureName}/new_security_select/state`, message, true);
    }

    // Handle global action commands
    else if (topic === this.prefixTopic(`${this.featureName}/command/add_hotspot`) && message === 'ADD_HOTSPOT') {
      await this.handleAddHotspotAction();
    } else if (topic === this.prefixTopic(`${this.featureName}/command/reload_hostapd`) && message === 'RELOAD_HOSTAPD') {
      await this.handleReloadHostapdAction();
    }

    // Handle per-network configuration changes
    else {
      const topicPrefix = this.prefixTopic(`${this.featureName}/`);
      if (topic.startsWith(topicPrefix)) {
        const relativeTopic = topic.substring(topicPrefix.length);
        const parts = relativeTopic.split('/');

        if (parts.length >= 3 && parts[0].startsWith('network_')) {
          const networkIdStr = parts[0].replace('network_', '');
          const networkId = parseInt(networkIdStr, 10);
          const param = parts[1];
          const action = parts[2];

          // Find the actual SSID from numeric networkId
          const ssid = this.getSsidFromId(networkId);
          if (!ssid || !this.networks.has(ssid)) return;

          if (action === 'set') {
            // Store pending change
            if (!this.pendingChanges.has(ssid)) {
              this.pendingChanges.set(ssid, {});
            }
            const pending = this.pendingChanges.get(ssid)!;

            if (param === 'name') {
              pending.ssid = message;
            } else if (param === 'password') {
              pending.password = message;
            } else if (param === 'security') {
              pending.securityType = message as SecurityType;
            }

            // Echo state back
            await this.publishState(`${this.featureName}/network_${networkId}/${param}/state`, message, true);
          } else if (parts[1] === 'command') {
            const command = parts[2];

            if (command === 'apply' && message === 'APPLY') {
              await this.handleApplyNetworkChanges(ssid);
            } else if (command === 'delete' && message === 'DELETE') {
              await this.handleDeleteNetwork(ssid);
            }
          }
        }
      }
    }
  }

  private async handleAddHotspotAction(): Promise<void> {
    try {
      await this.publishState(`${this.featureName}/last_action/state`, 'Adding new hotspot...', true);

      // Read current form values from MQTT state
      // Broker credentials come from the agent config (env-overridable via
      // MQTT_PASSWORD) — never hard-coded in source.
      const mqtt = this.agentConfig.mqtt;
      const mqttArgs = (topic: string) => {
        const args = ['-h', mqtt.host, '-p', String(mqtt.port)];
        if (mqtt.username) args.push('-u', mqtt.username);
        if (mqtt.password) args.push('-P', mqtt.password);
        args.push('-t', topic, '-C', '1', '-W', '1');
        return args;
      };
      const ssidResult = await execute_argv('mosquitto_sub', mqttArgs(this.prefixTopic(`${this.featureName}/new_ssid_input/state`)));
      const passwordResult = await execute_argv('mosquitto_sub', mqttArgs(this.prefixTopic(`${this.featureName}/new_password_input/state`)));
      const securityResult = await execute_argv('mosquitto_sub', mqttArgs(this.prefixTopic(`${this.featureName}/new_security_select/state`)));

      const newSsid = ssidResult.stdout.trim() || 'NewNetwork';
      const newPassword = passwordResult.stdout.trim() || 'password123';
      const newSecurityType = (securityResult.stdout.trim() as SecurityType) || 'WPA2-PSK';

      // Validate
      const validationError = this.validateNetworkConfig(newSsid, newPassword, newSecurityType);
      if (validationError) {
        await this.publishState(`${this.featureName}/last_action/state`, `Validation error: ${validationError}`, true);
        return;
      }

      // Check for duplicate
      if (this.networks.has(newSsid)) {
        await this.publishState(`${this.featureName}/last_action/state`, `Error: Network ${newSsid} already exists`, true);
        return;
      }

      const configPaths = this.featureConfig.config_paths || [];
      if (configPaths.length === 0) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: No config paths configured', true);
        return;
      }

      // Add to all config files (2.4GHz + 5GHz) as BSS
      for (const configPath of configPaths) {
        await this.addNetworkToConfig(configPath, newSsid, newPassword, newSecurityType);
      }

      // Add to internal state
      this.networks.set(newSsid, {
        ssid: newSsid,
        password: newPassword,
        securityType: newSecurityType,
        configFiles: configPaths,
        isPrimary: false
      });

      // Clear form
      await this.publishState(`${this.featureName}/new_ssid_input/state`, '', true);
      await this.publishState(`${this.featureName}/new_password_input/state`, '', true);
      await this.publishState(`${this.featureName}/new_security_select/state`, 'WPA2-PSK', true);

      await this.publishState(`${this.featureName}/last_action/state`, `Successfully added ${newSsid}. Reload hostapd to apply.`, true);

    } catch (error: any) {
      const errorMessage = error.message || 'Unknown error during hotspot creation';
      logger.error(`Error adding hotspot for ${this.featureName}:`, errorMessage);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${errorMessage}`, true);
    }
  }

  // Add a new network as BSS to a config file
  private async addNetworkToConfig(filePath: string, ssid: string, password: string, securityType: SecurityType): Promise<void> {
    const readResult = await execute_argv('cat', [filePath]);
    if (readResult.exitCode !== 0) {
      throw new Error(`Failed to read config file: ${filePath}`);
    }

    const lines = readResult.stdout.split('\n');
    const updatedLines: string[] = [];

    // Find the last BSS section or end of primary network
    let lastBssLine = -1;
    let nextBssNumber = 1;

    for (let i = 0; i < lines.length; i++) {
      const trimmed = lines[i].trim();
      if (trimmed.startsWith('bss=')) {
        lastBssLine = i;
        // Extract BSS number (e.g., wlp11s1 -> 1, wlp10s2 -> 2)
        const match = trimmed.match(/bss=\w+(\d+)$/);
        if (match) {
          nextBssNumber = Math.max(nextBssNumber, parseInt(match[1]) + 1);
        }
      }
    }

    // Determine interface base name from file
    const interfaceBase = filePath.includes('5Ghz') ? 'wlp10s' : 'wlp11s';
    const newBssInterface = `${interfaceBase}${nextBssNumber}`;

    // Build new BSS section
    const securityParams = SECURITY_TYPE_MAPPING[securityType](password);
    const bssSection = [
      ``,
      `bss=${newBssInterface}`,
      `ssid=${ssid}`,
      ...Object.entries(securityParams).map(([key, value]) => `${key}=${value}`),
      `bridge=localBridge`,
      `access_network_type=0`
    ];

    // Insert at the end
    updatedLines.push(...lines);
    updatedLines.push(...bssSection);

    // Backup and write. The temp file is written via fs (no shell → the SSID /
    // passphrase cannot inject a command), then moved into place with sudo.
    await execute_argv('sudo', ['cp', filePath, `${filePath}.backup`]);
    const tempFile = `/tmp/hostapd_${Date.now()}.conf`;
    fs.writeFileSync(tempFile, updatedLines.join('\n'));
    await execute_argv('sudo', ['mv', tempFile, filePath]);

    logger.info(`Added new network ${ssid} to ${filePath} as ${newBssInterface}`);
  }

  private async handleReloadHostapdAction(): Promise<void> {
    try {
      await this.publishState(`${this.featureName}/last_action/state`, 'Reloading hostapd...', true);

      await execute_argv('sudo', ['systemctl', 'reload', 'hostapd']);

      await this.publishState(`${this.featureName}/last_action/state`, 'Hostapd reloaded successfully', true);

    } catch (error: any) {
      const errorMessage = error.message || 'Unknown error during hostapd reload';
      logger.error(`Error reloading hostapd for ${this.featureName}:`, errorMessage);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${errorMessage}`, true);
    }
  }

  // Apply configuration changes to a network
  private async handleApplyNetworkChanges(ssid: string): Promise<void> {
    try {
      const network = this.networks.get(ssid);
      if (!network) {
        await this.publishState(`${this.featureName}/last_action/state`, `Error: Network ${ssid} not found`, true);
        return;
      }

      const pending = this.pendingChanges.get(ssid);
      if (!pending || Object.keys(pending).length === 0) {
        await this.publishState(`${this.featureName}/last_action/state`, `No changes to apply for ${ssid}`, true);
        return;
      }

      await this.publishState(`${this.featureName}/last_action/state`, `Applying changes to ${ssid}...`, true);

      // Validate changes
      const newSsid = pending.ssid || network.ssid;
      const newPassword = pending.password !== undefined ? pending.password : network.password;
      const newSecurityType = pending.securityType || network.securityType;

      const validationError = this.validateNetworkConfig(newSsid, newPassword, newSecurityType);
      if (validationError) {
        await this.publishState(`${this.featureName}/last_action/state`, `Validation error: ${validationError}`, true);
        return;
      }

      // Update config files
      for (const configFile of network.configFiles) {
        await this.updateConfigFile(configFile, ssid, newSsid, newPassword, newSecurityType);
      }

      // Update internal state
      if (newSsid !== ssid) {
        // SSID changed - move to new key
        this.networks.delete(ssid);
        this.networks.set(newSsid, {
          ssid: newSsid,
          password: newPassword,
          securityType: newSecurityType,
          configFiles: network.configFiles,
          isPrimary: network.isPrimary,
          bssInterfaces: network.bssInterfaces
        });
      } else {
        // Update in place
        network.ssid = newSsid;
        network.password = newPassword;
        network.securityType = newSecurityType;
      }

      // Clear pending changes
      this.pendingChanges.delete(ssid);

      await this.publishState(`${this.featureName}/last_action/state`, `Successfully updated ${ssid}. Reload hostapd to apply.`, true);

    } catch (error: any) {
      logger.error(`Error applying changes to network ${ssid}:`, error.message);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, true);
    }
  }

  // Delete a network from all config files
  private async handleDeleteNetwork(ssid: string): Promise<void> {
    try {
      const network = this.networks.get(ssid);
      if (!network) {
        await this.publishState(`${this.featureName}/last_action/state`, `Error: Network ${ssid} not found`, true);
        return;
      }

      await this.publishState(`${this.featureName}/last_action/state`, `Deleting network ${ssid}...`, true);

      // Remove from config files
      for (const configFile of network.configFiles) {
        await this.removeNetworkFromConfig(configFile, ssid);
      }

      // Remove from internal state
      this.networks.delete(ssid);
      this.pendingChanges.delete(ssid);

      await this.publishState(`${this.featureName}/last_action/state`, `Successfully deleted ${ssid}. Reload hostapd to apply.`, true);

    } catch (error: any) {
      logger.error(`Error deleting network ${ssid}:`, error.message);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, true);
    }
  }

  // Validate network configuration
  private validateNetworkConfig(ssid: string, password: string, securityType: SecurityType): string | null {
    if (!ssid || ssid.length === 0 || ssid.length > 32) {
      return 'SSID must be between 1 and 32 characters';
    }

    // Reject control characters that would corrupt the hostapd config file
    // (values are written verbatim as `ssid=` / passphrase lines).
    if (!Validators.isConfigSafeLine(ssid)) {
      return 'SSID contains invalid characters';
    }

    if (securityType !== 'Open') {
      if (!password || password.length < 8 || password.length > 63) {
        return 'Password must be between 8 and 63 characters for secured networks';
      }
      if (!Validators.isConfigSafeLine(password)) {
        return 'Password contains invalid characters';
      }
    }

    return null;
  }

  // Update a specific network in a config file
  private async updateConfigFile(filePath: string, oldSsid: string, newSsid: string, newPassword: string, newSecurityType: SecurityType): Promise<void> {
    // Read config file
    const readResult = await execute_argv('cat', [filePath]);
    if (readResult.exitCode !== 0) {
      throw new Error(`Failed to read config file: ${filePath}`);
    }

    const lines = readResult.stdout.split('\n');
    const updatedLines: string[] = [];
    let inTargetNetwork = false;
    let inBssSection = false;
    let currentSsid = '';
    let foundNetwork = false;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();

      // Detect BSS section
      if (trimmed.startsWith('bss=')) {
        inBssSection = true;
        inTargetNetwork = false;
        currentSsid = '';
        updatedLines.push(line);
        continue;
      }

      // Detect SSID
      if (trimmed.startsWith('ssid=')) {
        currentSsid = trimmed.substring('ssid='.length).trim();

        if (currentSsid === oldSsid) {
          inTargetNetwork = true;
          foundNetwork = true;
          updatedLines.push(`ssid=${newSsid}`);
          continue;
        }
      }

      // If we're in the target network, update security parameters
      if (inTargetNetwork) {
        // Skip old security-related lines
        if (trimmed.startsWith('wpa_passphrase=') ||
            trimmed.startsWith('sae_password=') ||
            trimmed.startsWith('wpa=') ||
            trimmed.startsWith('wpa_key_mgmt=') ||
            trimmed.startsWith('auth_algs=') ||
            trimmed.startsWith('rsn_pairwise=') ||
            trimmed.startsWith('ieee80211w=')) {
          continue;
        }

        // After ssid line, inject new security parameters
        if (i > 0 && lines[i-1].trim().startsWith('ssid=')) {
          const securityParams = SECURITY_TYPE_MAPPING[newSecurityType](newPassword);
          for (const [key, value] of Object.entries(securityParams)) {
            updatedLines.push(`${key}=${value}`);
          }
        }

        updatedLines.push(line);

        // Check if we're exiting this network section (next ssid or bss)
        if (trimmed.startsWith('ssid=') && currentSsid !== oldSsid) {
          inTargetNetwork = false;
        }
      } else {
        updatedLines.push(line);
      }
    }

    if (!foundNetwork) {
      throw new Error(`Network ${oldSsid} not found in ${filePath}`);
    }

    // Backup and write. The temp file is written via fs (no shell → the SSID /
    // passphrase cannot inject a command), then moved into place with sudo.
    await execute_argv('sudo', ['cp', filePath, `${filePath}.backup`]);
    const tempFile = `/tmp/hostapd_${Date.now()}.conf`;
    fs.writeFileSync(tempFile, updatedLines.join('\n'));
    await execute_argv('sudo', ['mv', tempFile, filePath]);

    logger.info(`Updated network ${oldSsid} -> ${newSsid} in ${filePath}`);
  }

  // Remove a network from a config file
  private async removeNetworkFromConfig(filePath: string, ssid: string): Promise<void> {
    const readResult = await execute_argv('cat', [filePath]);
    if (readResult.exitCode !== 0) {
      throw new Error(`Failed to read config file: ${filePath}`);
    }

    const lines = readResult.stdout.split('\n');
    const updatedLines: string[] = [];
    let inTargetNetwork = false;
    let skipUntilNextSection = false;
    let currentSsid = '';

    for (const line of lines) {
      const trimmed = line.trim();

      // Detect BSS section or interface section (start of new network)
      if (trimmed.startsWith('bss=') || trimmed.startsWith('interface=')) {
        if (skipUntilNextSection) {
          skipUntilNextSection = false;
          inTargetNetwork = false;
        }
        updatedLines.push(line);
        continue;
      }

      // Detect SSID
      if (trimmed.startsWith('ssid=')) {
        currentSsid = trimmed.substring('ssid='.length).trim();

        if (currentSsid === ssid) {
          inTargetNetwork = true;
          skipUntilNextSection = true;
          continue; // Skip this SSID line
        }
      }

      // Skip lines in target network
      if (inTargetNetwork && skipUntilNextSection) {
        continue;
      }

      updatedLines.push(line);
    }

    // Backup and write. The temp file is written via fs (no shell → the SSID /
    // passphrase cannot inject a command), then moved into place with sudo.
    await execute_argv('sudo', ['cp', filePath, `${filePath}.backup`]);
    const tempFile = `/tmp/hostapd_${Date.now()}.conf`;
    fs.writeFileSync(tempFile, updatedLines.join('\n'));
    await execute_argv('sudo', ['mv', tempFile, filePath]);

    logger.info(`Removed network ${ssid} from ${filePath}`);
  }

  private parseHostapdConfig(configContent: string, filePath: string): SsidInfo[] {
    const ssids: SsidInfo[] = [];
    if (!configContent) return ssids;

    const lines = configContent.split('\n');
    let currentBss: string | undefined = undefined;
    let currentSsid: string | undefined = undefined;

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (trimmedLine.startsWith('#') || !trimmedLine) continue;

      if (trimmedLine.startsWith('bss=')) {
        if (currentSsid && !currentBss) {
            ssids.push({ ssid: currentSsid, sourceFile: filePath });
            currentSsid = undefined;
        }
        currentBss = trimmedLine.substring('bss='.length).trim();
        currentSsid = undefined;
      } else if (trimmedLine.startsWith('ssid=')) {
        currentSsid = trimmedLine.substring('ssid='.length).trim();
      }

      if (currentSsid && currentBss) {
        if (!ssids.find(s => s.bss === currentBss && s.ssid === currentSsid && s.sourceFile === filePath)) {
             ssids.push({ ssid: currentSsid, bss: currentBss, sourceFile: filePath });
        }
      }
    }
    if (currentSsid && !currentBss && !ssids.find(s => s.ssid === currentSsid && !s.bss && s.sourceFile === filePath)) {
        ssids.push({ ssid: currentSsid, sourceFile: filePath });
    }

    return ssids.filter(s => s.ssid);
  }

  // Enhanced parser to extract complete network configuration
  private parseNetworkConfig(configContent: string, filePath: string): Map<string, NetworkConfig> {
    const networks = new Map<string, NetworkConfig>();
    if (!configContent) return networks;

    const lines = configContent.split('\n');
    let currentBss: string | undefined = undefined;
    let currentNetwork: Partial<NetworkConfig> = {};
    let isInPrimaryNetwork = true;

    const finalizeNetwork = () => {
      if (currentNetwork.ssid) {
        const existingNetwork = networks.get(currentNetwork.ssid);
        if (existingNetwork) {
          // Merge with existing (same SSID in another file or BSS)
          if (!existingNetwork.configFiles.includes(filePath)) {
            existingNetwork.configFiles.push(filePath);
          }
          if (currentBss && !existingNetwork.bssInterfaces?.includes(currentBss)) {
            existingNetwork.bssInterfaces = existingNetwork.bssInterfaces || [];
            existingNetwork.bssInterfaces.push(currentBss);
          }
          // Update bridge if found in this section (last bridge wins for same SSID)
          if (currentNetwork.bridge) {
            existingNetwork.bridge = currentNetwork.bridge;
          }
        } else {
          // New network
          networks.set(currentNetwork.ssid, {
            ssid: currentNetwork.ssid,
            password: currentNetwork.password || '',
            securityType: currentNetwork.securityType || 'WPA2-PSK',
            configFiles: [filePath],
            isPrimary: !currentBss,
            bssInterfaces: currentBss ? [currentBss] : undefined,
            bridge: currentNetwork.bridge
          });
        }
      }
    };

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (trimmedLine.startsWith('#') || !trimmedLine) continue;

      // New BSS section
      if (trimmedLine.startsWith('bss=')) {
        finalizeNetwork();
        currentBss = trimmedLine.substring('bss='.length).trim();
        currentNetwork = {};
        isInPrimaryNetwork = false;
        continue;
      }

      // Extract configuration parameters
      if (trimmedLine.startsWith('ssid=')) {
        currentNetwork.ssid = trimmedLine.substring('ssid='.length).trim();
      } else if (trimmedLine.startsWith('bridge=')) {
        currentNetwork.bridge = trimmedLine.substring('bridge='.length).trim();
      } else if (trimmedLine.startsWith('wpa_passphrase=')) {
        currentNetwork.password = trimmedLine.substring('wpa_passphrase='.length).trim();
      } else if (trimmedLine.startsWith('sae_password=')) {
        // WPA3 uses sae_password
        if (!currentNetwork.password) {
          currentNetwork.password = trimmedLine.substring('sae_password='.length).trim();
        }
      } else if (trimmedLine.startsWith('wpa_key_mgmt=')) {
        const keyMgmt = trimmedLine.substring('wpa_key_mgmt='.length).trim();
        if (keyMgmt === 'SAE') {
          currentNetwork.securityType = 'WPA3-SAE';
        } else if (keyMgmt.includes('WPA-PSK') && keyMgmt.includes('SAE')) {
          currentNetwork.securityType = 'WPA2/WPA3-Mixed';
        } else if (keyMgmt.includes('WPA-PSK')) {
          currentNetwork.securityType = 'WPA2-PSK';
        }
      } else if (trimmedLine.startsWith('wpa=')) {
        // If no wpa parameter, it's likely Open
        const wpaValue = trimmedLine.substring('wpa='.length).trim();
        if (wpaValue === '0' || !wpaValue) {
          currentNetwork.securityType = 'Open';
        }
      }
    }

    // Finalize last network
    finalizeNetwork();

    return networks;
  }

  protected async publishDiscovery(): Promise<void> {
    // Load networks first to create dynamic entities
    await this.loadNetworks();

    // Summary sensor (shows all SSIDs)
    await this.publishEntityDiscovery('sensor', 'ssids_summary', {
      name: `Network WiFi SSIDs Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/ssids_count/state`),
      icon: 'mdi:wifi',
      entity_category: 'diagnostic' as 'diagnostic',
      json_attributes_topic: this.prefixTopic(`${this.featureName}/ssids_list/attributes`),
    });

    // WiFi management entities
    await this.publishEntityDiscovery('sensor', 'last_action', {
      name: `Network WiFi Last Action Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/last_action/state`),
      icon: 'mdi:wifi-sync',
      entity_category: 'diagnostic' as 'diagnostic',
    });

    // === "Add New Hotspot" form ===
    await this.publishEntityDiscovery('text', 'new_ssid_input', {
      name: `Network WiFi New SSID Input`,
      command_topic: this.prefixTopic(`${this.featureName}/new_ssid_input/set`),
      state_topic: this.prefixTopic(`${this.featureName}/new_ssid_input/state`),
      icon: 'mdi:wifi',
      entity_category: 'config' as 'config',
      mode: 'text',
    });

    await this.publishEntityDiscovery('text', 'new_password_input', {
      name: `Network WiFi New Password Input`,
      command_topic: this.prefixTopic(`${this.featureName}/new_password_input/set`),
      state_topic: this.prefixTopic(`${this.featureName}/new_password_input/state`),
      icon: 'mdi:key',
      entity_category: 'config' as 'config',
      mode: 'password',
    });

    await this.publishEntityDiscovery('select', 'new_security_select', {
      name: `Network WiFi New Security Type`,
      command_topic: this.prefixTopic(`${this.featureName}/new_security_select/set`),
      state_topic: this.prefixTopic(`${this.featureName}/new_security_select/state`),
      icon: 'mdi:shield-lock',
      entity_category: 'config' as 'config',
      options: ['WPA2-PSK', 'WPA3-SAE', 'WPA2/WPA3-Mixed', 'Open'],
    });

    await this.publishEntityDiscovery('button', 'add_hotspot_btn', {
      name: `Network WiFi Add Hotspot Button`,
      command_topic: this.prefixTopic(`${this.featureName}/command/add_hotspot`),
      payload_press: 'ADD_HOTSPOT',
      icon: 'mdi:wifi-plus',
      entity_category: 'config' as 'config',
    });

    await this.publishEntityDiscovery('button', 'reload_hostapd_btn', {
      name: `Network WiFi Reload Hostapd Button`,
      command_topic: this.prefixTopic(`${this.featureName}/command/reload_hostapd`),
      payload_press: 'RELOAD_HOSTAPD',
      icon: 'mdi:reload',
      entity_category: 'config' as 'config',
    });

    // === Create entities for each existing network ===
    for (const [ssid, network] of this.networks) {
      await this.publishNetworkEntities(ssid, network);
    }

    // Initialize states for "Add New" form
    await this.publishState(`${this.featureName}/last_action/state`, 'Ready', true);
    await this.publishState(`${this.featureName}/new_ssid_input/state`, '', true);
    await this.publishState(`${this.featureName}/new_password_input/state`, '', true);
    await this.publishState(`${this.featureName}/new_security_select/state`, 'WPA2-PSK', true);
  }

  // Helper method to load networks before discovery
  private async loadNetworks(): Promise<void> {
    const configPaths = this.featureConfig.config_paths || DEFAULT_HOSTAPD_CONFIG_PATHS;
    const allNetworks = new Map<string, NetworkConfig>();

    for (const configPath of configPaths) {
      try {
        const configFileContent = await execute_argv('cat', [configPath]);
        if (configFileContent.exitCode === 0 && !configFileContent.stderr) {
          const networksFromFile = this.parseNetworkConfig(configFileContent.stdout, configPath);
          networksFromFile.forEach((network, ssid) => {
            if (allNetworks.has(ssid)) {
              const existing = allNetworks.get(ssid)!;
              network.configFiles.forEach(file => {
                if (!existing.configFiles.includes(file)) {
                  existing.configFiles.push(file);
                }
              });
            } else {
              allNetworks.set(ssid, network);
            }
          });
        }
      } catch (error: any) {
        logger.error(`Error loading network configs during discovery: ${error.message}`);
      }
    }

    this.networks = allNetworks;
  }

  // Publish entities for a specific network
  private async publishNetworkEntities(ssid: string, network: NetworkConfig): Promise<void> {
    const networkId = this.getNetworkId(ssid);

    // Text input: SSID name
    await this.publishEntityDiscovery('text', `network_${networkId}_name`, {
      name: `Network WiFi ${ssid} SSID`,
      command_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/name/set`),
      state_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/name/state`),
      icon: 'mdi:wifi',
      entity_category: 'config' as 'config',
      mode: 'text',
    });

    // Text input: Password
    await this.publishEntityDiscovery('text', `network_${networkId}_password`, {
      name: `Network WiFi ${ssid} Password`,
      command_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/password/set`),
      state_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/password/state`),
      icon: 'mdi:key',
      entity_category: 'config' as 'config',
      mode: 'text',
    });

    // Select: Security type
    await this.publishEntityDiscovery('select', `network_${networkId}_security`, {
      name: `Network WiFi ${ssid} Security`,
      command_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/security/set`),
      state_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/security/state`),
      icon: 'mdi:shield-lock',
      entity_category: 'config' as 'config',
      options: ['WPA2-PSK', 'WPA3-SAE', 'WPA2/WPA3-Mixed', 'Open'],
    });

    // Button: Apply changes
    await this.publishEntityDiscovery('button', `network_${networkId}_apply`, {
      name: `Network WiFi ${ssid} Apply`,
      command_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/command/apply`),
      payload_press: 'APPLY',
      icon: 'mdi:check-circle',
      entity_category: 'config' as 'config',
    });

    // Button: Delete network
    await this.publishEntityDiscovery('button', `network_${networkId}_delete`, {
      name: `Network WiFi ${ssid} Delete`,
      command_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/command/delete`),
      payload_press: 'DELETE',
      icon: 'mdi:delete',
      entity_category: 'config' as 'config',
    });

    // Sensor: Status
    await this.publishEntityDiscovery('sensor', `network_${networkId}_status`, {
      name: `Network WiFi ${ssid} Status`,
      state_topic: this.prefixTopic(`${this.featureName}/network_${networkId}/status/state`),
      icon: 'mdi:information',
      entity_category: 'diagnostic' as 'diagnostic',
    });

    // Initialize states
    await this.publishState(`${this.featureName}/network_${networkId}/name/state`, network.ssid, true);
    await this.publishState(`${this.featureName}/network_${networkId}/password/state`, network.password, true);
    await this.publishState(`${this.featureName}/network_${networkId}/security/state`, network.securityType, true);

    const statusText = `Active (${network.configFiles.length > 1 ? '2.4+5GHz' : network.configFiles[0].includes('5Ghz') ? '5GHz' : '2.4GHz'})`;
    await this.publishState(`${this.featureName}/network_${networkId}/status/state`, statusText, true);
  }

  protected async update(): Promise<void> {
    const configPaths = this.featureConfig.config_paths || DEFAULT_HOSTAPD_CONFIG_PATHS;
    if (configPaths.length === 0) {
      logger.warn(`No hostapd config paths specified for ${this.featureName}.`);
      await this.publishState(`${this.featureName}/ssids_count/state`, 0, true);
      await this.publishAttributes(`${this.featureName}/ssids_list/attributes`, { ssids: [] }, true);
      return;
    }

    // Parse all config files and build network map
    const allNetworks = new Map<string, NetworkConfig>();
    let errorOccurred = false;

    for (const configPath of configPaths) {
      try {
        const configFileContent = await execute_argv('cat', [configPath]);
        if (configFileContent.exitCode !== 0 || configFileContent.stderr) {
            logger.error(`Error reading hostapd config ${configPath}: ${configFileContent.stderr || `Exit code ${configFileContent.exitCode}`}`);
            errorOccurred = true;
            continue;
        }

        const networksFromFile = this.parseNetworkConfig(configFileContent.stdout, configPath);
        // Merge networks with same SSID
        networksFromFile.forEach((network, ssid) => {
          if (allNetworks.has(ssid)) {
            const existing = allNetworks.get(ssid)!;
            // Merge config files
            network.configFiles.forEach(file => {
              if (!existing.configFiles.includes(file)) {
                existing.configFiles.push(file);
              }
            });
            // Merge BSS interfaces
            if (network.bssInterfaces) {
              existing.bssInterfaces = existing.bssInterfaces || [];
              network.bssInterfaces.forEach(bss => {
                if (!existing.bssInterfaces!.includes(bss)) {
                  existing.bssInterfaces!.push(bss);
                }
              });
            }
          } else {
            allNetworks.set(ssid, network);
          }
        });
      } catch (error: any) {
        logger.error(`Exception reading or parsing hostapd config ${configPath} for ${this.featureName}:`, error.message);
        errorOccurred = true;
      }
    }

    // Update internal state
    this.networks = allNetworks;

    // Publish summary
    if (errorOccurred && allNetworks.size === 0) {
      await this.publishState(`${this.featureName}/ssids_count/state`, 'Error', true);
      await this.publishAttributes(`${this.featureName}/ssids_list/attributes`, { error: "Failed to read one or more config files.", list: [] }, true);
    } else {
      const networksSummary = Array.from(allNetworks.values()).map(network => ({
        name: network.ssid,
        sources: network.configFiles.map(f => f.split('/').pop() || f),
        security: network.securityType,
        bands: network.configFiles.length > 1 ? '2.4GHz + 5GHz' : network.configFiles[0].includes('5Ghz') ? '5GHz' : '2.4GHz',
        bridge: network.bridge || 'localBridge' // Default to localBridge if not specified
      }));

      await this.publishState(`${this.featureName}/ssids_count/state`, allNetworks.size, true);
      await this.publishAttributes(`${this.featureName}/ssids_list/attributes`, { list: networksSummary }, true);
    }

    // Update per-network state topics
    for (const [ssid, network] of allNetworks) {
      const networkId = this.getNetworkId(ssid);
      await this.publishState(`${this.featureName}/network_${networkId}/name/state`, network.ssid, true);
      await this.publishState(`${this.featureName}/network_${networkId}/password/state`, network.password, true);
      await this.publishState(`${this.featureName}/network_${networkId}/security/state`, network.securityType, true);

      const statusText = `Active (${network.configFiles.length > 1 ? '2.4+5GHz' : network.configFiles[0].includes('5Ghz') ? '5GHz' : '2.4GHz'})`;
      await this.publishState(`${this.featureName}/network_${networkId}/status/state`, statusText, true);
    }
  }

  // Helper to get numeric network ID
  private getNetworkId(ssid: string): number {
    if (!this.ssidToId.has(ssid)) {
      this.networkIdCounter++;
      this.ssidToId.set(ssid, this.networkIdCounter);
    }
    return this.ssidToId.get(ssid)!;
  }

  // Find SSID from numeric ID
  private getSsidFromId(networkId: number): string | undefined {
    for (const [ssid, id] of this.ssidToId.entries()) {
      if (id === networkId) {
        return ssid;
      }
    }
    return undefined;
  }
}
