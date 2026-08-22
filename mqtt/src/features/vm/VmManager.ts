// src/features/vm/VmManager.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import { Validators, rejectInvalid } from '../../utils/validators';
import logger from '../../utils/logger';

interface VmManagerFeatureConfig extends FeatureConfig {
  vm_names?: string[]; // Optional: specific VMs to manage by name
}

interface VmInfo {
  id: string;
  name: string;
  state: string;
}

export class VmManager extends BaseFeature {
  protected featureConfig: VmManagerFeatureConfig;

  constructor(mqttClient: MqttClient, featureName: string = 'vm_manager') {
    super(mqttClient, featureName);
    this.featureConfig = this.agentConfig.features[featureName] as VmManagerFeatureConfig ||
                         { enabled: true, update_interval_seconds: 60 };
  }

  private parseVirshList(output: string): VmInfo[] {
    const vms: VmInfo[] = [];
    if (!output) return vms;

    const lines = output.split('\n');
    // Skip header lines (usually 2: " Id Name State" and "---------------------------")
    for (let i = 2; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;

      // Regex to capture Id (can be '-' or number), Name, and State (can have spaces)
      const match = line.match(/^\s*([\w-]+)\s+([\w.-]+)\s+(.*)$/);
      if (match) {
        vms.push({
          id: match[1].trim(),
          name: match[2].trim(),
          state: match[3].trim(),
        });
      }
    }
    return vms;
  }

  protected async publishDiscovery(): Promise<void> {
    // Discovery will be dynamic based on VMs found.
    // We'll create a sensor for each VM's state and control buttons.
    let vmsToMonitor: VmInfo[] = [];
    try {
      logger.debug(`[${this.featureName}] Discovering VMs with 'virsh list --all'`);
      const virshListOutput = await execute_argv('virsh', ['-c', 'qemu:///system', 'list', '--all']);
      logger.debug(`[${this.featureName}] 'virsh list --all' raw output:`, virshListOutput.stdout);
      if (virshListOutput.stderr) {
        logger.warn(`[${this.featureName}] 'virsh list --all' stderr during discovery:`, virshListOutput.stderr);
      }
      const vms = this.parseVirshList(virshListOutput.stdout);
      logger.debug(`[${this.featureName}] Parsed VMs:`, JSON.stringify(vms, null, 2));

      vmsToMonitor = this.featureConfig.vm_names && this.featureConfig.vm_names.length > 0
        ? vms.filter(vm => this.featureConfig.vm_names!.includes(vm.name))
        : vms;
      
      logger.debug(`[${this.featureName}] VMs to monitor after filtering:`, JSON.stringify(vmsToMonitor, null, 2));

      for (const vm of vmsToMonitor) {
        const vmObjectId = vm.name.replace(/[^a-zA-Z0-9_-]/g, '_');
        const baseName = `VM ${vm.name}`;

        // VM State Sensor
        await this.publishEntityDiscovery('sensor', `${vmObjectId}_state`, {
          name: `${baseName} State Sensor`,
          state_topic: this.prefixTopic(`${this.featureName}/${vmObjectId}/state`),
          icon: 'mdi:server', // or mdi:play, mdi:stop based on state
          entity_category: 'diagnostic' as 'diagnostic',
        });

        // VM Control Buttons (Start, Stop, Restart)
        const actions = ['start', 'stop', 'restart'];
        const icons: { [key: string]: string } = { start: 'mdi:play', stop: 'mdi:stop', restart: 'mdi:restart' };

        for (const action of actions) {
          await this.publishEntityDiscovery('button', `${vmObjectId}_${action}`, {
            name: `${baseName} ${action.charAt(0).toUpperCase() + action.slice(1)} Button`,
            command_topic: this.prefixTopic(`${this.featureName}/${vmObjectId}/command`),
            payload_press: action.toUpperCase(), // e.g., START, STOP, RESTART
            icon: icons[action],
            entity_category: 'config' as 'config',
          });
        }
        // Publish initial state during discovery
        await this.updateVmState(vm.name, vm.state);
      }
    } catch (error: any) {
      const errorMessage = error.message || 'Unknown error during VM discovery';
      logger.error(`Error during VM discovery for ${this.featureName}:`, errorMessage, error.stderr || '');
      // If discovery fails, we might not have VM names to publish error states for.
      // Consider a general error sensor for the VmManager feature itself if needed.
    }
  }
  
  protected async setup(): Promise<void> {
    // Subscribe to command topics for all potential VMs.
    // A more optimized approach might subscribe only to discovered/configured VMs.
    const commandTopicPattern = this.prefixTopic(`${this.featureName}/+/command`); // '+' is wildcard for VM name
    this.mqttClient.subscribe(commandTopicPattern, undefined, (err: Error | null) => {
      if (err) {
        logger.error(`Failed to subscribe to VM command topics (${commandTopicPattern}):`, err);
      }
    });
    this.mqttClient.on('message', this.handleMqttMessage.bind(this));
  }

  private async handleMqttMessage(topic: string, payload: Buffer): Promise<void> {
    const message = payload.toString();
    // Topic structure: .../vm_manager/<vm_object_id>/command
    const topicParts = topic.split('/');
    if (topicParts.length < 3) return; // Not a valid command topic for this feature

    const featureNameSegment = topicParts[topicParts.length - 3];
    const vmObjectIdSegment = topicParts[topicParts.length - 2];
    const commandSegment = topicParts[topicParts.length - 1];

    if (featureNameSegment !== this.featureName || commandSegment !== 'command') {
      return;
    }

    const vmName = vmObjectIdSegment; // Assuming objectId is the VM name for simplicity here
    const action = message.toLowerCase(); // START -> start

    logger.info(`Received command '${action}' for VM '${vmName}'.`);

    // vmName comes from the MQTT topic — validate strictly before it reaches virsh.
    if (!Validators.isVmName(vmName)) {
      rejectInvalid('vm_name', vmName, this.featureName);
      return;
    }

    // NOTE: "--mode acpi" is mandatory for stop/restart. On libvirt 11.x the
    // default shutdown/reboot mode does NOT deliver the ACPI power-button event
    // to a Windows guest without a qemu-guest-agent (it reports "in progress"
    // then nothing happens). "--mode acpi" maps to QMP system_powerdown, which
    // the guest honours (verified 2026-07-17). Guests with qemu-ga could also use
    // "--mode agent"; this VM has no agent channel, so ACPI is the reliable path.
    let virshArgs: string[] = [];
    switch (action) {
      case 'start':
        virshArgs = ['start', vmName];
        break;
      case 'stop': // graceful ACPI power-off (virsh destroy would be forceful)
        virshArgs = ['shutdown', '--mode', 'acpi', vmName];
        break;
      case 'restart':
        virshArgs = ['reboot', '--mode', 'acpi', vmName];
        break;
      default:
        logger.warn(`Unknown action '${action}' for VM '${vmName}'.`);
        return;
    }

    const virshCommand = `virsh -c qemu:///system ${virshArgs.join(' ')}`;
    try {
      logger.info(`Executing: ${virshCommand}`);
      // Passed as argv (no shell) so the VM name cannot inject a command.
      const cmdResult = await execute_argv('virsh', ['-c', 'qemu:///system', ...virshArgs]);
      if (cmdResult.stderr) {
        logger.warn(`[${this.featureName}] Stderr from '${virshCommand}': ${cmdResult.stderr}`);
        // Potentially publish an error attribute or event
      }
      logger.info(`Command '${virshCommand}' executed for VM '${vmName}'. Triggering update. Output: ${cmdResult.stdout}`);
      // Trigger an immediate update to reflect new state
      await this.updateVmState(vmName);
    } catch (error: any) {
      const errorMessage = error.message || 'Unknown error';
      const errorDetails = error.stderr || '';
      logger.error(`Error executing command '${virshCommand}' for VM '${vmName}':`, errorMessage, errorDetails);
      // Publish error state to VM sensor if possible
      const vmObjectId = vmName.replace(/[^a-zA-Z0-9_-]/g, '_');
      await this.publishState(`${this.featureName}/${vmObjectId}/state`, 'error', true);
      await this.publishAttributes(`${this.featureName}/${vmObjectId}/attributes`, { error: `Command failed: ${errorMessage} ${errorDetails}`.trim() }, true);
    }
  }


  protected async update(): Promise<void> {
    let vmsToMonitorNames: string[] = [];
    if (this.featureConfig.vm_names && this.featureConfig.vm_names.length > 0) {
        vmsToMonitorNames = this.featureConfig.vm_names;
    } else {
        // If no specific VMs are configured, discover all and update them
        try {
            logger.debug(`[${this.featureName}] Updating all VMs: fetching list with 'virsh list --all'`);
            const virshListOutput = await execute_argv('virsh', ['-c', 'qemu:///system', 'list', '--all']);
             if (virshListOutput.stderr) {
                logger.warn(`[${this.featureName}] 'virsh list --all' stderr during update:`, virshListOutput.stderr);
            }
            const allVms = this.parseVirshList(virshListOutput.stdout);
            vmsToMonitorNames = allVms.map(vm => vm.name);
        } catch (error: any) {
            const errorMessage = error.message || 'Unknown error listing VMs during update';
            logger.error(`Error listing all VMs during update for ${this.featureName}:`, errorMessage, error.stderr || '');
            return; // Cannot proceed if we can't list VMs
        }
    }
    
    logger.debug(`[${this.featureName}] Scheduled update for VMs: ${vmsToMonitorNames.join(', ')}`);
    for (const vmName of vmsToMonitorNames) {
        // We always fetch the current state in updateVmState if not provided
        await this.updateVmState(vmName);
    }
  }

  private async updateVmState(vmName: string, state?: string): Promise<void> {
    const vmObjectId = vmName.replace(/[^a-zA-Z0-9_-]/g, '_');
    let currentState = state;
    let attributes: { [key: string]: any } = {};

    if (!currentState) { // If state not provided, fetch it
        try {
            logger.debug(`[${this.featureName}] Fetching state for VM ${vmName} with 'virsh dominfo ${vmName}'`);
            // Using 'virsh dominfo' might be more reliable for a single VM's state
            // However, 'virsh list --all' is also fine if we parse it correctly.
            // For simplicity, let's stick to 'virsh list --all' and parse.
            const virshListOutput = await execute_argv('virsh', ['-c', 'qemu:///system', 'list', '--all']);
            if (virshListOutput.stderr && !virshListOutput.stderr.includes("Domain not found")) { // Ignore "Domain not found" if VM was deleted
                logger.warn(`[${this.featureName}] 'virsh list --all' stderr while fetching state for ${vmName}:`, virshListOutput.stderr);
            }
            const vms = this.parseVirshList(virshListOutput.stdout);
            const vmInfo = vms.find(v => v.name === vmName);
            if (vmInfo) {
                currentState = vmInfo.state;
                attributes.id = vmInfo.id; // Add ID as an attribute
            } else {
                logger.warn(`[${this.featureName}] VM ${vmName} not found in 'virsh list --all' during state update. It might have been deleted.`);
                currentState = 'not_found'; // Or 'deleted'
            }
        } catch (error: any) {
            const errorMessage = error.message || 'Unknown error';
            const errorDetails = error.stderr || '';
            logger.error(`Error fetching state for VM ${vmName} (${this.featureName}):`, errorMessage, errorDetails);
            currentState = 'error';
            attributes.error = `Failed to fetch state: ${errorMessage} ${errorDetails}`.trim();
        }
    } else {
        // If state is provided (e.g., from initial discovery), try to get ID
        try {
            const virshListOutput = await execute_argv('virsh', ['-c', 'qemu:///system', 'list', '--all']);
            const vms = this.parseVirshList(virshListOutput.stdout);
            const vmInfo = vms.find(v => v.name === vmName);
            if (vmInfo) attributes.id = vmInfo.id;
        } catch { /* ignore, ID is optional here */ }
    }
    
    logger.debug(`[${this.featureName}] Publishing state for VM ${vmName}: ${currentState}, Attrs: ${JSON.stringify(attributes)}`);
    await this.publishState(`${this.featureName}/${vmObjectId}/state`, currentState || 'unknown', true);
    if (Object.keys(attributes).length > 0) {
        await this.publishAttributes(`${this.featureName}/${vmObjectId}/attributes`, attributes, true);
    }
  }
}
