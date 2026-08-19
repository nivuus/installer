// src/features/ups/UpsMonitor.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import logger from '../../utils/logger';

interface UpsMonitorConfig extends FeatureConfig {
  ups_name: string;
  low_battery_threshold: number;
  critical_battery_threshold: number;
  shutdown_on_critical: boolean;
  shutdown_script: string;
}

interface UpsData {
  status: string;
  batteryCharge: number | null;
  runtimeSeconds: number | null;
  load: number | null;
  inputVoltage: number | null;
  realPowerNominal: number | null;
}

// Status translations from NUT to human-readable
const STATUS_MAP: Record<string, string> = {
  'OL': 'Online',
  'OB': 'On Battery',
  'LB': 'Low Battery',
  'OL CHRG': 'Charging',
  'OB DISCHRG': 'On Battery',
  'OL CHRG LB': 'Charging (Low)',
  'FSD': 'Forced Shutdown',
  'OFF': 'Offline',
};

export class UpsMonitor extends BaseFeature {
  private previousStatus: string = '';
  private lowBatteryAlertSent: boolean = false;
  private shutdownTriggered: boolean = false;

  constructor(mqttClient: MqttClient, featureName: string = 'ups_monitor') {
    super(mqttClient, featureName);
  }

  private get upsConfig(): UpsMonitorConfig {
    return this.featureConfig as UpsMonitorConfig;
  }

  protected async publishDiscovery(): Promise<void> {
    const baseName = 'UPS';
    await Promise.all([
      this.publishEntityDiscovery('sensor', 'battery_level', {
        name: `${baseName} Battery Level`,
        state_topic: `${this.featureName}/battery_level/state`,
        device_class: 'battery',
        unit_of_measurement: '%',
        state_class: 'measurement' as 'measurement',
        icon: 'mdi:battery',
      }),
      this.publishEntityDiscovery('sensor', 'runtime', {
        name: `${baseName} Battery Runtime`,
        state_topic: `${this.featureName}/runtime/state`,
        device_class: 'duration',
        unit_of_measurement: 'min',
        state_class: 'measurement' as 'measurement',
        icon: 'mdi:timer-outline',
      }),
      this.publishEntityDiscovery('sensor', 'status', {
        name: `${baseName} Status`,
        state_topic: `${this.featureName}/status/state`,
        json_attributes_topic: `${this.featureName}/status/attributes`,
        icon: 'mdi:ups',
      }),
      this.publishEntityDiscovery('sensor', 'load', {
        name: `${baseName} Load`,
        state_topic: `${this.featureName}/load/state`,
        unit_of_measurement: '%',
        state_class: 'measurement' as 'measurement',
        icon: 'mdi:flash',
      }),
      this.publishEntityDiscovery('sensor', 'input_voltage', {
        name: `${baseName} Input Voltage`,
        state_topic: `${this.featureName}/input_voltage/state`,
        device_class: 'voltage',
        unit_of_measurement: 'V',
        state_class: 'measurement' as 'measurement',
        icon: 'mdi:sine-wave',
      }),
      this.publishEntityDiscovery('sensor', 'power_consumption', {
        name: `${baseName} Power Consumption`,
        state_topic: `${this.featureName}/power_consumption/state`,
        device_class: 'power',
        unit_of_measurement: 'W',
        state_class: 'measurement' as 'measurement',
        icon: 'mdi:flash-triangle',
      }),
      this.publishEntityDiscovery('binary_sensor', 'power_source', {
        name: `${baseName} Power Source`,
        state_topic: `${this.featureName}/power_source/state`,
        device_class: 'power',
        payload_on: 'ON',
        payload_off: 'OFF',
        icon: 'mdi:power-plug',
      }),
      this.publishEntityDiscovery('button', 'battery_test', {
        name: `${baseName} Battery Test`,
        command_topic: `${this.featureName}/command/battery_test`,
        payload_press: 'TEST',
        icon: 'mdi:battery-heart-variant',
        entity_category: 'config' as 'config',
      }),
    ]);
  }

  protected async setup(): Promise<void> {
    const commandTopic = this.prefixTopic(`${this.featureName}/command/#`);
    await this.mqttClient.subscribe(commandTopic);
    this.mqttClient.on('message', this.handleMqttMessage.bind(this));
  }

  private async handleMqttMessage(topic: string, payload: Buffer): Promise<void> {
    const expectedPrefix = this.prefixTopic(`${this.featureName}/command/`);
    if (!topic.startsWith(expectedPrefix)) return;

    const command = topic.substring(expectedPrefix.length);
    const message = payload.toString();

    if (command === 'battery_test' && message === 'TEST') {
      logger.info(`[${this.featureName}] Battery test requested`);
      const upsName = this.upsConfig.ups_name || 'myups@localhost';
      const result = await execute_argv('upscmd', ['-u', 'admin', '-p', 'admin', upsName, 'test.battery.start']);
      if (result.exitCode === 0) {
        logger.info(`[${this.featureName}] Battery test initiated`);
      } else {
        logger.error(`[${this.featureName}] Battery test failed: ${result.stderr}`);
      }
    }
  }

  protected async update(): Promise<void> {
    const upsName = this.upsConfig.ups_name || 'myups@localhost';
    const data = await this.fetchUpsData(upsName);
    if (!data) return;

    await Promise.all([
      this.publishState(`${this.featureName}/battery_level/state`,
        data.batteryCharge !== null ? data.batteryCharge : 'unavailable', true),
      this.publishState(`${this.featureName}/runtime/state`,
        data.runtimeSeconds !== null ? Math.round(data.runtimeSeconds / 60) : 'unavailable', true),
      this.publishState(`${this.featureName}/status/state`,
        this.translateStatus(data.status), true),
      this.publishAttributes(`${this.featureName}/status/attributes`, {
        raw_status: data.status,
        ups_name: upsName,
      }, true),
      this.publishState(`${this.featureName}/load/state`,
        data.load !== null ? data.load : 'unavailable', true),
      this.publishState(`${this.featureName}/input_voltage/state`,
        data.inputVoltage !== null ? data.inputVoltage : 'unavailable', true),
      this.publishState(`${this.featureName}/power_consumption/state`,
        data.load !== null && data.realPowerNominal !== null
          ? Math.round(data.load * data.realPowerNominal / 100)
          : 'unavailable', true),
      // ON = mains power present (OL), OFF = on battery
      this.publishState(`${this.featureName}/power_source/state`,
        data.status.includes('OL') ? 'ON' : 'OFF', true),
    ]);

    await this.checkAlerts(data);
    this.previousStatus = data.status;
  }

  private async fetchUpsData(upsName: string): Promise<UpsData | null> {
    const result = await execute_argv('upsc', [upsName]);
    if (result.exitCode !== 0) {
      logger.error(`[${this.featureName}] Failed to query UPS: ${result.stderr}`);
      await this.publishState(`${this.featureName}/status/state`, 'unavailable', true);
      return null;
    }

    const lines = result.stdout.split('\n');
    const vars: Record<string, string> = {};
    for (const line of lines) {
      const colonIdx = line.indexOf(':');
      if (colonIdx === -1) continue;
      vars[line.substring(0, colonIdx).trim()] = line.substring(colonIdx + 1).trim();
    }

    return {
      status: vars['ups.status'] || 'UNKNOWN',
      batteryCharge: this.parseFloat(vars['battery.charge']),
      runtimeSeconds: this.parseFloat(vars['battery.runtime']),
      load: this.parseFloat(vars['ups.load']),
      inputVoltage: this.parseFloat(vars['input.voltage']),
      realPowerNominal: this.parseFloat(vars['ups.realpower.nominal']),
    };
  }

  private parseFloat(value: string | undefined): number | null {
    if (!value) return null;
    const num = Number.parseFloat(value);
    return Number.isNaN(num) ? null : num;
  }

  private translateStatus(raw: string): string {
    return STATUS_MAP[raw] || raw;
  }

  private async checkAlerts(data: UpsData): Promise<void> {
    const wasOnLine = this.previousStatus.includes('OL') || this.previousStatus === '';
    const isOnBattery = data.status.includes('OB');
    const isOnLine = data.status.includes('OL');

    // Power loss: OL → OB
    if (wasOnLine && isOnBattery && this.previousStatus !== '') {
      await this.sendAlert('Power loss detected — running on battery', 'warning', {
        battery: data.batteryCharge,
        runtime_min: data.runtimeSeconds ? Math.round(data.runtimeSeconds / 60) : null,
      });
    }

    // Power restored: OB → OL
    if (this.previousStatus.includes('OB') && isOnLine) {
      await this.sendAlert('Power restored — back on mains', 'info', {
        battery: data.batteryCharge,
      });
      this.lowBatteryAlertSent = false;
    }

    if (data.batteryCharge === null) return;

    const lowThreshold = this.upsConfig.low_battery_threshold ?? 30;
    const criticalThreshold = this.upsConfig.critical_battery_threshold ?? 10;

    // Low battery warning (only while on battery, once)
    if (isOnBattery && data.batteryCharge <= lowThreshold && !this.lowBatteryAlertSent) {
      await this.sendAlert(`Battery low: ${data.batteryCharge}%`, 'warning', {
        battery: data.batteryCharge,
        runtime_min: data.runtimeSeconds ? Math.round(data.runtimeSeconds / 60) : null,
      });
      this.lowBatteryAlertSent = true;
    }

    // Critical battery → trigger shutdown
    if (isOnBattery && data.batteryCharge <= criticalThreshold && !this.shutdownTriggered) {
      await this.sendAlert(`Critical battery: ${data.batteryCharge}% — initiating shutdown`, 'critical', {
        battery: data.batteryCharge,
      });
      if (this.upsConfig.shutdown_on_critical) {
        this.shutdownTriggered = true;
        await this.triggerShutdown();
      }
    }
  }

  private async sendAlert(message: string, severity: string, data: any): Promise<void> {
    const alertTopic = this.prefixTopic('alert');
    const payload = JSON.stringify({
      timestamp: new Date().toISOString(),
      message,
      severity,
      source: this.featureName,
      data,
    });
    await this.mqttClient.publish(alertTopic, payload, { retain: false, qos: 1 });
    logger.info(`[${this.featureName}] Alert (${severity}): ${message}`);
  }

  private async triggerShutdown(): Promise<void> {
    const script = this.upsConfig.shutdown_script || '/opt/mqtt-system-agent/scripts/ups-shutdown.sh';
    logger.warn(`[${this.featureName}] Triggering graceful shutdown via ${script}`);
    const result = await execute_argv('sudo', [script]);
    if (result.exitCode !== 0) {
      logger.error(`[${this.featureName}] Shutdown script failed: ${result.stderr}`);
    }
  }
}
