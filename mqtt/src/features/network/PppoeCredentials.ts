// src/features/network/PppoeCredentials.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_command, execute_argv } from '../../utils/exec';
import { Validators, rejectInvalid } from '../../utils/validators';
import logger from '../../utils/logger';
import * as fs from 'fs';

interface PppoeCredentialsFeatureConfig extends FeatureConfig {
  chap_secrets_path?: string;
  pap_secrets_path?: string;
  nmconnection_path?: string;
}

const DEFAULT_CHAP_SECRETS_PATH = '/etc/ppp/chap-secrets';
const DEFAULT_PAP_SECRETS_PATH = '/etc/ppp/pap-secrets';
const DEFAULT_NMCONNECTION_PATH = '/etc/NetworkManager/system-connections/pppoe-enp6s0.835.nmconnection';

export class PppoeCredentials extends BaseFeature {
  protected featureConfig: PppoeCredentialsFeatureConfig;

  constructor(mqttClient: MqttClient, featureName: string = 'pppoe_credentials') {
    super(mqttClient, featureName);
    this.featureConfig = this.agentConfig.features[featureName] as PppoeCredentialsFeatureConfig ||
                         {
                           enabled: true,
                           update_interval_seconds: 3600 * 6, // Check every 6 hours
                           chap_secrets_path: DEFAULT_CHAP_SECRETS_PATH,
                           pap_secrets_path: DEFAULT_PAP_SECRETS_PATH,
                           nmconnection_path: DEFAULT_NMCONNECTION_PATH,
                         };
  }

  // Read PPPoE credentials from NetworkManager connection file
  private async readNetworkManagerCredentials(): Promise<{ username: string; password: string } | null> {
    try {
      const nmPath = this.featureConfig.nmconnection_path || DEFAULT_NMCONNECTION_PATH;

      // Read file with sudo (nmconnection files are restricted to root)
      const result = await execute_argv('sudo', ['cat', nmPath]);

      if (result.exitCode !== 0) {
        logger.warn(`Failed to read NetworkManager connection file: ${nmPath}`);
        return null;
      }

      const content = result.stdout;
      let username = '';
      let password = '';

      // Parse INI-style format for [pppoe] section
      const lines = content.split('\n');
      let inPppoeSection = false;

      for (const line of lines) {
        const trimmed = line.trim();

        if (trimmed === '[pppoe]') {
          inPppoeSection = true;
          continue;
        }

        if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
          inPppoeSection = false;
          continue;
        }

        if (inPppoeSection) {
          if (trimmed.startsWith('username=')) {
            username = trimmed.substring('username='.length);
          } else if (trimmed.startsWith('password=')) {
            password = trimmed.substring('password='.length);
          }
        }
      }

      if (username || password) {
        return { username, password };
      }

      return null;
    } catch (error: any) {
      logger.error(`Error reading NetworkManager credentials:`, error.message);
      return null;
    }
  }

  private async getPppoeConnectionStatus(): Promise<{
    status: string;
    interface?: string;
    ip?: string;
    gateway?: string;
    uptime?: string;
  }> {
    try {
      // Check if ppp0 interface exists and is up
      const interfaceCheck = await execute_command('ip addr show ppp0', false);
      
      if (interfaceCheck.exitCode === 0) {
        // Extract IP from interface info
        const ipMatch = interfaceCheck.stdout.match(/inet (\d+\.\d+\.\d+\.\d+)/);
        const ip = ipMatch ? ipMatch[1] : undefined;
        
        // Get gateway/remote IP
        const routeCheck = await execute_command('ip route show dev ppp0', false);
        const gatewayMatch = routeCheck.stdout.match(/default via (\d+\.\d+\.\d+\.\d+)/);
        const gateway = gatewayMatch ? gatewayMatch[1] : undefined;
        
        // Try to get connection uptime from systemctl if pppoe service exists
        let uptime: string | undefined;
        try {
          const statusCheck = await execute_command('systemctl status ppp@provider.service 2>/dev/null || systemctl status pppoe-server 2>/dev/null || true', false);
          const activeMatch = statusCheck.stdout.match(/Active:\s+active\s+\(running\)\s+since\s+(.+);/);
          if (activeMatch) {
            uptime = activeMatch[1];
          }
        } catch (error) {
          // Ignore systemctl errors
        }
        
        return {
          status: 'CONNECTED',
          interface: 'ppp0',
          ip,
          gateway,
          uptime
        };
      }
      
      return { status: 'DISCONNECTED' };
    } catch (error) {
      logger.error(`Error checking PPPoE connection status:`, error);
      return { status: 'ERROR' };
    }
  }

  protected async publishDiscovery(): Promise<void> {
    await this.publishEntityDiscovery('binary_sensor', 'config_found', {
      name: `Network PPPoE Config Status Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/config_status/state`),
      payload_on: "DETECTED",
      payload_off: "NOT_DETECTED",
      icon: 'mdi:file-key-outline',
      entity_category: 'diagnostic' as 'diagnostic',
    });

    await this.publishEntityDiscovery('sensor', 'connection_status', {
      name: `Network PPPoE Connection Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/connection/state`),
      icon: 'mdi:wan',
      entity_category: 'diagnostic' as 'diagnostic',
      json_attributes_topic: this.prefixTopic(`${this.featureName}/connection/attributes`),
    });

    await this.publishEntityDiscovery('sensor', 'credentials_count', {
      name: `Network PPPoE Credentials Count Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/credentials_count/state`),
      icon: 'mdi:account-key',
      entity_category: 'diagnostic' as 'diagnostic',
    });


    // PPPoE credentials management entities
    await this.publishEntityDiscovery('sensor', 'last_action', {
      name: `Network PPPoE Last Action Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/last_action/state`),
      icon: 'mdi:account-sync',
      entity_category: 'diagnostic' as 'diagnostic',
    });

    // Input text entities for PPPoE credentials
    await this.publishEntityDiscovery('text', 'username_input', {
      name: `Network PPPoE Username Input`,
      command_topic: this.prefixTopic(`${this.featureName}/username_input/set`),
      state_topic: this.prefixTopic(`${this.featureName}/username_input/state`),
      icon: 'mdi:account',
      entity_category: 'config' as 'config',
      mode: 'text',
    });

    await this.publishEntityDiscovery('text', 'password_input', {
      name: `Network PPPoE Password Input`,
      command_topic: this.prefixTopic(`${this.featureName}/password_input/set`),
      state_topic: this.prefixTopic(`${this.featureName}/password_input/state`),
      icon: 'mdi:key',
      entity_category: 'config' as 'config',
      mode: 'text',
    });

    // Action buttons
    await this.publishEntityDiscovery('button', 'save_credentials_btn', {
      name: `Network Save PPPoE Credentials Button`,
      command_topic: this.prefixTopic(`${this.featureName}/command/save_credentials`),
      payload_press: 'SAVE_CREDENTIALS',
      icon: 'mdi:content-save',
      entity_category: 'config' as 'config',
    });

    await this.publishEntityDiscovery('button', 'test_connection_btn', {
      name: `Network Test PPPoE Connection Button`,
      command_topic: this.prefixTopic(`${this.featureName}/command/test_connection`),
      payload_press: 'TEST_CONNECTION',
      icon: 'mdi:connection',
      entity_category: 'config' as 'config',
    });

    // Initialize states - read real credentials from NetworkManager
    await this.publishState(`${this.featureName}/last_action/state`, 'Ready', true);

    const credentials = await this.readNetworkManagerCredentials();
    if (credentials) {
      await this.publishState(`${this.featureName}/username_input/state`, credentials.username, true);
      await this.publishState(`${this.featureName}/password_input/state`, credentials.password, true);
    } else {
      await this.publishState(`${this.featureName}/username_input/state`, '', true);
      await this.publishState(`${this.featureName}/password_input/state`, '', true);
    }
  }

  private async checkFileExists(filePath: string): Promise<boolean> {
    try {
      return fs.existsSync(filePath);
    } catch {
      return false;
    }
  }

  private async countCredentials(filePath: string): Promise<number> {
    // filePath is a fixed config path (never MQTT-controlled); a shell is used
    // here only for the `grep | echo` fallback. No external value is interpolated.
    try {
      const result = await execute_command(`grep -c "^[^#]" "${filePath}" 2>/dev/null || echo "0"`, false);
      return parseInt(result.stdout.trim()) || 0;
    } catch {
      return 0;
    }
  }

  protected async update(): Promise<void> {
    const chapPath = this.featureConfig.chap_secrets_path || DEFAULT_CHAP_SECRETS_PATH;
    const papPath = this.featureConfig.pap_secrets_path || DEFAULT_PAP_SECRETS_PATH;
    let detected = false;
    let credentialsCount = 0;

    try {
      const chapExists = await this.checkFileExists(chapPath);
      if (chapExists) {
        logger.debug(`PPPoE CHAP secrets file found at ${chapPath}`);
        detected = true;
        credentialsCount += await this.countCredentials(chapPath);
      }

      const papExists = await this.checkFileExists(papPath);
      if (papExists) {
        logger.debug(`PPPoE PAP secrets file found at ${papPath}`);
        detected = true;
        credentialsCount += await this.countCredentials(papPath);
      }

      await this.publishState(`${this.featureName}/config_status/state`, detected ? "DETECTED" : "NOT_DETECTED", true);
      await this.publishState(`${this.featureName}/credentials_count/state`, credentialsCount.toString(), true);

      // Get connection status
      const connectionStatus = await this.getPppoeConnectionStatus();
      await this.publishState(`${this.featureName}/connection/state`, connectionStatus.status, true);
      
      if (connectionStatus.status === 'CONNECTED') {
        await this.publishState(`${this.featureName}/connection/attributes`, JSON.stringify({
          interface: connectionStatus.interface,
          ip: connectionStatus.ip,
          gateway: connectionStatus.gateway,
          uptime: connectionStatus.uptime
        }), true);
      }

    } catch (error: any) {
      logger.error(`Error checking for PPPoE credential files for ${this.featureName}:`, error.message);
      await this.publishState(`${this.featureName}/config_status/state`, "ERROR", true);
      await this.publishState(`${this.featureName}/connection/state`, "ERROR", true);
    }
  }

  protected async setup(): Promise<void> {
    // Subscribe to command topics for PPPoE management
    const commandTopic = this.prefixTopic(`${this.featureName}/command/#`);
    const inputTopic = this.prefixTopic(`${this.featureName}/*/set`);
    
    await this.mqttClient.subscribe(commandTopic);
    await this.mqttClient.subscribe(inputTopic);
    
    this.mqttClient.on('message', this.handleMqttMessage.bind(this));
  }

  private async handleMqttMessage(topic: string, payload: Buffer): Promise<void> {
    const message = payload.toString();

    // Handle input states
    if (topic === this.prefixTopic(`${this.featureName}/username_input/set`)) {
      await this.publishState(`${this.featureName}/username_input/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/password_input/set`)) {
      await this.publishState(`${this.featureName}/password_input/state`, message, true);
    }

    // Handle action commands
    else if (topic === this.prefixTopic(`${this.featureName}/command/save_credentials`) && message === 'SAVE_CREDENTIALS') {
      await this.handleSaveCredentialsAction();
    } else if (topic === this.prefixTopic(`${this.featureName}/command/test_connection`) && message === 'TEST_CONNECTION') {
      await this.handleTestConnectionAction();
    }
  }

  private async handleSaveCredentialsAction(): Promise<void> {
    try {
      await this.publishState(`${this.featureName}/last_action/state`, 'Saving credentials...', true);

      // Read current input values from MQTT states (topics derived from config,
      // not from any external value). Broker credentials come from the agent
      // config (env-overridable via MQTT_PASSWORD) — never hard-coded.
      const mqtt = this.agentConfig.mqtt;
      const mqttArgs = (topic: string) => {
        const args = ['-h', mqtt.host, '-p', String(mqtt.port)];
        if (mqtt.username) args.push('-u', mqtt.username);
        if (mqtt.password) args.push('-P', mqtt.password);
        args.push('-t', topic, '-C', '1', '-W', '1');
        return args;
      };
      const usernameResult = await execute_argv('mosquitto_sub', mqttArgs(this.prefixTopic(`${this.featureName}/username_input/state`)));
      const passwordResult = await execute_argv('mosquitto_sub', mqttArgs(this.prefixTopic(`${this.featureName}/password_input/state`)));

      const newUsername = usernameResult.stdout.trim();
      const newPassword = passwordResult.stdout.trim();

      if (!newUsername || !newPassword) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Username and password required', true);
        return;
      }

      // Credentials are written verbatim into an INI file line — reject values
      // containing control characters (newlines, etc.) that would corrupt it.
      if (!Validators.isConfigSafeLine(newUsername) || !Validators.isConfigSafeLine(newPassword)) {
        rejectInvalid('pppoe_credentials', newUsername, this.featureName);
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid characters in credentials', true);
        return;
      }

      const nmPath = this.featureConfig.nmconnection_path || DEFAULT_NMCONNECTION_PATH;

      // Read current config
      const readResult = await execute_argv('sudo', ['cat', nmPath]);
      if (readResult.exitCode !== 0) {
        throw new Error(`Failed to read NetworkManager config: ${nmPath}`);
      }

      const lines = readResult.stdout.split('\n');
      const updatedLines: string[] = [];
      let inPppoeSection = false;

      for (const line of lines) {
        const trimmed = line.trim();

        if (trimmed === '[pppoe]') {
          inPppoeSection = true;
          updatedLines.push(line);
          continue;
        }

        if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
          inPppoeSection = false;
          updatedLines.push(line);
          continue;
        }

        if (inPppoeSection) {
          if (trimmed.startsWith('username=')) {
            updatedLines.push(`username=${newUsername}`);
          } else if (trimmed.startsWith('password=')) {
            updatedLines.push(`password=${newPassword}`);
          } else {
            updatedLines.push(line);
          }
        } else {
          updatedLines.push(line);
        }
      }

      // Create backup
      await execute_argv('sudo', ['cp', nmPath, `${nmPath}.backup`]);

      // Write to temp file via fs (no shell → no injection), then move into place
      const tempFile = `/tmp/pppoe_nm_${Date.now()}.nmconnection`;
      const content = updatedLines.join('\n');
      fs.writeFileSync(tempFile, content, { mode: 0o600 });
      await execute_argv('sudo', ['mv', tempFile, nmPath]);
      await execute_argv('sudo', ['chmod', '600', nmPath]);

      // Reload NetworkManager connection to apply changes
      await execute_argv('sudo', ['nmcli', 'connection', 'reload']);
      await execute_argv('sudo', ['nmcli', 'connection', 'down', 'pppoe-enp6s0.835']);
      await execute_argv('sudo', ['nmcli', 'connection', 'up', 'pppoe-enp6s0.835']);

      await this.publishState(`${this.featureName}/last_action/state`, 'Credentials saved and connection restarted', true);
      logger.info(`PPPoE credentials updated in ${nmPath}`);

    } catch (error: any) {
      const errorMessage = error.message || 'Unknown error during credential save';
      logger.error(`Error saving PPPoE credentials for ${this.featureName}:`, errorMessage);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${errorMessage}`, true);
    }
  }

  private async handleTestConnectionAction(): Promise<void> {
    try {
      await this.publishState(`${this.featureName}/last_action/state`, 'Testing connection...', true);
      
      // Test PPPoE connection
      const result = await execute_argv('sudo', ['pppd', 'call', 'provider']);
      
      if (result.exitCode === 0) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Connection test successful', true);
      } else {
        await this.publishState(`${this.featureName}/last_action/state`, 'Connection test failed', true);
      }
      
    } catch (error: any) {
      const errorMessage = error.message || 'Unknown error during connection test';
      logger.error(`Error testing PPPoE connection for ${this.featureName}:`, errorMessage);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${errorMessage}`, true);
    }
  }
}
