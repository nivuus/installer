// src/features/motherboard/MotherboardTemperature.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, HaDiscoveryPayload } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import logger from '../../utils/logger';

interface MotherboardSensors {
  systemTemp: number | null;      // SYSTIN
  cpuTemp: number | null;          // CPUTIN
  pchTemp: number | null;          // PCH_CHIP_TEMP
  nvmeTemp: number | null;         // NVMe Composite
  fan1Speed: number | null;        // fan1 RPM
  fan2Speed: number | null;        // fan2 RPM
}

/**
 * Motherboard Temperature & Fan monitoring using lm-sensors
 */
export class MotherboardTemperature extends BaseFeature {
  private sensors: MotherboardSensors = {
    systemTemp: null,
    cpuTemp: null,
    pchTemp: null,
    nvmeTemp: null,
    fan1Speed: null,
    fan2Speed: null,
  };

  async publishDiscovery(): Promise<void> {
    const entities = [
      {
        id: 'system_temperature',
        name: 'Motherboard System Temperature Sensor',
        unit: '°C',
        icon: 'mdi:thermometer',
        deviceClass: 'temperature',
      },
      {
        id: 'cpu_socket_temperature',
        name: 'Motherboard CPU Socket Temperature Sensor',
        unit: '°C',
        icon: 'mdi:thermometer',
        deviceClass: 'temperature',
      },
      {
        id: 'pch_temperature',
        name: 'Motherboard PCH Chipset Temperature Sensor',
        unit: '°C',
        icon: 'mdi:thermometer',
        deviceClass: 'temperature',
      },
      {
        id: 'nvme_temperature',
        name: 'Motherboard NVMe Temperature Sensor',
        unit: '°C',
        icon: 'mdi:thermometer',
        deviceClass: 'temperature',
      },
      {
        id: 'chassis_fan1',
        name: 'Motherboard Chassis Fan 1 Sensor',
        unit: 'RPM',
        icon: 'mdi:fan',
      },
      {
        id: 'chassis_fan2',
        name: 'Motherboard Chassis Fan 2 Sensor',
        unit: 'RPM',
        icon: 'mdi:fan',
      },
    ];

    for (const entity of entities) {
      const payload: HaDiscoveryPayload = {
        unique_id: `${this.deviceInfo.identifiers[0]}_${entity.id}`,
        name: `${entity.name}`,
        state_topic: this.prefixTopic(`${this.featureName}/${entity.id}/state`),
        availability_topic: this.availabilityTopic,
        payload_available: this.payloadAvailable,
        payload_not_available: this.payloadNotAvailable,
        device: this.deviceInfo,
        icon: entity.icon,
        entity_category: 'diagnostic',
      };

      if (entity.unit) {
        payload.unit_of_measurement = entity.unit;
      }
      if (entity.deviceClass) {
        payload.device_class = entity.deviceClass;
      }

      const discoveryTopic = `homeassistant/sensor/${this.deviceInfo.identifiers[0]}/${payload.unique_id}/config`;
      await this.mqttClient.publish(discoveryTopic, JSON.stringify(payload), { qos: 1, retain: true });
    }
  }

  async update(): Promise<void> {
    try {
      const result = await execute_argv('sensors', ['-A']);


      if (result.exitCode !== 0 || !result.stdout) {
        logger.error('Failed to read sensors');
        return;
      }

      const output = result.stdout;

      // Parse nct6798 sensor data
      this.sensors.systemTemp = this.extractValue(output, /SYSTIN:\s+\+?([\d.-]+)°C/);
      this.sensors.cpuTemp = this.extractValue(output, /CPUTIN:\s+\+?([\d.-]+)°C/);
      this.sensors.pchTemp = this.extractValue(output, /PCH_CHIP_TEMP:\s+\+?([\d.-]+)°C/);
      this.sensors.fan1Speed = this.extractValue(output, /fan1:\s+([\d]+)\s+RPM/, 10000); // Max 10000 RPM
      this.sensors.fan2Speed = this.extractValue(output, /fan2:\s+([\d]+)\s+RPM/, 10000); // Max 10000 RPM

      // Parse NVMe temperature
      this.sensors.nvmeTemp = this.extractValue(output, /nvme-pci-[\w]+[\s\S]*?Composite:\s+\+?([\d.-]+)°C/);


      await this.publishSensors();
    } catch (error) {
      logger.error('Error updating motherboard sensors:', error);
    }
  }

  private extractValue(text: string, regex: RegExp, maxValue: number = 150): number | null {
    const match = text.match(regex);
    if (match && match[1]) {
      const value = parseFloat(match[1]);
      // Ignore invalid readings (e.g., negative temps, 0°C, or unreasonably high values)
      if (value > 0 && value < maxValue) {
        return value;
      }
    }
    return null;
  }

  private async publishSensors(): Promise<void> {
    const sensors = [
      { id: 'system_temperature', value: this.sensors.systemTemp },
      { id: 'cpu_socket_temperature', value: this.sensors.cpuTemp },
      { id: 'pch_temperature', value: this.sensors.pchTemp },
      { id: 'nvme_temperature', value: this.sensors.nvmeTemp },
      { id: 'chassis_fan1', value: this.sensors.fan1Speed },
      { id: 'chassis_fan2', value: this.sensors.fan2Speed },
    ];

    for (const sensor of sensors) {
      const stateValue = sensor.value !== null ? sensor.value.toFixed(1) : 'unknown';
      const topic = this.prefixTopic(`${this.featureName}/${sensor.id}/state`);
      await this.mqttClient.publish(
        topic,
        stateValue,
        { qos: 0, retain: true }
      );
    }
  }

  async setup(): Promise<void> {
    // No setup needed
  }

  async cleanup(): Promise<void> {
    // No cleanup needed
  }
}
