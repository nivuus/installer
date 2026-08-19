// src/features/services/SystemdServices.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, HaDiscoveryPayload } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import { Validators, rejectInvalid } from '../../utils/validators';
import logger from '../../utils/logger';
import * as fs from 'fs';

const DEBUG_LOG = '/tmp/mqtt-agent-debug.log';
function debugLog(msg: string) {
  const timestamp = new Date().toISOString();
  fs.appendFileSync(DEBUG_LOG, `[${timestamp}] [systemd_services] ${msg}\n`);
}

interface ServiceStatus {
  name: string;
  active: boolean;
  state: string; // active, failed, inactive
}

/**
 * Systemd Services Monitoring
 * Monitors critical system services and provides control
 */
export class SystemdServices extends BaseFeature {
  private services: string[] = [
    'docker',
    'libvirtd',
    'firewalld',
    'hostapd',
    'NetworkManager',
    'mosquitto',
  ];

  private serviceStatus: Map<string, ServiceStatus> = new Map();

  async publishDiscovery(): Promise<void> {
    debugLog('publishDiscovery() called');
    // Create sensor for each service
    for (const serviceName of this.services) {
      const serviceId = serviceName.toLowerCase().replace(/[^a-z0-9]/g, '_');

      // Status sensor
      const statusPayload: HaDiscoveryPayload = {
        unique_id: `${this.deviceInfo.identifiers[0]}_service_${serviceId}_status`,
        name: `System Service ${serviceName} Status Sensor`,
        state_topic: this.prefixTopic(`${this.featureName}/${serviceId}/state`),
        json_attributes_topic: this.prefixTopic(`${this.featureName}/${serviceId}/attributes`),
        availability_topic: this.availabilityTopic,
        payload_available: this.payloadAvailable,
        payload_not_available: this.payloadNotAvailable,
        device: this.deviceInfo,
        icon: 'mdi:cog',
        entity_category: 'diagnostic',
      };

      const discoveryTopic = `homeassistant/sensor/${this.deviceInfo.identifiers[0]}/${statusPayload.unique_id}/config`;
      debugLog(`Publishing discovery for ${serviceName} status to ${discoveryTopic}`);
      await this.mqttClient.publish(discoveryTopic, JSON.stringify(statusPayload), { qos: 1, retain: true });
      debugLog(`Discovery published for ${serviceName} status`);

      // Restart button
      const restartPayload: HaDiscoveryPayload = {
        unique_id: `${this.deviceInfo.identifiers[0]}_service_${serviceId}_restart`,
        name: `System Service ${serviceName} Restart Button`,
        command_topic: this.prefixTopic(`${this.featureName}/${serviceId}/restart`),
        availability_topic: this.availabilityTopic,
        payload_available: this.payloadAvailable,
        payload_not_available: this.payloadNotAvailable,
        device: this.deviceInfo,
        icon: 'mdi:restart',
        entity_category: 'config',
      };

      const restartDiscoveryTopic = `homeassistant/button/${this.deviceInfo.identifiers[0]}/${restartPayload.unique_id}/config`;
      debugLog(`Publishing discovery for ${serviceName} restart button to ${restartDiscoveryTopic}`);
      await this.mqttClient.publish(restartDiscoveryTopic, JSON.stringify(restartPayload), { qos: 1, retain: true });
      debugLog(`Discovery published for ${serviceName} restart button`);
    }
    debugLog('publishDiscovery() completed');
  }

  async update(): Promise<void> {
    debugLog('update() called');
    try {
      for (const serviceName of this.services) {
        debugLog(`Checking service: ${serviceName}`);
        await this.checkService(serviceName);
        debugLog(`Checked service: ${serviceName}`);
      }
    } catch (error) {
      debugLog(`Error in update(): ${error}`);
      logger.error('Error updating systemd services:', error);
    }
    debugLog('update() completed');
  }

  private async checkService(serviceName: string): Promise<void> {
    debugLog(`checkService(${serviceName}) called`);
    try {
      debugLog(`Executing systemctl is-active ${serviceName}.service`);
      const unit = `${serviceName}.service`;
      if (!Validators.isSystemdUnit(unit)) {
        rejectInvalid('systemd_unit', unit, 'systemd_services');
        return;
      }
      const result = await execute_argv('systemctl', ['is-active', unit]);
      const serviceId = serviceName.toLowerCase().replace(/[^a-z0-9]/g, '_');

      const state = result.stdout?.trim() || 'unknown';
      const isActive = state === 'active';
      debugLog(`Service ${serviceName}: state=${state}, isActive=${isActive}`);

      this.serviceStatus.set(serviceName, {
        name: serviceName,
        active: isActive,
        state: state,
      });

      // Publish state
      const stateTopic = this.prefixTopic(`${this.featureName}/${serviceId}/state`);
      const stateValue = isActive ? 'active' : state;
      debugLog(`Publishing state for ${serviceName}: ${stateValue} to ${stateTopic}`);
      await this.mqttClient.publish(
        stateTopic,
        stateValue,
        { qos: 0, retain: true }
      );
      debugLog(`Published state for ${serviceName}`);

      // Publish attributes
      const attributes = {
        service: serviceName,
        state: state,
        active: isActive,
      };

      const attrTopic = this.prefixTopic(`${this.featureName}/${serviceId}/attributes`);
      debugLog(`Publishing attributes for ${serviceName} to ${attrTopic}`);
      await this.mqttClient.publish(
        attrTopic,
        JSON.stringify(attributes),
        { qos: 0, retain: true }
      );
      debugLog(`Published attributes for ${serviceName}`);
    } catch (error) {
      debugLog(`Error checking service ${serviceName}: ${error}`);
      logger.error(`Error checking service ${serviceName}:`, error);
    }
  }

  async setup(): Promise<void> {
    debugLog('setup() called');
    for (const serviceName of this.services) {
      const serviceId = serviceName.toLowerCase().replace(/[^a-z0-9]/g, '_');
      const restartTopic = this.prefixTopic(`${this.featureName}/${serviceId}/restart`);

      debugLog(`Subscribing to ${restartTopic}`);
      this.mqttClient.subscribe(restartTopic, { qos: 1 });
      debugLog(`Subscribed to ${restartTopic}`);
    }

    this.mqttClient.on('message', async (topic: string, message: Buffer) => {
      const topicStr = topic.toString();

      for (const serviceName of this.services) {
        const serviceId = serviceName.toLowerCase().replace(/[^a-z0-9]/g, '_');
        const restartTopic = this.prefixTopic(`${this.featureName}/${serviceId}/restart`);

        if (topicStr === restartTopic) {
          await this.restartService(serviceName);
        }
      }
    });
    debugLog('setup() completed');
  }

  private async restartService(serviceName: string): Promise<void> {
    logger.info(`Restarting service: ${serviceName}`);

    try {
      const unit = `${serviceName}.service`;
      if (!Validators.isSystemdUnit(unit)) {
        rejectInvalid('systemd_unit', unit, 'systemd_services');
        return;
      }
      const result = await execute_argv('sudo', ['systemctl', 'restart', unit]);

      if (result.exitCode === 0) {
        logger.info(`Service ${serviceName} restarted successfully`);
        // Wait a bit before checking status
        await new Promise(resolve => setTimeout(resolve, 2000));
        await this.checkService(serviceName);
      } else {
        logger.error(`Failed to restart service ${serviceName}:`, result.stderr);
      }
    } catch (error) {
      logger.error(`Error restarting service ${serviceName}:`, error);
    }
  }

  async cleanup(): Promise<void> {
    // Unsubscribe from topics if needed
  }
}
