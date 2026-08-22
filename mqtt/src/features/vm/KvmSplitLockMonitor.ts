// src/features/vm/KvmSplitLockMonitor.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import logger from '../../utils/logger';
import { spawn, ChildProcess } from 'child_process';

interface SplitLockEvent {
  timestamp: string;
  cpu: string;
  process: string;
  pid: string;
  address: string;
}

export class KvmSplitLockMonitor extends BaseFeature {
  private journalProcess: ChildProcess | null = null;
  private splitLockCount: number = 0;
  private lastSplitLockEvent: SplitLockEvent | null = null;
  private shuttingDown: boolean = false;
  private readonly monitorCommand = 'journalctl -kf --no-pager';

  constructor(mqttClient: MqttClient, featureName: string = 'kvm_split_lock_monitor') {
    super(mqttClient, featureName);
  }

  protected async publishDiscovery(): Promise<void> {
    const baseName = `VM Split Lock`;

    // Split Lock Count Sensor
    await this.publishEntityDiscovery('sensor', 'split_lock_count', {
      name: `${baseName} Count Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/count/state`),
      icon: 'mdi:alert-circle',
      entity_category: 'diagnostic' as 'diagnostic',
      state_class: 'total_increasing' as 'total_increasing',
    });

    // Last Split Lock Event Sensor
    await this.publishEntityDiscovery('sensor', 'split_lock_last_event', {
      name: `${baseName} Last Event Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/last_event/state`),
      json_attributes_topic: this.prefixTopic(`${this.featureName}/last_event/attributes`),
      icon: 'mdi:alert-circle-outline',
      entity_category: 'diagnostic' as 'diagnostic',
    });

    // Binary Sensor for Active Split Lock Detection
    await this.publishEntityDiscovery('binary_sensor', 'split_lock_active', {
      name: `${baseName} Active Sensor`,
      state_topic: this.prefixTopic(`${this.featureName}/active/state`),
      payload_on: 'ON',
      payload_off: 'OFF',
      device_class: 'problem' as 'problem',
      entity_category: 'diagnostic' as 'diagnostic',
    });

    // Publish initial states
    await this.publishState(`${this.featureName}/count/state`, this.splitLockCount.toString());
    await this.publishState(`${this.featureName}/last_event/state`, 'none');
    await this.publishState(`${this.featureName}/active/state`, 'OFF');
  }

  protected async setup(): Promise<void> {
    await super.setup();
    this.shuttingDown = false;
    this.stopMonitoring();
    this.startMonitoring();
  }

  private parseSplitLockMessage(message: string): SplitLockEvent | null {
    // Parse messages like: "x86/split lock detection: #AC: CPU 5/KVM/3495 took a split_lock trap at address: 0xfffff8040c6dfdff"
    const regex = /x86\/split lock detection: #AC: CPU (\d+)\/(.*?)\/(\d+) took a split_lock trap at address: (0x[0-9a-fA-F]+)/;
    const match = message.match(regex);

    if (match) {
      return {
        timestamp: new Date().toISOString(),
        cpu: match[1],
        process: match[2],
        pid: match[3],
        address: match[4],
      };
    }
    return null;
  }

  private async handleSplitLockEvent(event: SplitLockEvent): Promise<void> {
    this.splitLockCount++;
    this.lastSplitLockEvent = event;

    logger.warn(
      `[${this.featureName}] Split lock detected! CPU ${event.cpu}, Process: ${event.process}, PID: ${event.pid}, Address: ${event.address}`
    );

    // Publish updated count
    await this.publishState(`${this.featureName}/count/state`, this.splitLockCount.toString(), true);

    // Publish last event details
    await this.publishState(`${this.featureName}/last_event/state`, event.timestamp, true);
    await this.publishAttributes(`${this.featureName}/last_event/attributes`, {
      cpu: event.cpu,
      process: event.process,
      pid: event.pid,
      address: event.address,
      timestamp: event.timestamp,
    }, true);

    // Temporarily activate the binary sensor (will be turned off in next update cycle)
    await this.publishState(`${this.featureName}/active/state`, 'ON', true);

    // Send alert through agent's alert system
    try {
      await this.sendAlert(
        `KVM Split Lock Detected`,
        `warning`,
        {
          cpu: event.cpu,
          process: event.process,
          pid: event.pid,
          address: event.address,
        }
      );
    } catch (error: any) {
      logger.error(`[${this.featureName}] Failed to send alert:`, error.message);
    }
  }

  private async sendAlert(message: string, severity: string, data: any): Promise<void> {
    // Use the agent's alert mechanism
    const alertTopic = this.prefixTopic('alert');
    const payload = JSON.stringify({
      timestamp: new Date().toISOString(),
      message,
      severity,
      data,
    });
    await this.mqttClient.publish(alertTopic, payload, { retain: false });
  }

  private startMonitoring(): void {
    if (this.journalProcess) {
      logger.warn(`[${this.featureName}] Monitoring already started, skipping.`);
      return;
    }

    logger.info(`[${this.featureName}] Starting kernel log monitoring for split locks`);

    // Start journalctl to follow kernel logs
    this.journalProcess = spawn('journalctl', ['-kf', '--no-pager'], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    this.journalProcess.stdout?.on('data', (data: Buffer) => {
      const lines = data.toString().split('\n');
      for (const line of lines) {
        if (line.includes('split lock detection') || line.includes('split_lock trap')) {
          const event = this.parseSplitLockMessage(line);
          if (event) {
            this.handleSplitLockEvent(event).catch((error) => {
              logger.error(`[${this.featureName}] Error handling split lock event:`, error.message);
            });
          }
        }
      }
    });

    this.journalProcess.stderr?.on('data', (data: Buffer) => {
      logger.debug(`[${this.featureName}] journalctl stderr:`, data.toString());
    });

    this.journalProcess.on('error', (error) => {
      logger.error(`[${this.featureName}] journalctl process error:`, error.message);
      this.journalProcess = null;
    });

    this.journalProcess.on('exit', (code) => {
      logger.warn(`[${this.featureName}] journalctl process exited with code ${code}`);
      this.journalProcess = null;
      if (this.shuttingDown) return;
      // Restart monitoring after a delay
      setTimeout(() => this.startMonitoring(), 5000);
    });
  }

  private stopMonitoring(): void {
    if (this.journalProcess) {
      logger.info(`[${this.featureName}] Stopping kernel log monitoring`);
      this.journalProcess.kill();
      this.journalProcess = null;
    }
  }

  protected async update(): Promise<void> {
    try {
      // Deactivate the binary sensor (will be reactivated if new event occurs)
      await this.publishState(`${this.featureName}/active/state`, 'OFF', true);

      // Check current split lock detection setting
      try {
        const result = await execute_argv('cat', ['/sys/module/kernel/parameters/split_lock_detect']);
        const setting = result.exitCode === 0 ? result.stdout.trim() : 'not_available';

        logger.debug(`[${this.featureName}] Current split_lock_detect setting: ${setting}`);

        // Update attributes with current setting if we have a last event
        if (this.lastSplitLockEvent) {
          await this.publishAttributes(`${this.featureName}/last_event/attributes`, {
            ...this.lastSplitLockEvent,
            split_lock_detect_setting: setting,
          }, true);
        }
      } catch (error: any) {
        logger.debug(`[${this.featureName}] Could not read split_lock_detect setting:`, error.message);
      }
    } catch (error: any) {
      logger.error(`[${this.featureName}] Error during update:`, error.message);
    }
  }

  protected async cleanup(): Promise<void> {
    this.shuttingDown = true;
    this.stopMonitoring();
    await super.cleanup();
  }
}
