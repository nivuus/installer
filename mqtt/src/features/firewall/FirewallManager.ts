// src/features/firewall/FirewallManager.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import { Validators, rejectInvalid } from '../../utils/validators';
import logger from '../../utils/logger';

interface FirewallManagerFeatureConfig extends FeatureConfig {
  // Future config options for firewall management
}

export class FirewallManager extends BaseFeature {
  protected featureConfig: FirewallManagerFeatureConfig;
  private readonly relevantInterfacePatterns = /^(eth|enp|wlp|wlo|ppp)/i; // From project requirements
  private availableZones: string[] = [];

  // State storage for user inputs
  private currentPort: string = '';
  private currentProtocol: string = 'tcp';
  private currentZone: string = 'public';
  private currentService: string = '';

  // State storage for port forward inputs
  private currentForwardPort: string = '';
  private currentForwardToAddr: string = '';
  private currentForwardToPort: string = '';
  private currentForwardProtocol: string = 'tcp';
  private currentForwardZone: string = 'public';

  // Interface to zone mapping
  private interfaceZoneMap: Map<string, string> = new Map();

  constructor(mqttClient: MqttClient, featureName: string = 'firewalld_manager') {
    super(mqttClient, featureName);
    this.featureConfig = this.agentConfig.features[featureName] as FirewallManagerFeatureConfig ||
                         { enabled: true, update_interval_seconds: 300 }; // Default to enabled
  }

  // Helper: Get port forwards for a specific zone
  private async getZonePortForwards(zone: string): Promise<Array<{port: string, proto: string, toport: string, toaddr: string}>> {
    try {
      const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--list-forward-ports']);
      if (result.exitCode !== 0) return [];

      const forwards: Array<{port: string, proto: string, toport: string, toaddr: string}> = [];
      const lines = result.stdout.trim().split('\n');

      for (const line of lines) {
        if (!line.trim()) continue;
        // Format: port=3389:proto=tcp:toport=3389:toaddr=192.168.3.2
        const match = line.match(/port=(\d+):proto=(tcp|udp):toport=(\d+):toaddr=([\d.]+)/);
        if (match) {
          forwards.push({
            port: match[1],
            proto: match[2],
            toport: match[3],
            toaddr: match[4]
          });
        }
      }
      return forwards;
    } catch (error) {
      logger.error(`Error getting port forwards for zone ${zone}:`, error);
      return [];
    }
  }

  // Helper: Get services in a zone
  private async getZoneServices(zone: string): Promise<string[]> {
    try {
      const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--list-services']);
      if (result.exitCode !== 0) return [];
      return result.stdout.trim().split(/\s+/).filter(s => s.length > 0);
    } catch (error) {
      logger.error(`Error getting services for zone ${zone}:`, error);
      return [];
    }
  }

  // Helper: Get ports in a zone
  private async getZonePorts(zone: string): Promise<string[]> {
    try {
      const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--list-ports']);
      if (result.exitCode !== 0) return [];
      return result.stdout.trim().split(/\s+/).filter(p => p.length > 0);
    } catch (error) {
      logger.error(`Error getting ports for zone ${zone}:`, error);
      return [];
    }
  }

  // Helper: Check if masquerading is enabled in a zone
  private async getZoneMasquerade(zone: string): Promise<boolean> {
    try {
      const result = await execute_argv('firewall-cmd', [`--zone=${zone}`, '--query-masquerade']);
      return result.exitCode === 0; // Exit code 0 means yes, 1 means no
    } catch (error) {
      logger.error(`Error querying masquerade for zone ${zone}:`, error);
      return false;
    }
  }

  protected async setup(): Promise<void> {
    // Subscribe to command topics for firewall management
    const commandTopic = this.prefixTopic(`${this.featureName}/command/#`);
    await this.mqttClient.subscribe(commandTopic);

    // Subscribe to all input and select topics with wildcard
    const allInputsTopic = this.prefixTopic(`${this.featureName}/+/set`);
    await this.mqttClient.subscribe(allInputsTopic);

    this.mqttClient.on('message', this.handleMqttMessage.bind(this));
  }

  private async handleMqttMessage(topic: string, payload: Buffer): Promise<void> {
    const message = payload.toString();

    // Handle input states for port/service management
    if (topic === this.prefixTopic(`${this.featureName}/port_input/set`)) {
      this.currentPort = message;
      await this.publishState(`${this.featureName}/port_input/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/protocol_select/set`)) {
      this.currentProtocol = message;
      await this.publishState(`${this.featureName}/protocol_select/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/zone_select/set`)) {
      this.currentZone = message;
      await this.publishState(`${this.featureName}/zone_select/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/service_input/set`)) {
      this.currentService = message;
      await this.publishState(`${this.featureName}/service_input/state`, message, true);
    }

    // Handle input states for port forward management
    else if (topic === this.prefixTopic(`${this.featureName}/forward_port_input/set`)) {
      this.currentForwardPort = message;
      await this.publishState(`${this.featureName}/forward_port_input/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/forward_toaddr_input/set`)) {
      this.currentForwardToAddr = message;
      await this.publishState(`${this.featureName}/forward_toaddr_input/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/forward_toport_input/set`)) {
      this.currentForwardToPort = message;
      await this.publishState(`${this.featureName}/forward_toport_input/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/forward_protocol_select/set`)) {
      this.currentForwardProtocol = message;
      await this.publishState(`${this.featureName}/forward_protocol_select/state`, message, true);
    } else if (topic === this.prefixTopic(`${this.featureName}/forward_zone_select/set`)) {
      this.currentForwardZone = message;
      await this.publishState(`${this.featureName}/forward_zone_select/state`, message, true);
    }

    // Handle interface zone changes - check if topic matches pattern interface_XXXX_zone_select/set
    else if (topic.includes('_zone_select/set')) {
      const topicPrefix = this.prefixTopic(`${this.featureName}/`);
      if (topic.startsWith(topicPrefix)) {
        const relativeTopic = topic.substring(topicPrefix.length);
        const match = relativeTopic.match(/^(.+)_zone_select\/set$/);
        if (match) {
          const interfaceName = match[1];
          const newZone = message;
          await this.handleInterfaceZoneChange(interfaceName, newZone);
        }
      }
    }

    // Handle action commands
    else if (topic === this.prefixTopic(`${this.featureName}/command/add_port`) && message === 'ADD_PORT') {
      await this.handleAddPortAction();
    } else if (topic === this.prefixTopic(`${this.featureName}/command/remove_port`) && message === 'REMOVE_PORT') {
      await this.handleRemovePortAction();
    } else if (topic === this.prefixTopic(`${this.featureName}/command/add_service`) && message === 'ADD_SERVICE') {
      await this.handleAddServiceAction();
    } else if (topic === this.prefixTopic(`${this.featureName}/command/remove_service`) && message === 'REMOVE_SERVICE') {
      await this.handleRemoveServiceAction();
    } else if (topic === this.prefixTopic(`${this.featureName}/command/add_forward`) && message === 'ADD_FORWARD') {
      await this.handleAddForwardAction();
    } else if (topic === this.prefixTopic(`${this.featureName}/command/remove_forward`) && message === 'REMOVE_FORWARD') {
      await this.handleRemoveForwardAction();
    }
  }

  // Strict validation of MQTT-controlled inputs before any firewall mutation.
  private validatePortInputs(zone: string, port: string, protocol: string): boolean {
    if (!Validators.isZone(zone)) return rejectInvalid('zone', zone, this.featureName);
    if (!Validators.isPort(port)) return rejectInvalid('port', port, this.featureName);
    if (!Validators.isProtocol(protocol)) return rejectInvalid('protocol', protocol, this.featureName);
    return true;
  }

  private validateServiceInputs(zone: string, service: string): boolean {
    if (!Validators.isZone(zone)) return rejectInvalid('zone', zone, this.featureName);
    if (!Validators.isFirewallService(service)) return rejectInvalid('service', service, this.featureName);
    return true;
  }

  private validateForwardInputs(): boolean {
    if (!Validators.isZone(this.currentForwardZone)) return rejectInvalid('zone', this.currentForwardZone, this.featureName);
    if (!Validators.isPort(this.currentForwardPort)) return rejectInvalid('forward_port', this.currentForwardPort, this.featureName);
    if (!Validators.isProtocol(this.currentForwardProtocol)) return rejectInvalid('forward_protocol', this.currentForwardProtocol, this.featureName);
    if (!Validators.isPort(this.currentForwardToPort)) return rejectInvalid('forward_toport', this.currentForwardToPort, this.featureName);
    if (!Validators.isIPv4(this.currentForwardToAddr)) return rejectInvalid('forward_toaddr', this.currentForwardToAddr, this.featureName);
    return true;
  }

  private async getCurrentInputs(): Promise<{port: string, protocol: string, zone: string, service: string}> {
    return {
      port: this.currentPort,
      protocol: this.currentProtocol,
      zone: this.currentZone,
      service: this.currentService
    };
  }

  private async handleAddPortAction(): Promise<void> {
    try {
      const inputs = await this.getCurrentInputs();
      if (!inputs.port || !inputs.protocol || !inputs.zone) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Missing port, protocol or zone', false);
        return;
      }
      if (!this.validatePortInputs(inputs.zone, inputs.port, inputs.protocol)) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid port, protocol or zone', false);
        return;
      }

      logger.info(`Adding port ${inputs.port}/${inputs.protocol} to zone ${inputs.zone}`);
      await execute_argv('firewall-cmd', [`--zone=${inputs.zone}`, `--add-port=${inputs.port}/${inputs.protocol}`, '--permanent']);
      await execute_argv('firewall-cmd', ['--reload']);
      await this.publishState(`${this.featureName}/last_action/state`, `Added port ${inputs.port}/${inputs.protocol} to ${inputs.zone}`, false);
    } catch (error: any) {
      logger.error(`Error adding port:`, error);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, false);
    }
  }

  private async handleRemovePortAction(): Promise<void> {
    try {
      const inputs = await this.getCurrentInputs();
      if (!inputs.port || !inputs.protocol || !inputs.zone) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Missing port, protocol or zone', false);
        return;
      }
      if (!this.validatePortInputs(inputs.zone, inputs.port, inputs.protocol)) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid port, protocol or zone', false);
        return;
      }

      logger.info(`Removing port ${inputs.port}/${inputs.protocol} from zone ${inputs.zone}`);
      await execute_argv('firewall-cmd', [`--zone=${inputs.zone}`, `--remove-port=${inputs.port}/${inputs.protocol}`, '--permanent']);
      await execute_argv('firewall-cmd', ['--reload']);
      await this.publishState(`${this.featureName}/last_action/state`, `Removed port ${inputs.port}/${inputs.protocol} from ${inputs.zone}`, false);
    } catch (error: any) {
      logger.error(`Error removing port:`, error);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, false);
    }
  }

  private async handleAddServiceAction(): Promise<void> {
    try {
      const inputs = await this.getCurrentInputs();
      if (!inputs.service || !inputs.zone) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Missing service or zone', false);
        return;
      }
      if (!this.validateServiceInputs(inputs.zone, inputs.service)) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid service or zone', false);
        return;
      }

      logger.info(`Adding service ${inputs.service} to zone ${inputs.zone}`);
      await execute_argv('firewall-cmd', [`--zone=${inputs.zone}`, `--add-service=${inputs.service}`, '--permanent']);
      await execute_argv('firewall-cmd', ['--reload']);
      await this.publishState(`${this.featureName}/last_action/state`, `Added service ${inputs.service} to ${inputs.zone}`, false);
    } catch (error: any) {
      logger.error(`Error adding service:`, error);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, false);
    }
  }

  private async handleRemoveServiceAction(): Promise<void> {
    try {
      const inputs = await this.getCurrentInputs();
      if (!inputs.service || !inputs.zone) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Missing service or zone', false);
        return;
      }
      if (!this.validateServiceInputs(inputs.zone, inputs.service)) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid service or zone', false);
        return;
      }

      logger.info(`Removing service ${inputs.service} from zone ${inputs.zone}`);
      await execute_argv('firewall-cmd', [`--zone=${inputs.zone}`, `--remove-service=${inputs.service}`, '--permanent']);
      await execute_argv('firewall-cmd', ['--reload']);
      await this.publishState(`${this.featureName}/last_action/state`, `Removed service ${inputs.service} from ${inputs.zone}`, false);
    } catch (error: any) {
      logger.error(`Error removing service:`, error);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, false);
    }
  }

  private async handleInterfaceZoneChange(interfaceName: string, newZone: string): Promise<void> {
    try {
      const oldZone = this.interfaceZoneMap.get(interfaceName);
      if (!oldZone) {
        await this.publishState(`${this.featureName}/last_action/state`, `Error: Interface ${interfaceName} not found`, false);
        return;
      }

      if (oldZone === newZone) {
        return; // No change
      }

      if (!Validators.isInterface(interfaceName) || !Validators.isZone(oldZone) || !Validators.isZone(newZone)) {
        rejectInvalid('interface/zone', `${interfaceName} ${oldZone}->${newZone}`, this.featureName);
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid interface or zone', false);
        return;
      }

      logger.info(`Moving interface ${interfaceName} from ${oldZone} to ${newZone}`);

      // Remove from old zone
      await execute_argv('firewall-cmd', [`--zone=${oldZone}`, `--remove-interface=${interfaceName}`, '--permanent']);
      // Add to new zone
      await execute_argv('firewall-cmd', [`--zone=${newZone}`, `--add-interface=${interfaceName}`, '--permanent']);
      // Reload firewall
      await execute_argv('firewall-cmd', ['--reload']);

      // Update internal mapping
      this.interfaceZoneMap.set(interfaceName, newZone);

      // Update state
      await this.publishState(`${this.featureName}/${interfaceName}_zone_select/state`, newZone, true);
      await this.publishState(`${this.featureName}/last_action/state`, `Moved ${interfaceName} to ${newZone}`, false);
    } catch (error: any) {
      logger.error(`Error changing interface zone:`, error);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, false);
    }
  }

  private async handleAddForwardAction(): Promise<void> {
    try {
      if (!this.currentForwardPort || !this.currentForwardToAddr || !this.currentForwardToPort) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Missing port, destination IP or destination port', false);
        return;
      }

      if (!this.validateForwardInputs()) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid forward port, protocol, address or zone', false);
        return;
      }

      // Note: the shell literal used single quotes around the rule; with argv the
      // value is passed verbatim as one argument, so NO surrounding quotes.
      const forwardRule = `port=${this.currentForwardPort}:proto=${this.currentForwardProtocol}:toport=${this.currentForwardToPort}:toaddr=${this.currentForwardToAddr}`;

      logger.info(`Adding port forward to zone ${this.currentForwardZone}: ${forwardRule}`);
      await execute_argv('firewall-cmd', [`--zone=${this.currentForwardZone}`, `--add-forward-port=${forwardRule}`, '--permanent']);
      await execute_argv('firewall-cmd', ['--reload']);

      await this.publishState(`${this.featureName}/last_action/state`, `Added forward ${this.currentForwardPort} → ${this.currentForwardToAddr}:${this.currentForwardToPort}`, false);

      // Clear inputs
      this.currentForwardPort = '';
      this.currentForwardToAddr = '';
      this.currentForwardToPort = '';
      await this.publishState(`${this.featureName}/forward_port_input/state`, '', true);
      await this.publishState(`${this.featureName}/forward_toaddr_input/state`, '', true);
      await this.publishState(`${this.featureName}/forward_toport_input/state`, '', true);
    } catch (error: any) {
      logger.error(`Error adding port forward:`, error);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, false);
    }
  }

  private async handleRemoveForwardAction(): Promise<void> {
    try {
      if (!this.currentForwardPort || !this.currentForwardToAddr || !this.currentForwardToPort) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Missing port, destination IP or destination port', false);
        return;
      }

      if (!this.validateForwardInputs()) {
        await this.publishState(`${this.featureName}/last_action/state`, 'Error: Invalid forward port, protocol, address or zone', false);
        return;
      }

      const forwardRule = `port=${this.currentForwardPort}:proto=${this.currentForwardProtocol}:toport=${this.currentForwardToPort}:toaddr=${this.currentForwardToAddr}`;

      logger.info(`Removing port forward from zone ${this.currentForwardZone}: ${forwardRule}`);
      await execute_argv('firewall-cmd', [`--zone=${this.currentForwardZone}`, `--remove-forward-port=${forwardRule}`, '--permanent']);
      await execute_argv('firewall-cmd', ['--reload']);

      await this.publishState(`${this.featureName}/last_action/state`, `Removed forward ${this.currentForwardPort} → ${this.currentForwardToAddr}:${this.currentForwardToPort}`, false);

      // Clear inputs
      this.currentForwardPort = '';
      this.currentForwardToAddr = '';
      this.currentForwardToPort = '';
      await this.publishState(`${this.featureName}/forward_port_input/state`, '', true);
      await this.publishState(`${this.featureName}/forward_toaddr_input/state`, '', true);
      await this.publishState(`${this.featureName}/forward_toport_input/state`, '', true);
    } catch (error: any) {
      logger.error(`Error removing port forward:`, error);
      await this.publishState(`${this.featureName}/last_action/state`, `Error: ${error.message}`, false);
    }
  }

  private parseActiveZones(output: string): Map<string, string[]> {
    const zoneInterfaceMap = new Map<string, string[]>();
    if (!output || typeof output !== 'string') return zoneInterfaceMap;

    const lines = output.split('\n');
    let currentZone: string | null = null;

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (!trimmedLine) continue; // Skip empty lines

      // Check if the line is a zone name (does not start with whitespace)
      if (line.match(/^\S/) && !trimmedLine.startsWith('interfaces:') && !trimmedLine.startsWith('sources:')) {
        // It's a zone name. It might or might not have interfaces on the same line.
        const parts = trimmedLine.split(':');
        // firewalld >= 2.x (Debian Trixie) appends " (default)" to the default
        // zone in `--get-active-zones`; strip it or every firewall-cmd call for
        // that zone fails with INVALID_ZONE (the internal zone went unmonitored).
        currentZone = parts[0].trim().replace(/\s*\(default\)\s*$/, '');
        if (parts.length > 1 && parts[1].trim()) {
          // Interfaces are on the same line as the zone
          const interfaces = parts[1].trim().split(/\s+/).filter(iface => iface.length > 0);
          if (interfaces.length > 0) {
            zoneInterfaceMap.set(currentZone, (zoneInterfaceMap.get(currentZone) || []).concat(interfaces));
          }
        } else {
          // Interfaces might be on the next line(s), set currentZone and wait for 'interfaces:'
          // If this zone doesn't have an explicit interfaces line, it might be an empty zone or just sources.
          // Ensure the zone is added even if no interfaces are listed immediately,
          // in case they appear on a following "interfaces:" line.
          if (!zoneInterfaceMap.has(currentZone)) {
            zoneInterfaceMap.set(currentZone, []);
          }
        }
      } else if (currentZone && trimmedLine.startsWith('interfaces:')) {
        // Interfaces for the currentZone
        const interfaces = trimmedLine.substring('interfaces:'.length).trim().split(/\s+/).filter(iface => iface.length > 0);
        if (interfaces.length > 0) {
          zoneInterfaceMap.set(currentZone, (zoneInterfaceMap.get(currentZone) || []).concat(interfaces));
        }
      } else if (currentZone && trimmedLine.startsWith('sources:')) {
        // Sources for the currentZone, ignore for now or handle if needed later
      }
      // If line doesn't match any known pattern and a currentZone is active,
      // it might be a continuation or something else. For now, this parser is simple.
    }
    
    // Clean up zones that were added but ended up with no interfaces
    for (const [zone, interfaces] of zoneInterfaceMap.entries()) {
        if (interfaces.length === 0) {
            zoneInterfaceMap.delete(zone);
        }
    }
    return zoneInterfaceMap;
  }

  protected async publishDiscovery(): Promise<void> {
    // Discovery will be dynamic based on active zones and interfaces found during the first update.
    // We can call `update()` once here to populate, then publish.
    // Or, features can dynamically create entities if BaseFeature supports it.
    // For simplicity, we'll try to get initial data here.
    try {
      const activeZonesOutput = await execute_argv('firewall-cmd', ['--get-active-zones']);
      const zoneInterfaceMap = this.parseActiveZones(activeZonesOutput.stdout);

      // Get all available zones for management
      const zonesOutput = await execute_argv('firewall-cmd', ['--get-zones']);
      this.availableZones = zonesOutput.stdout.trim().split(/\s+/).filter(zone => zone.length > 0);

      const allInterfaces = new Set<string>();
      zoneInterfaceMap.forEach(interfaces => interfaces.forEach(iface => allInterfaces.add(iface)));

      for (const iface of allInterfaces) {
        if (this.relevantInterfacePatterns.test(iface)) {
          await this.publishEntityDiscovery('sensor', `${iface}_zone`, {
            name: `Network Firewall ${iface} Zone Sensor`,
            state_topic: this.prefixTopic(`${this.featureName}/${iface}/zone/state`),
            icon: 'mdi:security-network',
            entity_category: 'diagnostic' as 'diagnostic',
          });
        }
      }

      // Sensor for all active zones and their interfaces
      await this.publishEntityDiscovery('sensor', 'active_zones_summary', {
        name: `Network Firewall Active Zones Sensor`,
        state_topic: this.prefixTopic(`${this.featureName}/active_zones/state`), // Could be count of zones or a summary string
        icon: 'mdi:shield-check',
        entity_category: 'diagnostic' as 'diagnostic',
        json_attributes_topic: this.prefixTopic(`${this.featureName}/active_zones/attributes`),
      });

      // Management entities
      await this.publishEntityDiscovery('sensor', 'last_action', {
        name: `Network Firewall Last Action Sensor`,
        state_topic: this.prefixTopic(`${this.featureName}/last_action/state`),
        icon: 'mdi:shield-sync',
        entity_category: 'diagnostic' as 'diagnostic',
      });

      // Input text entities for port management
      await this.publishEntityDiscovery('text', 'port_input', {
        name: `Network Firewall Port Input`,
        command_topic: this.prefixTopic(`${this.featureName}/port_input/set`),
        state_topic: this.prefixTopic(`${this.featureName}/port_input/state`),
        icon: 'mdi:ethernet-cable',
        entity_category: 'config' as 'config',
        pattern: '^[0-9]{1,5}$',
        mode: 'text',
      });

      await this.publishEntityDiscovery('select', 'protocol_select', {
        name: `Network Firewall Protocol Select`,
        command_topic: this.prefixTopic(`${this.featureName}/protocol_select/set`),
        state_topic: this.prefixTopic(`${this.featureName}/protocol_select/state`),
        icon: 'mdi:protocol',
        entity_category: 'config' as 'config',
        options: ['tcp', 'udp'],
      });

      await this.publishEntityDiscovery('select', 'zone_select', {
        name: `Network Firewall Zone Select`,
        command_topic: this.prefixTopic(`${this.featureName}/zone_select/set`),
        state_topic: this.prefixTopic(`${this.featureName}/zone_select/state`),
        icon: 'mdi:shield',
        entity_category: 'config' as 'config',
        options: this.availableZones,
      });

      await this.publishEntityDiscovery('text', 'service_input', {
        name: `Network Firewall Service Input`,
        command_topic: this.prefixTopic(`${this.featureName}/service_input/set`),
        state_topic: this.prefixTopic(`${this.featureName}/service_input/state`),
        icon: 'mdi:cog',
        entity_category: 'config' as 'config',
        mode: 'text',
      });

      // Action buttons for port/service management
      await this.publishEntityDiscovery('button', 'add_port_btn', {
        name: `Network Firewall Add Port Button`,
        command_topic: this.prefixTopic(`${this.featureName}/command/add_port`),
        payload_press: 'ADD_PORT',
        icon: 'mdi:plus-circle',
        entity_category: 'config' as 'config',
      });

      await this.publishEntityDiscovery('button', 'remove_port_btn', {
        name: `Network Firewall Remove Port Button`,
        command_topic: this.prefixTopic(`${this.featureName}/command/remove_port`),
        payload_press: 'REMOVE_PORT',
        icon: 'mdi:minus-circle',
        entity_category: 'config' as 'config',
      });

      await this.publishEntityDiscovery('button', 'add_service_btn', {
        name: `Network Firewall Add Service Button`,
        command_topic: this.prefixTopic(`${this.featureName}/command/add_service`),
        payload_press: 'ADD_SERVICE',
        icon: 'mdi:plus-box',
        entity_category: 'config' as 'config',
      });

      await this.publishEntityDiscovery('button', 'remove_service_btn', {
        name: `Network Firewall Remove Service Button`,
        command_topic: this.prefixTopic(`${this.featureName}/command/remove_service`),
        payload_press: 'REMOVE_SERVICE',
        icon: 'mdi:minus-box',
        entity_category: 'config' as 'config',
      });

      // === Interface Zone Select entities ===
      // Create a select dropdown for each relevant interface to change its zone
      // Include bridges (localBridge, internalBridge, publicBridge) and standard network interfaces
      for (const iface of allInterfaces) {
        const isRelevant = this.relevantInterfacePatterns.test(iface) || iface.includes('Bridge');
        if (isRelevant) {
          const currentZone = Array.from(zoneInterfaceMap.entries()).find(([_, interfaces]) => interfaces.includes(iface))?.[0] || 'public';
          this.interfaceZoneMap.set(iface, currentZone);

          await this.publishEntityDiscovery('select', `${iface}_zone_select`, {
            name: `Network Firewall ${iface} Zone Select`,
            command_topic: this.prefixTopic(`${this.featureName}/${iface}_zone_select/set`),
            state_topic: this.prefixTopic(`${this.featureName}/${iface}_zone_select/state`),
            icon: 'mdi:network',
            entity_category: 'config' as 'config',
            options: this.availableZones,
          });

          // Initialize state with current zone
          await this.publishState(`${this.featureName}/${iface}_zone_select/state`, currentZone, true);
        }
      }

      // === Port Forward Management Entities ===
      await this.publishEntityDiscovery('text', 'forward_port_input', {
        name: `Network Firewall Forward Port Input`,
        command_topic: this.prefixTopic(`${this.featureName}/forward_port_input/set`),
        state_topic: this.prefixTopic(`${this.featureName}/forward_port_input/state`),
        icon: 'mdi:ethernet',
        entity_category: 'config' as 'config',
        pattern: '^[0-9]{1,5}$',
        mode: 'text',
      });

      await this.publishEntityDiscovery('text', 'forward_toaddr_input', {
        name: `Network Firewall Forward To Address Input`,
        command_topic: this.prefixTopic(`${this.featureName}/forward_toaddr_input/set`),
        state_topic: this.prefixTopic(`${this.featureName}/forward_toaddr_input/state`),
        icon: 'mdi:ip',
        entity_category: 'config' as 'config',
        mode: 'text',
      });

      await this.publishEntityDiscovery('text', 'forward_toport_input', {
        name: `Network Firewall Forward To Port Input`,
        command_topic: this.prefixTopic(`${this.featureName}/forward_toport_input/set`),
        state_topic: this.prefixTopic(`${this.featureName}/forward_toport_input/state`),
        icon: 'mdi:ethernet',
        entity_category: 'config' as 'config',
        pattern: '^[0-9]{1,5}$',
        mode: 'text',
      });

      await this.publishEntityDiscovery('select', 'forward_protocol_select', {
        name: `Network Firewall Forward Protocol Select`,
        command_topic: this.prefixTopic(`${this.featureName}/forward_protocol_select/set`),
        state_topic: this.prefixTopic(`${this.featureName}/forward_protocol_select/state`),
        icon: 'mdi:protocol',
        entity_category: 'config' as 'config',
        options: ['tcp', 'udp'],
      });

      await this.publishEntityDiscovery('select', 'forward_zone_select', {
        name: `Network Firewall Forward Zone Select`,
        command_topic: this.prefixTopic(`${this.featureName}/forward_zone_select/set`),
        state_topic: this.prefixTopic(`${this.featureName}/forward_zone_select/state`),
        icon: 'mdi:shield',
        entity_category: 'config' as 'config',
        options: this.availableZones,
      });

      await this.publishEntityDiscovery('button', 'add_forward_btn', {
        name: `Network Firewall Add Forward Button`,
        command_topic: this.prefixTopic(`${this.featureName}/command/add_forward`),
        payload_press: 'ADD_FORWARD',
        icon: 'mdi:arrow-right-bold-circle',
        entity_category: 'config' as 'config',
      });

      await this.publishEntityDiscovery('button', 'remove_forward_btn', {
        name: `Network Firewall Remove Forward Button`,
        command_topic: this.prefixTopic(`${this.featureName}/command/remove_forward`),
        payload_press: 'REMOVE_FORWARD',
        icon: 'mdi:arrow-left-bold-circle',
        entity_category: 'config' as 'config',
      });

      // === Zone Detail Sensors ===
      // For each active zone, create sensors for port forwards, services, ports, masquerade
      for (const [zone, interfaces] of zoneInterfaceMap) {
        // Port forwards sensor
        await this.publishEntityDiscovery('sensor', `${zone}_port_forwards`, {
          name: `Network Firewall ${zone} Port Forwards Sensor`,
          state_topic: this.prefixTopic(`${this.featureName}/${zone}/port_forwards/state`),
          icon: 'mdi:arrow-right-bold',
          entity_category: 'diagnostic' as 'diagnostic',
          json_attributes_topic: this.prefixTopic(`${this.featureName}/${zone}/port_forwards/attributes`),
        });

        // Services sensor
        await this.publishEntityDiscovery('sensor', `${zone}_services`, {
          name: `Network Firewall ${zone} Services Sensor`,
          state_topic: this.prefixTopic(`${this.featureName}/${zone}/services/state`),
          icon: 'mdi:cog',
          entity_category: 'diagnostic' as 'diagnostic',
          json_attributes_topic: this.prefixTopic(`${this.featureName}/${zone}/services/attributes`),
        });

        // Ports sensor
        await this.publishEntityDiscovery('sensor', `${zone}_ports`, {
          name: `Network Firewall ${zone} Ports Sensor`,
          state_topic: this.prefixTopic(`${this.featureName}/${zone}/ports/state`),
          icon: 'mdi:gate',
          entity_category: 'diagnostic' as 'diagnostic',
          json_attributes_topic: this.prefixTopic(`${this.featureName}/${zone}/ports/attributes`),
        });

        // Masquerade binary sensor
        await this.publishEntityDiscovery('binary_sensor', `${zone}_masquerade`, {
          name: `Network Firewall ${zone} Masquerade Sensor`,
          state_topic: this.prefixTopic(`${this.featureName}/${zone}/masquerade/state`),
          payload_on: 'ON',
          payload_off: 'OFF',
          icon: 'mdi:incognito',
          entity_category: 'diagnostic' as 'diagnostic',
        });
      }

      // Initialize states and sync with stored values
      this.currentZone = this.availableZones[0] || 'public';
      this.currentForwardZone = this.availableZones[0] || 'public';

      await this.publishState(`${this.featureName}/last_action/state`, 'Ready', true);
      await this.publishState(`${this.featureName}/port_input/state`, '', true);
      await this.publishState(`${this.featureName}/protocol_select/state`, this.currentProtocol, true);
      await this.publishState(`${this.featureName}/zone_select/state`, this.currentZone, true);
      await this.publishState(`${this.featureName}/service_input/state`, '', true);

      // Initialize forward management states
      await this.publishState(`${this.featureName}/forward_port_input/state`, '', true);
      await this.publishState(`${this.featureName}/forward_toaddr_input/state`, '', true);
      await this.publishState(`${this.featureName}/forward_toport_input/state`, '', true);
      await this.publishState(`${this.featureName}/forward_protocol_select/state`, this.currentForwardProtocol, true);
      await this.publishState(`${this.featureName}/forward_zone_select/state`, this.currentForwardZone, true);

    } catch (error) {
      logger.error(`Error during firewall discovery for ${this.featureName}:`, error);
    }
  }

  protected async update(): Promise<void> {
    try {
      const activeZonesOutput = await execute_argv('firewall-cmd', ['--get-active-zones']);
      const zoneInterfaceMap = this.parseActiveZones(activeZonesOutput.stdout);

      const interfaceToZone: { [iface: string]: string } = {};
      const activeZoneDetails: { [zone: string]: string[] } = {};

      zoneInterfaceMap.forEach((interfaces, zone) => {
        activeZoneDetails[zone] = interfaces;
        interfaces.forEach(iface => {
          if (this.relevantInterfacePatterns.test(iface)) {
            interfaceToZone[iface] = zone;
          }
        });
      });

      for (const iface in interfaceToZone) {
        await this.publishState(`${this.featureName}/${iface}/zone/state`, interfaceToZone[iface], true);
        // Update internal mapping
        this.interfaceZoneMap.set(iface, interfaceToZone[iface]);
      }

      // Publish summary sensor
      const zoneCount = Object.keys(activeZoneDetails).length;
      await this.publishState(`${this.featureName}/active_zones/state`, `${zoneCount} active zone(s)`, true);
      await this.publishAttributes(`${this.featureName}/active_zones/attributes`, activeZoneDetails, true);

      // Publish zone details (port forwards, services, ports, masquerade) for each active zone
      for (const zone of zoneInterfaceMap.keys()) {
        // Port forwards
        const forwards = await this.getZonePortForwards(zone);
        await this.publishState(`${this.featureName}/${zone}/port_forwards/state`, forwards.length.toString(), true);
        await this.publishAttributes(`${this.featureName}/${zone}/port_forwards/attributes`, { forwards }, true);

        // Services
        const services = await this.getZoneServices(zone);
        await this.publishState(`${this.featureName}/${zone}/services/state`, services.length.toString(), true);
        await this.publishAttributes(`${this.featureName}/${zone}/services/attributes`, { services }, true);

        // Ports
        const ports = await this.getZonePorts(zone);
        await this.publishState(`${this.featureName}/${zone}/ports/state`, ports.length.toString(), true);
        await this.publishAttributes(`${this.featureName}/${zone}/ports/attributes`, { ports }, true);

        // Masquerade
        const masquerade = await this.getZoneMasquerade(zone);
        await this.publishState(`${this.featureName}/${zone}/masquerade/state`, masquerade ? 'ON' : 'OFF', true);
      }

    } catch (error) {
      logger.error(`Error updating firewall status for ${this.featureName}:`, error);
    }
  }
}
