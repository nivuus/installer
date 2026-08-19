// src/features/network/ConnectedDevices.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_command } from '../../utils/exec';
import { Validators } from '../../utils/validators';
import { MacVendorLookup } from '../../utils/macVendor';
import logger from '../../utils/logger';

interface ConnectedDevicesFeatureConfig extends FeatureConfig {
  bridge_interfaces?: string[];
  dhcp_lease_paths?: string[];
}

interface ConnectedDevice {
  ip: string;
  mac: string;
  hostname?: string;
  bridge: string;
  lease_time?: string;
  vendor?: string;
  connection_type: 'arp' | 'dhcp' | 'both';
}

interface BridgeInfo {
  name: string;
  ip?: string;
  subnet?: string;
  cidr?: string;
}

export class ConnectedDevices extends BaseFeature {
  protected featureConfig: ConnectedDevicesFeatureConfig;
  private hostnameCache: Map<string, { hostname: string | undefined, timestamp: number }> = new Map();
  private readonly HOSTNAME_CACHE_TTL = 3600000; // 1 hour in milliseconds

  constructor(mqttClient: MqttClient, featureName: string = 'connected_devices') {
    super(mqttClient, featureName);
    this.featureConfig = this.agentConfig.features[featureName] as ConnectedDevicesFeatureConfig ||
                         { 
                           enabled: true, 
                           update_interval_seconds: 60,
                           dhcp_lease_paths: [
                             '/var/lib/dhcp/dhcpd.leases',
                             '/var/lib/dhcp/dhcpd6.leases',
                             '/var/lib/dhcpcd5/dhcpcd.leases'
                           ]
                         };
  }

  private async getBridgeInterfaces(): Promise<BridgeInfo[]> {
    const bridges: BridgeInfo[] = [];
    
    try {
      // Get all interfaces that look like bridges
      const interfaceResult = await execute_command('ip addr show', false);
      const interfaceLines = interfaceResult.stdout.split('\n');
      
      for (const line of interfaceLines) {
        // Look for bridge-like interface names
        const match = line.match(/^\d+:\s*([\w-]+):/);
        if (match) {
          const ifName = match[1];
          // Only include main bridges, exclude Docker bridges
          if (ifName === 'localBridge' || ifName === 'publicBridge' || ifName === 'internalBridge') {
            const bridgeInfo = await this.getBridgeInfo(ifName);
            if (bridgeInfo) {
              bridges.push(bridgeInfo);
            }
          }
        }
      }

      // Also check brctl for Linux bridges
      try {
        const bridgeOutput = await execute_command('brctl show 2>/dev/null', false);
        if (bridgeOutput.stdout) {
          const lines = bridgeOutput.stdout.split('\n');
          for (const line of lines) {
            if (line.includes('bridge name') || line.trim() === '') continue;
            const parts = line.trim().split(/\s+/);
            if (parts.length >= 1 && parts[0]) {
              const bridgeName = parts[0];
              // Only include main bridges, exclude Docker bridges
              if ((bridgeName === 'localBridge' || bridgeName === 'publicBridge' || bridgeName === 'internalBridge') &&
                  !bridges.find(b => b.name === bridgeName)) {
                const bridgeInfo = await this.getBridgeInfo(bridgeName);
                if (bridgeInfo) {
                  bridges.push(bridgeInfo);
                }
              }
            }
          }
        }
      } catch (error) {
        // brctl not available, continue
      }

    } catch (error) {
      logger.error(`Error detecting bridge interfaces:`, error);
    }

    return bridges;
  }

  private async getBridgeInfo(bridgeName: string): Promise<BridgeInfo | null> {
    try {
      const result = await execute_command(`ip addr show ${bridgeName}`, false);
      if (result.exitCode !== 0) return null;

      const bridge: BridgeInfo = { name: bridgeName };
      
      // Extract IP and subnet
      const ipMatch = result.stdout.match(/inet (\d+\.\d+\.\d+\.\d+)\/(\d+)/);
      if (ipMatch) {
        bridge.ip = ipMatch[1];
        bridge.cidr = ipMatch[2];
        
        // Calculate subnet mask
        const cidr = parseInt(ipMatch[2]);
        const mask = Array(4).fill(0).map((_, i) => {
          const bits = Math.max(0, Math.min(8, cidr - i * 8));
          return 256 - Math.pow(2, 8 - bits);
        }).join('.');
        bridge.subnet = mask;
      }

      return bridge;
    } catch (error) {
      return null;
    }
  }

  private async getArpDevices(bridgeName: string): Promise<ConnectedDevice[]> {
    const devices: ConnectedDevice[] = [];
    
    try {
      // Try modern ip neigh command first
      const neighResult = await execute_command(`ip neigh show dev ${bridgeName}`, false);
      if (neighResult.exitCode === 0 && neighResult.stdout) {
        const lines = neighResult.stdout.split('\n');
        for (const line of lines) {
          if (line.trim() === '') continue;
          
          const match = line.match(/^(\d+\.\d+\.\d+\.\d+)\s+lladdr\s+([\w:]+)\s+(\w+)/);
          if (match) {
            const ip = match[1];
            const mac = match[2];
            const state = match[3];
            
            if (state === 'REACHABLE' || state === 'STALE' || state === 'DELAY') {
              devices.push({
                ip,
                mac,
                vendor: undefined, // Will be filled later with batch lookup
                bridge: bridgeName,
                connection_type: 'arp'
              });
            }
          }
        }
      }

      // Fallback to traditional arp command
      if (devices.length === 0) {
        const arpResult = await execute_command(`arp -i ${bridgeName} -a 2>/dev/null || arp -a | grep ${bridgeName}`, false);
        if (arpResult.stdout) {
          const lines = arpResult.stdout.split('\n');
          for (const line of lines) {
            const match = line.match(/\(([\d.]+)\)\s+at\s+([\w:]+)/);
            if (match) {
              const ip = match[1];
              const mac = match[2];
              devices.push({
                ip,
                mac,
                vendor: undefined, // Will be filled later with batch lookup
                bridge: bridgeName,
                connection_type: 'arp'
              });
            }
          }
        }
      }

    } catch (error) {
      logger.error(`Error getting ARP devices for ${bridgeName}:`, error);
    }

    return devices;
  }

  private async getDhcpDevices(bridgeName: string): Promise<ConnectedDevice[]> {
    const devices: ConnectedDevice[] = [];
    
    for (const leasePath of this.featureConfig.dhcp_lease_paths || []) {
      try {
        const result = await execute_command(`cat ${leasePath} 2>/dev/null || true`, false);
        if (result.stdout) {
          const leaseBlocks = result.stdout.split('lease ');
          for (const block of leaseBlocks) {
            if (!block.trim()) continue;
            
            const ipMatch = block.match(/^(\d+\.\d+\.\d+\.\d+)/);
            const macMatch = block.match(/hardware ethernet\s+([\w:]+);/);
            const hostnameMatch = block.match(/client-hostname\s+"([^"]+)";/);
            const endTimeMatch = block.match(/ends\s+\d+\s+([\d\/]+\s+[\d:]+);/);
            
            if (ipMatch && macMatch) {
              devices.push({
                ip: ipMatch[1],
                mac: macMatch[1],
                hostname: hostnameMatch ? hostnameMatch[1] : undefined,
                lease_time: endTimeMatch ? endTimeMatch[1] : undefined,
                vendor: undefined, // Will be filled later with batch lookup
                bridge: bridgeName,
                connection_type: 'dhcp'
              });
            }
          }
        }
      } catch (error) {
        // File not found or permission error, continue
      }
    }

    return devices;
  }

  private async resolveHostname(ip: string): Promise<string | undefined> {
    try {
      // Defence in depth: only ever pass a strict dotted-quad to the shell
      // lookups below. IPs already come from strict regexes, but guard anyway.
      if (!Validators.isIPv4(ip)) return undefined;

      // Skip common Docker/virtual IPs to avoid unnecessary timeouts
      if (ip.startsWith('172.17.') || ip.startsWith('172.18.') || ip.startsWith('172.19.') || 
          ip.startsWith('172.20.') || ip.startsWith('169.254.') || ip === '127.0.0.1') {
        return undefined;
      }

      // Check cache first
      const cached = this.hostnameCache.get(ip);
      if (cached && (Date.now() - cached.timestamp) < this.HOSTNAME_CACHE_TTL) {
        return cached.hostname;
      }

      // Method 1: Try getent hosts (works with /etc/hosts and DNS) - fastest
      const getentResult = await execute_command(`timeout 2 getent hosts ${ip} 2>/dev/null`, false);
      if (getentResult.exitCode === 0 && getentResult.stdout.trim()) {
        const parts = getentResult.stdout.trim().split(/\s+/);
        if (parts.length >= 2) {
          const hostname = parts[1];
          if (hostname && hostname !== ip) {
            // Accept hostname, but prefer short name over FQDN
            if (!hostname.includes('.')) {
              // Cache and return
              this.hostnameCache.set(ip, { hostname, timestamp: Date.now() });
              return hostname;
            } else {
              // Extract short name from FQDN
              const shortName = hostname.split('.')[0];
              if (shortName && shortName !== ip && shortName.length > 1) {
                // Cache and return
                this.hostnameCache.set(ip, { hostname: shortName, timestamp: Date.now() });
                return shortName;
              }
            }
          }
        }
      }

      // Method 2: Try dig with reverse DNS (faster than nslookup)
      const digResult = await execute_command(`timeout 2 dig +short -x ${ip} 2>/dev/null`, false);
      if (digResult.exitCode === 0 && digResult.stdout.trim()) {
        const hostname = digResult.stdout.trim().replace(/\.$/, ''); // Remove trailing dot
        if (hostname && hostname !== ip && !hostname.includes('NXDOMAIN')) {
          // Accept hostname, but prefer short name over FQDN
          if (!hostname.includes('.')) {
            // Cache and return
            this.hostnameCache.set(ip, { hostname, timestamp: Date.now() });
            return hostname;
          } else {
            // Extract short name from FQDN
            const shortName = hostname.split('.')[0];
            if (shortName && shortName !== ip && shortName.length > 1) {
              // Cache and return
              this.hostnameCache.set(ip, { hostname: shortName, timestamp: Date.now() });
              return shortName;
            }
          }
        }
      }

      // Method 3: Try ping with hostname resolution (some systems report this)
      const pingResult = await execute_command(`timeout 1 ping -c 1 -W 1 ${ip} 2>/dev/null | head -1`, false);
      if (pingResult.exitCode === 0) {
        const pingMatch = pingResult.stdout.match(/PING ([^\s]+) \(/);
        if (pingMatch) {
          const hostname = pingMatch[1];
          if (hostname && hostname !== ip && !hostname.includes(ip)) {
            // Accept hostname, but prefer short name over FQDN
            if (!hostname.includes('.')) {
              // Cache and return
              this.hostnameCache.set(ip, { hostname, timestamp: Date.now() });
              return hostname;
            } else {
              // Extract short name from FQDN
              const shortName = hostname.split('.')[0];
              if (shortName && shortName !== ip && shortName.length > 1) {
                // Cache and return
                this.hostnameCache.set(ip, { hostname: shortName, timestamp: Date.now() });
                return shortName;
              }
            }
          }
        }
      }

      // Cache negative result too to avoid repeated lookups
      this.hostnameCache.set(ip, { hostname: undefined, timestamp: Date.now() });
      return undefined;
    } catch (error) {
      logger.debug(`Error resolving hostname for ${ip}:`, error);
      // Cache negative result
      this.hostnameCache.set(ip, { hostname: undefined, timestamp: Date.now() });
      return undefined;
    }
  }

  private async getBridgeDevices(bridgeName: string): Promise<ConnectedDevice[]> {
    const devices: ConnectedDevice[] = [];
    
    try {
      // Get devices from ARP table
      const arpDevices = await this.getArpDevices(bridgeName);
      devices.push(...arpDevices);

      // Get devices from DHCP leases
      const dhcpDevices = await this.getDhcpDevices(bridgeName);
      
      // Merge DHCP info with ARP info
      for (const dhcpDevice of dhcpDevices) {
        const existingDevice = devices.find(d => d.mac === dhcpDevice.mac || d.ip === dhcpDevice.ip);
        if (existingDevice) {
          existingDevice.hostname = dhcpDevice.hostname || existingDevice.hostname;
          existingDevice.lease_time = dhcpDevice.lease_time;
          existingDevice.connection_type = 'both';
        } else {
          devices.push(dhcpDevice);
        }
      }

      // Try to resolve hostnames for devices without names (parallelized for performance)
      const hostnameResolutions = devices
        .filter(device => !device.hostname && device.ip)
        .map(async (device) => {
          device.hostname = await this.resolveHostname(device.ip);
        });

      if (hostnameResolutions.length > 0) {
        await Promise.all(hostnameResolutions);
      }

      // Batch lookup MAC vendors for better performance
      const macAddresses = devices.filter(d => d.mac).map(d => d.mac);
      if (macAddresses.length > 0) {
        try {
          const vendorResults = await MacVendorLookup.batchLookup(macAddresses);
          for (const device of devices) {
            if (device.mac) {
              const vendorInfo = vendorResults.get(device.mac);
              if (vendorInfo && vendorInfo.vendor) {
                device.vendor = vendorInfo.vendor;
              }
            }
          }
        } catch (error) {
          logger.error(`Error during batch vendor lookup for bridge ${bridgeName}:`, error);
          // Fallback to individual lookups
          for (const device of devices) {
            if (device.mac) {
              try {
                const vendorInfo = await MacVendorLookup.lookupVendor(device.mac);
                if (vendorInfo.vendor) {
                  device.vendor = vendorInfo.vendor;
                }
              } catch (err) {
                logger.debug(`Error looking up vendor for MAC ${device.mac}:`, err);
              }
            }
          }
        }
      }

    } catch (error) {
      logger.error(`Error getting devices for bridge ${bridgeName}:`, error);
    }

    return devices;
  }

  protected async publishDiscovery(): Promise<void> {
    const bridges = await this.getBridgeInterfaces();
    
    for (const bridge of bridges) {
      const objectId = bridge.name.replace(/[^a-zA-Z0-9_-]/g, '_');
      
      await this.publishEntityDiscovery('sensor', `${objectId}_device_count`, {
        name: `Network ${bridge.name} Device Count`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/count/state`),
        icon: 'mdi:counter',
        entity_category: 'diagnostic' as 'diagnostic',
      });

      await this.publishEntityDiscovery('sensor', `${objectId}_devices`, {
        name: `Network ${bridge.name} Connected Devices`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/devices/state`),
        icon: 'mdi:lan-connect',
        entity_category: 'diagnostic' as 'diagnostic',
        json_attributes_topic: this.prefixTopic(`${this.featureName}/${objectId}/devices/attributes`),
      });

      await this.publishEntityDiscovery('sensor', `${objectId}_bridge_info`, {
        name: `Network ${bridge.name} Bridge Info`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/bridge_info/state`),
        icon: 'mdi:bridge',
        entity_category: 'diagnostic' as 'diagnostic',
        json_attributes_topic: this.prefixTopic(`${this.featureName}/${objectId}/bridge_info/attributes`),
      });
    }
  }

  protected async update(): Promise<void> {
    const bridges = await this.getBridgeInterfaces();
    
    if (bridges.length === 0) {
      logger.debug(`No bridge interfaces found for ${this.featureName}.`);
      return;
    }

    for (const bridge of bridges) {
      const objectId = bridge.name.replace(/[^a-zA-Z0-9_-]/g, '_');
      
      try {
        const devices = await this.getBridgeDevices(bridge.name);
        
        // Publish device count
        await this.publishState(`${this.featureName}/${objectId}/count/state`, devices.length, true);
        
        // Publish devices list
        await this.publishState(`${this.featureName}/${objectId}/devices/state`, 
          devices.length > 0 ? `${devices.length} devices` : 'No devices', true);
        
        // Publish device details as attributes
        const deviceAttributes = {
          devices: devices.map(d => ({
            ip: d.ip,
            mac: d.mac,
            hostname: d.hostname || 'Unknown',
            vendor: d.vendor || 'Unknown',
            lease_time: d.lease_time,
            connection_type: d.connection_type
          })),
          last_updated: new Date().toISOString(),
          bridge_name: bridge.name
        };
        await this.publishState(`${this.featureName}/${objectId}/devices/attributes`, 
          JSON.stringify(deviceAttributes), true);

        // Publish bridge info
        await this.publishState(`${this.featureName}/${objectId}/bridge_info/state`, 
          bridge.ip ? `${bridge.ip}/${bridge.cidr}` : 'No IP', true);
        
        const bridgeAttributes = {
          name: bridge.name,
          ip: bridge.ip,
          subnet_mask: bridge.subnet,
          cidr: bridge.cidr,
          device_count: devices.length
        };
        await this.publishState(`${this.featureName}/${objectId}/bridge_info/attributes`, 
          JSON.stringify(bridgeAttributes), true);

      } catch (error: any) {
        logger.error(`Error updating connected devices for bridge ${bridge.name}:`, error.message);
        await this.publishState(`${this.featureName}/${objectId}/count/state`, 'Error', true);
        await this.publishState(`${this.featureName}/${objectId}/devices/state`, 'Error', true);
      }
    }
  }
}
