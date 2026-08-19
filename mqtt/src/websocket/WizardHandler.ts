// src/websocket/WizardHandler.ts

import { WizardCommand, FeatureName, FeatureHandlers, ActionHandler } from './types';
import { execute_command, execute_argv } from '../utils/exec';
import { Validators, rejectInvalid } from '../utils/validators';
import logger from '../utils/logger';
import { getConfigManager } from '../config';
import * as fs from 'fs';

// hostapd config files the wizard is allowed to touch. Any config path received
// from a wizard command must be one of these (prevents arbitrary file writes).
const ALLOWED_HOSTAPD_CONFIGS = ['/etc/hostapd/2.4Ghz.conf', '/etc/hostapd/5Ghz.conf'];

/**
 * Handler for wizard commands
 * Routes commands to the appropriate feature handlers
 */
export class WizardHandler {
  private readonly handlers: Map<FeatureName, FeatureHandlers> = new Map();

  constructor() {
    this.registerHandlers();
  }

  /**
   * Handle an incoming wizard command
   */
  public async handleCommand(command: WizardCommand): Promise<unknown> {
    const featureHandlers = this.handlers.get(command.feature);
    if (!featureHandlers) {
      throw new Error(`Unknown feature: ${command.feature}`);
    }

    const handler = featureHandlers[command.action];
    if (!handler) {
      throw new Error(`Unknown action: ${command.action} for feature ${command.feature}`);
    }

    return handler(command.params || {});
  }

  /**
   * Register all feature handlers
   */
  private registerHandlers(): void {
    this.handlers.set('network', this.createNetworkHandlers());
    this.handlers.set('firewall', this.createFirewallHandlers());
    this.handlers.set('wifi', this.createWifiHandlers());
    this.handlers.set('pppoe', this.createPppoeHandlers());
    this.handlers.set('vm', this.createVmHandlers());
    this.handlers.set('system', this.createSystemHandlers());
    this.handlers.set('wizard', this.createWizardHandlers());
  }

  // ============================================
  // NETWORK HANDLERS
  // ============================================
  private createNetworkHandlers(): FeatureHandlers {
    return {
      get_interfaces: async () => {
        const linkResult = await execute_argv('ip', ['-j', 'link', 'show']);
        if (linkResult.exitCode !== 0) throw new Error('Failed to get interfaces');

        const addrResult = await execute_argv('ip', ['-j', 'addr', 'show']);
        const addresses = addrResult.exitCode === 0 ? JSON.parse(addrResult.stdout) : [];

        // Get firewall zones for interfaces
        const zonesResult = await execute_argv('firewall-cmd', ['--get-active-zones']);
        const zoneMap = new Map<string, string>();
        if (zonesResult.exitCode === 0) {
          let currentZone = '';
          for (const line of zonesResult.stdout.split('\n')) {
            if (!line.startsWith(' ') && !line.startsWith('\t') && line.trim()) {
              currentZone = line.replace(':', '').trim();
            } else if (line.includes('interfaces:')) {
              const ifaces = line.replace('interfaces:', '').trim().split(/\s+/);
              for (const iface of ifaces) {
                if (iface) zoneMap.set(iface, currentZone);
              }
            }
          }
        }

        // Get network stats
        const statsResult = await execute_argv('cat', ['/proc/net/dev']);
        const statsMap = new Map<string, { rx_bytes: number; tx_bytes: number }>();
        if (statsResult.exitCode === 0) {
          for (const line of statsResult.stdout.split('\n').slice(2)) {
            const parts = line.trim().split(/\s+/);
            if (parts.length >= 10) {
              const ifaceName = parts[0].replace(':', '');
              statsMap.set(ifaceName, {
                rx_bytes: parseInt(parts[1]) || 0,
                tx_bytes: parseInt(parts[9]) || 0,
              });
            }
          }
        }

        const linkInterfaces = JSON.parse(linkResult.stdout);
        return linkInterfaces
          .filter((iface: any) => !['lo'].includes(iface.ifname) && !iface.ifname.startsWith('veth') && !iface.ifname.startsWith('vnet'))
          .map((iface: any) => {
            const addrInfo = addresses.find((a: any) => a.ifname === iface.ifname);
            const ipv4 = addrInfo?.addr_info?.find((a: any) => a.family === 'inet');
            const stats = statsMap.get(iface.ifname);

            // Determine interface type
            let type = 'ethernet';
            if (iface.ifname.includes('Bridge') || iface.ifname.startsWith('br')) type = 'bridge';
            else if (iface.ifname.startsWith('wl')) type = 'wifi';
            else if (iface.ifname.startsWith('ppp')) type = 'ppp';
            else if (iface.ifname.includes('.')) type = 'vlan';
            else if (iface.ifname.startsWith('docker') || iface.ifname.startsWith('veth')) type = 'virtual';

            return {
              name: iface.ifname,
              type,
              state: iface.operstate?.toLowerCase() || 'unknown',
              mac_address: iface.address,
              ip_address: ipv4?.local,
              mtu: iface.mtu,
              rx_bytes: stats?.rx_bytes || 0,
              tx_bytes: stats?.tx_bytes || 0,
              zone: zoneMap.get(iface.ifname),
            };
          });
      },

      get_zones: async () => {
        const result = await execute_argv('firewall-cmd', ['--get-zones']);
        if (result.exitCode !== 0) throw new Error('Failed to get zones');
        return result.stdout.trim().split(/\s+/);
      },

      set_interface_zone: async (params) => {
        const { interface: iface, zone, currentZone } = params as { interface: string; zone: string; currentZone?: string };
        if (!iface || !zone) throw new Error('Missing interface or zone parameter');
        // Validate wizard-controlled input before it reaches firewall-cmd.
        if (!Validators.isInterface(iface)) throw new Error('Invalid interface parameter');
        if (!Validators.isZone(zone)) throw new Error('Invalid zone parameter');
        if (currentZone && !Validators.isZone(currentZone)) throw new Error('Invalid currentZone parameter');

        // Remove from current zone if specified
        if (currentZone) {
          await execute_argv('firewall-cmd', [`--zone=${currentZone}`, `--remove-interface=${iface}`, '--permanent']);
        }

        // Add to new zone
        const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, `--add-interface=${iface}`, '--permanent']);
        if (result.exitCode !== 0) throw new Error(`Failed to add interface to zone: ${result.stderr}`);

        // Reload firewall
        await execute_argv('firewall-cmd', ['--reload']);

        return { success: true, interface: iface, zone };
      },

      get_status: async () => {
        const activeZones = await execute_argv('firewall-cmd', ['--get-active-zones']);
        return { activeZones: activeZones.stdout };
      },
    };
  }

  // ============================================
  // FIREWALL HANDLERS
  // ============================================
  private createFirewallHandlers(): FeatureHandlers {
    return {
      list_zones: async () => {
        const result = await execute_argv('firewall-cmd', ['--get-zones']);
        if (result.exitCode !== 0) throw new Error('Failed to list zones');
        return result.stdout.trim().split(/\s+/);
      },

      get_zone_details: async (params) => {
        const { zone } = params as { zone: string };
        if (!zone) throw new Error('Missing zone parameter');
        if (!Validators.isZone(zone)) throw new Error('Invalid zone parameter');

        const [forwards, services, ports, masquerade] = await Promise.all([
          this.getZonePortForwards(zone),
          this.getZoneServices(zone),
          this.getZonePorts(zone),
          this.getZoneMasquerade(zone),
        ]);

        return { zone, forwards, services, ports, masquerade };
      },

      get_rules: async () => {
        const activeZonesOutput = await execute_argv('firewall-cmd', ['--get-active-zones']);
        const zoneInterfaceMap = this.parseActiveZones(activeZonesOutput.stdout);

        const defaultZoneResult = await execute_argv('firewall-cmd', ['--get-default-zone']);
        const defaultZone = defaultZoneResult.stdout.trim();

        const zones: Array<{
          name: string;
          target: string;
          interfaces: string[];
          services: string[];
          ports: string[];
          masquerade: boolean;
          forward_ports: Array<{ port: string; protocol: string; to_port: string; to_addr: string }>;
        }> = [];

        for (const [zoneName, interfaces] of zoneInterfaceMap) {
          const [forwards, services, ports, masquerade, target] = await Promise.all([
            this.getZonePortForwards(zoneName),
            this.getZoneServices(zoneName),
            this.getZonePorts(zoneName),
            this.getZoneMasquerade(zoneName),
            this.getZoneTarget(zoneName),
          ]);

          zones.push({
            name: zoneName,
            target,
            interfaces,
            services,
            ports,
            masquerade,
            forward_ports: forwards.map(f => ({
              port: f.port,
              protocol: f.proto,
              to_port: f.toport,
              to_addr: f.toaddr,
            })),
          });
        }

        return { zones, default_zone: defaultZone };
      },

      add_port: async (params) => {
        const { port, protocol = 'tcp', zone = 'public' } = params as { port: string; protocol?: string; zone?: string };
        if (!port) throw new Error('Missing port parameter');
        this.assertPortInputs(zone, port, protocol);

        await execute_argv('firewall-cmd', [`--zone=${zone}`, `--add-port=${port}/${protocol}`, '--permanent']);
        await execute_argv('firewall-cmd', ['--reload']);
        return { success: true, port, protocol, zone };
      },

      remove_port: async (params) => {
        const { port, protocol = 'tcp', zone = 'public' } = params as { port: string; protocol?: string; zone?: string };
        if (!port) throw new Error('Missing port parameter');
        this.assertPortInputs(zone, port, protocol);

        await execute_argv('firewall-cmd', [`--zone=${zone}`, `--remove-port=${port}/${protocol}`, '--permanent']);
        await execute_argv('firewall-cmd', ['--reload']);
        return { success: true, port, protocol, zone };
      },

      add_forward: async (params) => {
        const { port, toAddr, toPort, protocol = 'tcp', zone = 'public' } = params as {
          port: string; toAddr: string; toPort: string; protocol?: string; zone?: string;
        };
        if (!port || !toAddr || !toPort) throw new Error('Missing port, toAddr or toPort parameter');
        this.assertForwardInputs(zone, port, protocol, toPort, toAddr);

        // With argv the rule is one literal argument — no surrounding quotes.
        const rule = `port=${port}:proto=${protocol}:toport=${toPort}:toaddr=${toAddr}`;
        await execute_argv('firewall-cmd', [`--zone=${zone}`, `--add-forward-port=${rule}`, '--permanent']);
        await execute_argv('firewall-cmd', ['--reload']);
        return { success: true, port, toAddr, toPort, protocol, zone };
      },

      remove_forward: async (params) => {
        const { port, toAddr, toPort, protocol = 'tcp', zone = 'public' } = params as {
          port: string; toAddr: string; toPort: string; protocol?: string; zone?: string;
        };
        if (!port || !toAddr || !toPort) throw new Error('Missing port, toAddr or toPort parameter');
        this.assertForwardInputs(zone, port, protocol, toPort, toAddr);

        const rule = `port=${port}:proto=${protocol}:toport=${toPort}:toaddr=${toAddr}`;
        await execute_argv('firewall-cmd', [`--zone=${zone}`, `--remove-forward-port=${rule}`, '--permanent']);
        await execute_argv('firewall-cmd', ['--reload']);
        return { success: true, port, toAddr, toPort, protocol, zone };
      },
    };
  }

  // ============================================
  // WIFI HANDLERS
  // ============================================
  private createWifiHandlers(): FeatureHandlers {
    const configPaths = ['/etc/hostapd/2.4Ghz.conf', '/etc/hostapd/5Ghz.conf'];

    return {
      get_networks: async () => {
        const networks: Array<{
          ssid: string;
          password?: string;
          security: string;
          band: string;
          channel?: number;
          enabled: boolean;
          configPath: string;
        }> = [];

        // Check if hostapd is running (argv: exit 0 + "active" means running)
        const hostapdStatus = await execute_argv('systemctl', ['is-active', 'hostapd']);
        const hostapdActive = hostapdStatus.stdout.trim() === 'active';

        for (const configPath of configPaths) {
          try {
            const result = await execute_argv('cat', [configPath]);
            if (result.exitCode !== 0) continue;

            const content = result.stdout;
            const ssid = this.extractHostapdValue(content, 'ssid');
            const wpa = this.extractHostapdValue(content, 'wpa');
            const wpaKeyMgmt = this.extractHostapdValue(content, 'wpa_key_mgmt');
            const channel = this.extractHostapdValue(content, 'channel');
            const password = this.extractHostapdValue(content, 'wpa_passphrase');

            let security = 'Open';
            if (wpa === '2' && wpaKeyMgmt?.includes('WPA-PSK')) security = 'WPA2-PSK';
            if (wpa === '2' && wpaKeyMgmt?.includes('SAE')) security = 'WPA3-SAE';
            if (wpa === '3' || (wpaKeyMgmt?.includes('WPA-PSK') && wpaKeyMgmt?.includes('SAE'))) security = 'WPA2/WPA3-Mixed';

            const band = configPath.includes('2.4') ? '2.4GHz' : '5GHz';

            if (ssid) {
              networks.push({
                ssid,
                password: password || undefined,
                security,
                band,
                channel: channel ? parseInt(channel) : undefined,
                enabled: hostapdActive && !!ssid,
                configPath,
              });
            }
          } catch (error) {
            logger.error(`Error reading hostapd config ${configPath}:`, error);
          }
        }

        return networks;
      },

      add_network: async (params) => {
        const { ssid, password, security = 'WPA2-PSK', band = '2.4GHz' } = params as {
          ssid: string; password: string; security?: string; band?: string;
        };
        if (!ssid || !password) throw new Error('Missing ssid or password parameter');
        // SSID/passphrase are written verbatim into the config file — reject
        // control characters that would corrupt it (or inject extra directives).
        if (!Validators.isConfigSafeLine(ssid) || !Validators.isConfigSafeLine(password)) {
          throw new Error('Invalid characters in ssid or password');
        }

        const configPath = band === '5GHz' ? '/etc/hostapd/5Ghz.conf' : '/etc/hostapd/2.4Ghz.conf';

        // Read existing config
        const existing = await execute_argv('cat', [configPath]);
        let content = existing.stdout;

        // Update SSID and password
        content = this.updateHostapdValue(content, 'ssid', ssid);
        content = this.updateHostapdValue(content, 'wpa_passphrase', password);

        // Update security settings
        const securityConfig = this.getSecurityConfig(security);
        content = this.updateHostapdValue(content, 'wpa', securityConfig.wpa);
        content = this.updateHostapdValue(content, 'wpa_key_mgmt', securityConfig.wpa_key_mgmt);
        if (securityConfig.rsn_pairwise) {
          content = this.updateHostapdValue(content, 'rsn_pairwise', securityConfig.rsn_pairwise);
        }

        // Write config and reload
        await this.writeRootFile(configPath, content);
        await execute_argv('sudo', ['systemctl', 'reload', 'hostapd']);

        return { success: true, ssid, band };
      },

      delete_network: async (params) => {
        // For simplicity, we just clear the SSID - in reality you might want to disable the interface
        const { configPath } = params as { configPath: string };
        if (!configPath) throw new Error('Missing configPath parameter');
        // configPath is wizard-controlled — restrict it to the known hostapd files.
        if (!ALLOWED_HOSTAPD_CONFIGS.includes(configPath)) throw new Error('Invalid configPath parameter');

        // Set SSID to empty or disable
        const existing = await execute_argv('cat', [configPath]);
        const content = this.updateHostapdValue(existing.stdout, 'ssid', '');

        await this.writeRootFile(configPath, content);
        await execute_argv('sudo', ['systemctl', 'reload', 'hostapd']);

        return { success: true };
      },

      reload: async () => {
        await execute_argv('sudo', ['systemctl', 'reload', 'hostapd']);
        return { success: true };
      },
    };
  }

  // ============================================
  // PPPOE HANDLERS
  // ============================================
  private createPppoeHandlers(): FeatureHandlers {
    const nmconnectionPath = '/etc/NetworkManager/system-connections/pppoe-enp6s0.835.nmconnection';

    return {
      get_credentials: async () => {
        try {
          const result = await execute_argv('sudo', ['cat', nmconnectionPath]);
          if (result.exitCode !== 0) return { found: false };

          const content = result.stdout;
          const username = this.extractIniValue(content, 'pppoe', 'username');
          const password = this.extractIniValue(content, 'pppoe', 'password');

          return { found: true, username, password: password ? '***' : null };
        } catch {
          return { found: false };
        }
      },

      set_credentials: async (params) => {
        const { username, password } = params as { username: string; password: string };
        if (!username || !password) throw new Error('Missing username or password');
        // Written verbatim into an INI line — reject control characters.
        if (!Validators.isConfigSafeLine(username) || !Validators.isConfigSafeLine(password)) {
          throw new Error('Invalid characters in username or password');
        }

        // Read current config
        const result = await execute_argv('sudo', ['cat', nmconnectionPath]);
        let content = result.stdout;

        // Update credentials
        content = this.updateIniValue(content, 'pppoe', 'username', username);
        content = this.updateIniValue(content, 'pppoe', 'password', password);

        // Backup and write (via fs → no shell, no injection), then lock down perms
        await this.writeRootFile(nmconnectionPath, content, 0o600);
        await execute_argv('sudo', ['chmod', '600', nmconnectionPath]);

        return { success: true };
      },

      restart: async () => {
        await execute_argv('sudo', ['nmcli', 'connection', 'reload']);
        await execute_argv('sudo', ['nmcli', 'connection', 'down', 'pppoe-enp6s0.835']); // may fail if already down
        await execute_argv('sudo', ['nmcli', 'connection', 'up', 'pppoe-enp6s0.835']);
        return { success: true };
      },

      test_connection: async () => {
        const result = await execute_argv('ip', ['addr', 'show', 'ppp0']);
        const isUp = result.exitCode === 0 && result.stdout.includes('state UP');
        return { connected: isUp };
      },
    };
  }

  // ============================================
  // VM HANDLERS
  // ============================================
  private createVmHandlers(): FeatureHandlers {
    return {
      list_vms: async () => {
        const result = await execute_argv('virsh', ['list', '--all']);
        if (result.exitCode !== 0) throw new Error('Failed to list VMs');

        const lines = result.stdout.split('\n').slice(2); // Skip header
        const vms: Array<{
          name: string;
          state: string;
          cpu_count?: number;
          memory_mb?: number;
          disk_gb?: number;
          os_type?: string;
          autostart?: boolean;
        }> = [];

        for (const line of lines) {
          const parts = line.trim().split(/\s+/);
          if (parts.length >= 2) {
            const name = parts[1];
            const state = parts.slice(2).join(' ') || 'unknown';
            if (!name) continue;
            // name comes from virsh output; validate before it is passed back to virsh.
            if (!Validators.isVmName(name)) continue;

            // Get VM details via argv, parsing the previously piped grep/awk in JS.
            const [vcpuResult, dominfoResult, xmlResult] = await Promise.all([
              execute_argv('virsh', ['vcpucount', name, '--current']),
              execute_argv('virsh', ['dominfo', name]),
              execute_argv('virsh', ['dumpxml', name]),
            ]);

            const cpuCount = parseInt(vcpuResult.stdout.trim()) || 0;
            const memMatch = dominfoResult.stdout.match(/^Max memory:\s*(\d+)/m);
            const memKb = memMatch ? parseInt(memMatch[1]) : 0;
            const autostartMatch = dominfoResult.stdout.match(/^Autostart:\s*(\S+)/m);
            const autostart = (autostartMatch?.[1] || '').toLowerCase() === 'enable';

            // Try to get OS type from domain XML
            const osMatch = xmlResult.stdout.match(/<os_type>([^<]+)<\/os_type>/);
            const osType = osMatch ? osMatch[1].trim() : undefined;

            vms.push({
              name,
              state,
              cpu_count: cpuCount > 0 ? cpuCount : undefined,
              memory_mb: memKb > 0 ? Math.round(memKb / 1024) : undefined,
              os_type: osType,
              autostart,
            });
          }
        }

        return vms;
      },

      get_state: async (params) => {
        const { vmName } = params as { vmName: string };
        if (!vmName) throw new Error('Missing vmName parameter');
        if (!Validators.isVmName(vmName)) throw new Error('Invalid vmName parameter');

        const result = await execute_argv('virsh', ['domstate', vmName]);
        return { vmName, state: result.stdout.trim() };
      },

      start_vm: async (params) => {
        const { vmName } = params as { vmName: string };
        if (!vmName) throw new Error('Missing vmName parameter');
        if (!Validators.isVmName(vmName)) throw new Error('Invalid vmName parameter');

        await execute_argv('virsh', ['start', vmName]);
        return { success: true, vmName, action: 'start' };
      },

      stop_vm: async (params) => {
        const { vmName } = params as { vmName: string };
        if (!vmName) throw new Error('Missing vmName parameter');
        if (!Validators.isVmName(vmName)) throw new Error('Invalid vmName parameter');

        await execute_argv('virsh', ['shutdown', vmName]);
        return { success: true, vmName, action: 'stop' };
      },

      restart_vm: async (params) => {
        const { vmName } = params as { vmName: string };
        if (!vmName) throw new Error('Missing vmName parameter');
        if (!Validators.isVmName(vmName)) throw new Error('Invalid vmName parameter');

        await execute_argv('virsh', ['reboot', vmName]);
        return { success: true, vmName, action: 'restart' };
      },

      force_stop_vm: async (params) => {
        const { vmName } = params as { vmName: string };
        if (!vmName) throw new Error('Missing vmName parameter');
        if (!Validators.isVmName(vmName)) throw new Error('Invalid vmName parameter');

        await execute_argv('virsh', ['destroy', vmName]);
        return { success: true, vmName, action: 'force_stop' };
      },

      pause_vm: async (params) => {
        const { vmName } = params as { vmName: string };
        if (!vmName) throw new Error('Missing vmName parameter');
        if (!Validators.isVmName(vmName)) throw new Error('Invalid vmName parameter');

        await execute_argv('virsh', ['suspend', vmName]);
        return { success: true, vmName, action: 'pause' };
      },

      resume_vm: async (params) => {
        const { vmName } = params as { vmName: string };
        if (!vmName) throw new Error('Missing vmName parameter');
        if (!Validators.isVmName(vmName)) throw new Error('Invalid vmName parameter');

        await execute_argv('virsh', ['resume', vmName]);
        return { success: true, vmName, action: 'resume' };
      },
    };
  }

  // ============================================
  // SYSTEM HANDLERS
  // ============================================
  private createSystemHandlers(): FeatureHandlers {
    // NOTE: every command in this group is a fixed literal with NO wizard / MQTT
    // input. Where a shell is still used (execute_command) it is only for pipes,
    // redirections or `||` fallbacks — never with an interpolated external value.
    return {
      get_info: async () => {
        const config = getConfigManager().config;
        const [hostname, uptime, kernel, osRelease, cpuInfo, memInfo] = await Promise.all([
          execute_argv('hostname', []),
          execute_argv('uptime', ['-p']),
          execute_argv('uname', ['-r']),
          execute_command('cat /etc/os-release 2>/dev/null || echo "ID=linux"', false),
          execute_argv('lscpu', []),
          execute_argv('free', ['-b']),
        ]);

        // Parse OS name
        const osMatch = osRelease.stdout.match(/PRETTY_NAME="?([^"\n]+)"?/);
        const os = osMatch ? osMatch[1] : 'Linux';

        // Parse CPU info
        const cpuModelMatch = cpuInfo.stdout.match(/Model name:\s*(.+)/);
        const cpuCoresMatch = cpuInfo.stdout.match(/CPU\(s\):\s*(\d+)/);

        // Parse memory
        const memLines = memInfo.stdout.split('\n');
        const memMatch = memLines[1]?.match(/\d+/g);
        const memTotal = memMatch ? parseInt(memMatch[0]) : 0;

        // Parse disk (pipe chain → shell; fixed literal, no external input)
        const diskResult = await execute_command("df -B1 / | tail -1 | awk '{print $2}'", false);
        const diskTotal = parseInt(diskResult.stdout.trim()) || 0;

        return {
          hostname: hostname.stdout.trim(),
          os,
          kernel: kernel.stdout.trim(),
          uptime: uptime.stdout.trim().replace('up ', ''),
          cpu_model: cpuModelMatch ? cpuModelMatch[1].trim() : 'Unknown',
          cpu_cores: cpuCoresMatch ? parseInt(cpuCoresMatch[1]) : 0,
          memory_total: this.formatBytes(memTotal),
          disk_total: this.formatBytes(diskTotal),
        };
      },

      get_status: async () => {
        const [cpuStat1, memInfo, diskInfo, loadAvg, temps] = await Promise.all([
          execute_argv('head', ['-1', '/proc/stat']),
          execute_argv('free', ['-b']),
          execute_command("df -B1 / | tail -1", false), // pipe → shell; fixed literal
          execute_argv('cat', ['/proc/loadavg']),
          execute_command("sensors -j 2>/dev/null || echo '{}'", false), // || → shell; fixed literal
        ]);

        // Wait 100ms and get second CPU reading for accurate usage
        await new Promise(resolve => setTimeout(resolve, 100));
        const cpuStat2 = await execute_argv('head', ['-1', '/proc/stat']);

        // Calculate CPU usage
        const parseCpuStat = (line: string) => {
          const parts = line.split(/\s+/).slice(1).map(Number);
          const idle = parts[3] + (parts[4] || 0);
          const total = parts.reduce((a, b) => a + b, 0);
          return { idle, total };
        };
        const stat1 = parseCpuStat(cpuStat1.stdout);
        const stat2 = parseCpuStat(cpuStat2.stdout);
        const idleDiff = stat2.idle - stat1.idle;
        const totalDiff = stat2.total - stat1.total;
        const cpuUsage = totalDiff > 0 ? ((totalDiff - idleDiff) / totalDiff) * 100 : 0;

        // Parse memory
        const memLines = memInfo.stdout.split('\n');
        const memMatch = memLines[1]?.match(/\d+/g);
        const memTotal = memMatch ? parseInt(memMatch[0]) : 0;
        const memUsed = memMatch ? parseInt(memMatch[1]) : 0;

        // Parse disk
        const diskParts = diskInfo.stdout.trim().split(/\s+/);
        const diskTotal = parseInt(diskParts[1]) || 0;
        const diskUsed = parseInt(diskParts[2]) || 0;

        // Parse load average
        const loadParts = loadAvg.stdout.trim().split(/\s+/);
        const loadAverage = loadParts.slice(0, 3).map(parseFloat);

        // Parse temperatures
        const temperatures: Record<string, number> = {};
        try {
          const sensorsData = JSON.parse(temps.stdout);
          for (const [adapter, data] of Object.entries(sensorsData)) {
            if (typeof data === 'object' && data !== null) {
              for (const [sensor, values] of Object.entries(data as Record<string, any>)) {
                if (typeof values === 'object' && values !== null) {
                  for (const [key, temp] of Object.entries(values)) {
                    if (key.includes('input') && typeof temp === 'number') {
                      const name = sensor.replace(/_/g, ' ').replace('temp', 'Core');
                      temperatures[name] = Math.round(temp);
                    }
                  }
                }
              }
            }
          }
        } catch { /* Ignore parse errors */ }

        return {
          cpu_usage: cpuUsage,
          memory_used: memUsed,
          memory_total: memTotal,
          disk_used: diskUsed,
          disk_total: diskTotal,
          load_average: loadAverage,
          temperatures,
        };
      },

      check_updates: async () => {
        await execute_argv('sudo', ['apt-get', 'update']);
        // pipe + redirect → shell; fixed literal, no external input
        const result = await execute_command('apt list --upgradable 2>/dev/null | grep -v "^Listing"', false);
        const updates = result.stdout.trim().split('\n').filter(line => line.length > 0);
        return { count: updates.length, packages: updates };
      },

      apply_updates: async () => {
        await execute_argv('sudo', ['apt-get', 'upgrade', '-y']);
        return { success: true };
      },
    };
  }

  // ============================================
  // WIZARD STATE HANDLERS
  // ============================================
  private createWizardHandlers(): FeatureHandlers {
    // Simple in-memory wizard state
    let wizardState = {
      currentStep: 0,
      steps: ['network', 'firewall', 'wifi', 'pppoe', 'vm', 'summary'],
      completed: false,
      completedSteps: [] as string[],
    };

    return {
      get_state: async () => wizardState,

      set_step: async (params) => {
        const { step } = params as { step: number | string };
        if (typeof step === 'number') {
          wizardState.currentStep = step;
        } else {
          const index = wizardState.steps.indexOf(step);
          if (index !== -1) wizardState.currentStep = index;
        }
        return wizardState;
      },

      complete: async () => {
        wizardState.completed = true;
        return wizardState;
      },

      reset: async () => {
        wizardState = {
          currentStep: 0,
          steps: ['network', 'firewall', 'wifi', 'pppoe', 'vm', 'summary'],
          completed: false,
          completedSteps: [],
        };
        return wizardState;
      },
    };
  }

  // ============================================
  // HELPER METHODS
  // ============================================

  // ---- input validation helpers (throw on invalid wizard input) ----

  private assertPortInputs(zone: string, port: string, protocol: string): void {
    if (!Validators.isZone(zone)) { rejectInvalid('zone', zone, 'wizard'); throw new Error('Invalid zone parameter'); }
    if (!Validators.isPort(port)) { rejectInvalid('port', port, 'wizard'); throw new Error('Invalid port parameter'); }
    if (!Validators.isProtocol(protocol)) { rejectInvalid('protocol', protocol, 'wizard'); throw new Error('Invalid protocol parameter'); }
  }

  private assertForwardInputs(zone: string, port: string, protocol: string, toPort: string, toAddr: string): void {
    this.assertPortInputs(zone, port, protocol);
    if (!Validators.isPort(toPort)) { rejectInvalid('toPort', toPort, 'wizard'); throw new Error('Invalid toPort parameter'); }
    if (!Validators.isIPv4(toAddr)) { rejectInvalid('toAddr', toAddr, 'wizard'); throw new Error('Invalid toAddr parameter'); }
  }

  // Atomically replace a root-owned file: write a temp file via fs (no shell),
  // back up the original, then move the temp into place with sudo.
  private async writeRootFile(targetPath: string, content: string, mode: number = 0o644): Promise<void> {
    const tempFile = `/tmp/nivuus_wizard_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    fs.writeFileSync(tempFile, content, { mode });
    await execute_argv('sudo', ['cp', targetPath, `${targetPath}.backup`]);
    await execute_argv('sudo', ['mv', tempFile, targetPath]);
  }

  private async getZonePortForwards(zone: string): Promise<Array<{ port: string; proto: string; toport: string; toaddr: string }>> {
    const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--list-forward-ports']);
    if (result.exitCode !== 0) return [];

    const forwards: Array<{ port: string; proto: string; toport: string; toaddr: string }> = [];
    const lines = result.stdout.trim().split('\n');

    for (const line of lines) {
      if (!line.trim()) continue;
      const match = line.match(/port=(\d+):proto=(tcp|udp):toport=(\d+):toaddr=([\d.]+)/);
      if (match) {
        forwards.push({ port: match[1], proto: match[2], toport: match[3], toaddr: match[4] });
      }
    }
    return forwards;
  }

  private async getZoneServices(zone: string): Promise<string[]> {
    const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--list-services']);
    if (result.exitCode !== 0) return [];
    return result.stdout.trim().split(/\s+/).filter(s => s.length > 0);
  }

  private async getZonePorts(zone: string): Promise<string[]> {
    const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--list-ports']);
    if (result.exitCode !== 0) return [];
    return result.stdout.trim().split(/\s+/).filter(p => p.length > 0);
  }

  private async getZoneMasquerade(zone: string): Promise<boolean> {
    const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--query-masquerade']);
    return result.exitCode === 0;
  }

  private async getZoneTarget(zone: string): Promise<string> {
    const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--get-target']);
    return result.exitCode === 0 ? result.stdout.trim() : 'default';
  }

  private parseActiveZones(output: string): Map<string, string[]> {
    const zoneInterfaceMap = new Map<string, string[]>();
    if (!output) return zoneInterfaceMap;

    const lines = output.split('\n');
    let currentZone: string | null = null;

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (!trimmedLine) continue;

      if (line.match(/^\S/) && !trimmedLine.startsWith('interfaces:') && !trimmedLine.startsWith('sources:')) {
        currentZone = trimmedLine.split(':')[0].trim();
        if (!zoneInterfaceMap.has(currentZone)) {
          zoneInterfaceMap.set(currentZone, []);
        }
      } else if (currentZone && trimmedLine.startsWith('interfaces:')) {
        const interfaces = trimmedLine.substring('interfaces:'.length).trim().split(/\s+/).filter(i => i.length > 0);
        const existing = zoneInterfaceMap.get(currentZone) || [];
        zoneInterfaceMap.set(currentZone, [...existing, ...interfaces]);
      }
    }

    return zoneInterfaceMap;
  }

  private extractHostapdValue(content: string, key: string): string | null {
    const match = content.match(new RegExp(`^${key}=(.*)$`, 'm'));
    return match ? match[1].trim() : null;
  }

  private updateHostapdValue(content: string, key: string, value: string): string {
    const regex = new RegExp(`^${key}=.*$`, 'm');
    if (content.match(regex)) {
      return content.replace(regex, `${key}=${value}`);
    }
    return content + `\n${key}=${value}`;
  }

  private getSecurityConfig(security: string): { wpa: string; wpa_key_mgmt: string; rsn_pairwise?: string } {
    switch (security) {
      case 'WPA2-PSK':
        return { wpa: '2', wpa_key_mgmt: 'WPA-PSK', rsn_pairwise: 'CCMP' };
      case 'WPA3-SAE':
        return { wpa: '2', wpa_key_mgmt: 'SAE', rsn_pairwise: 'CCMP' };
      case 'WPA2/WPA3-Mixed':
        return { wpa: '2', wpa_key_mgmt: 'WPA-PSK SAE', rsn_pairwise: 'CCMP' };
      case 'Open':
      default:
        return { wpa: '0', wpa_key_mgmt: '' };
    }
  }

  private extractIniValue(content: string, section: string, key: string): string | null {
    const sectionRegex = new RegExp(`\\[${section}\\]([\\s\\S]*?)(?=\\[|$)`, 'i');
    const sectionMatch = content.match(sectionRegex);
    if (!sectionMatch) return null;

    const keyRegex = new RegExp(`^${key}=(.*)$`, 'm');
    const keyMatch = sectionMatch[1].match(keyRegex);
    return keyMatch ? keyMatch[1].trim() : null;
  }

  private updateIniValue(content: string, section: string, key: string, value: string): string {
    const sectionRegex = new RegExp(`(\\[${section}\\][\\s\\S]*?)^${key}=.*$`, 'm');
    if (content.match(sectionRegex)) {
      return content.replace(sectionRegex, `$1${key}=${value}`);
    }
    // Key not found, add it to section
    const sectionHeaderRegex = new RegExp(`(\\[${section}\\])`, 'i');
    return content.replace(sectionHeaderRegex, `$1\n${key}=${value}`);
  }

  private formatBytes(bytes: number): string {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let unitIndex = 0;
    let value = bytes;

    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex++;
    }

    return `${value.toFixed(1)} ${units[unitIndex]}`;
  }
}
