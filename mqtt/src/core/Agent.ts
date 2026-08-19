// src/core/Agent.ts

import { MqttClient as MqttClientInterface, AgentConfig, DeviceInfo, WebSocketConfig } from './types';
import { getConfigManager } from '../config';
import logger from '../utils/logger';
import { BaseFeature } from './BaseFeature';
import { MqttClient } from '../mqtt/MqttClient'; // Import the actual MqttClient
import { WebSocketServer, WizardHandler } from '../websocket';

// Feature imports
import { CpuTemperature } from '../features/cpu/CpuTemperature';
import { CpuLoad } from '../features/cpu/CpuLoad';
import { MemoryUsage } from '../features/memory/MemoryUsage';
import { NetworkStats } from '../features/network/NetworkStats';
import { DiskUsage } from '../features/disk/DiskUsage';
import { AptUpdates } from '../features/updates/AptUpdates';
import { DockerUpdates } from '../features/updates/DockerUpdates';
import { FirewallManager } from '../features/firewall/FirewallManager';
import { HostapdManager } from '../features/wifi/HostapdManager';
import { VmManager } from '../features/vm/VmManager';
import { ConnectedDevices } from '../features/network/ConnectedDevices';
import { PppoeCredentials } from '../features/network/PppoeCredentials';
import { SmartStatus } from '../features/disk/SmartStatus';
import { EventMonitor } from '../features/events/EventMonitor';
import { GpuMonitoring } from '../features/gpu/GpuMonitoring';
import { MotherboardTemperature } from '../features/motherboard/MotherboardTemperature';
import { SystemdServices } from '../features/services/SystemdServices';
import { StreamingStatus } from '../features/streaming/StreamingStatus';
import { KvmSplitLockMonitor } from '../features/vm/KvmSplitLockMonitor';
import { WindowsDiskUsage } from '../features/disk/WindowsDiskUsage';
import { UpsMonitor } from '../features/ups/UpsMonitor';
import { HardwareHealth } from '../features/health/HardwareHealth';

type FeatureConstructor = new (mqttClient: MqttClientInterface, featureName: string) => BaseFeature;

// A map to hold feature constructors.
const availableFeatures: { [key: string]: FeatureConstructor } = {
  cpu_temperature: CpuTemperature,
  cpu_load: CpuLoad,
  memory_usage: MemoryUsage,
  network_stats: NetworkStats,
  disk_usage: DiskUsage,
  apt_updates: AptUpdates,
  docker_updates: DockerUpdates,
  firewalld_manager: FirewallManager,
  hostapd_manager: HostapdManager,
  vm_manager: VmManager,
  connected_devices: ConnectedDevices,
  pppoe_credentials: PppoeCredentials,
  smart_status: SmartStatus,
  event_monitor: EventMonitor,
  gpu_monitoring: GpuMonitoring,
  motherboard_temperature: MotherboardTemperature,
  systemd_services: SystemdServices,
  streaming_status: StreamingStatus,
  kvm_split_lock_monitor: KvmSplitLockMonitor,
  windows_disk_usage: WindowsDiskUsage,
  ups_monitor: UpsMonitor,
  hardware_health: HardwareHealth,
};

export class Agent {
  private readonly config: AgentConfig;
  private readonly deviceInfo: DeviceInfo;
  private mqttClient!: MqttClientInterface; // To be initialized in start()
  private features: BaseFeature[] = [];
  private readonly availabilityTopic: string;
  private readonly payloadAvailable: string = 'online';
  private readonly payloadNotAvailable: string = 'offline';
  private shuttingDown: boolean = false;

  // WebSocket server for Wizard API
  private webSocketServer: WebSocketServer | null = null;
  private wizardHandler: WizardHandler | null = null;

  constructor() {
    this.config = getConfigManager().config;
    this.deviceInfo = this.config.device_info;
    // Construct the agent's own availability topic
    this.availabilityTopic = `${this.config.mqtt.base_topic}/${this.deviceInfo.identifiers[0]}/status`;

    this.setupSignalHandlers();
  }

  private initializeMqttClient(): void {
    this.mqttClient = new MqttClient(); // Instantiate our MqttClient wrapper
    // The MqttClient constructor now handles reading options from configManager.
  }

  private initializeFeatures(): void {
    logger.info('Initializing features...');
    for (const featureName in this.config.features) {
      if (this.config.features[featureName]?.enabled) {
        const FeatureClass = availableFeatures[featureName];
        if (FeatureClass) {
          try {
            const featureInstance = new FeatureClass(this.mqttClient, featureName);
            this.features.push(featureInstance);
            logger.info(`Feature ${featureName} initialized.`);
          } catch (error) {
            logger.error(`Error initializing feature ${featureName}:`, error);
          }
        } else {
          logger.warn(`Feature ${featureName} is enabled in config but not available in the agent.`);
        }
      }
    }
  }

  private initializeWebSocketServer(): void {
    const wsConfig = this.config.websocket;
    if (!wsConfig || !wsConfig.enabled) {
      logger.info('WebSocket server is disabled in config');
      return;
    }

    // Handle environment variable for token
    let token = wsConfig.auth?.token;
    if (token?.startsWith('${') && token?.endsWith('}')) {
      const envVar = token.slice(2, -1);
      token = process.env[envVar] || token;
    }

    const webSocketConfig: WebSocketConfig = {
      enabled: wsConfig.enabled,
      port: wsConfig.port || 8765,
      host: wsConfig.host || '0.0.0.0',
      auth: {
        type: wsConfig.auth?.type || 'token',
        token: token,
      },
    };

    this.wizardHandler = new WizardHandler();
    this.webSocketServer = new WebSocketServer(webSocketConfig, this.wizardHandler);
    this.webSocketServer.start();
  }

  public async start(): Promise<void> {
    logger.info('Starting agent...');
    this.initializeMqttClient(); // Initialize (or mock) MQTT client
    this.initializeWebSocketServer(); // Initialize WebSocket server for Wizard API

    // Setup MQTT event handlers
    this.mqttClient.on('connect', async () => {
      logger.info(`Connected to MQTT broker at ${this.config.mqtt.host}:${this.config.mqtt.port}`);
      await this.mqttClient.publish(this.availabilityTopic, this.payloadAvailable, { qos: 1, retain: true });
      // Inline Home Assistant MQTT discovery for alerts and events
      try {
        const cfg = this.config;
        const base = cfg.mqtt.base_topic;
        const device = {
          identifiers: cfg.device_info.identifiers,
          name: cfg.device_info.name,
          model: cfg.device_info.model,
          manufacturer: cfg.device_info.manufacturer,
          sw_version: cfg.device_info.sw_version,
        };
        for (const deviceId of cfg.device_info.identifiers) {
          // Per-id Alert discovery
          const alertConfigTopic = `homeassistant/sensor/${deviceId}/alert/config`;
          const alertStateTopic = `${base}/${deviceId}/alert`;
          const alertConfig = { name: `System Alert Sensor`, state_topic: alertStateTopic, json_attributes_topic: alertStateTopic, value_template: "{{ value_json.message }}", unique_id: `${deviceId}_alert`, device, icon: "mdi:alert" };
          await this.mqttClient.publish(alertConfigTopic, JSON.stringify(alertConfig), { qos: 1, retain: true });
          // Per-id Event discovery
          const eventConfigTopic = `homeassistant/sensor/${deviceId}/event/config`;
          const eventStateTopic = `${base}/${deviceId}/event`;
          const eventConfig = { name: `System Event Sensor`, state_topic: eventStateTopic, json_attributes_topic: eventStateTopic, value_template: "{{ value_json.type }}", unique_id: `${deviceId}_event`, device, icon: "mdi:bell" };
          await this.mqttClient.publish(eventConfigTopic, JSON.stringify(eventConfig), { qos: 1, retain: true });
        }
        logger.info('HA discovery inline for alerts and events published for all ids.');
      } catch (err) {
        logger.error('HA discovery inline failed:', err);
      }
      
      logger.info(`Agent status published: ${this.payloadAvailable}`);
      
      // Stop all features before restarting them (prevents duplicate timers/watchers on reconnect)
      logger.info('Stopping all features before (re)starting...');
      for (const feature of this.features) {
        try {
          await feature.stop();
        } catch (error) {
          logger.error(`Error stopping feature ${feature.featureName} before restart:`, error);
        }
      }

      // Start all initialized features
      logger.info('Starting all enabled features...');
      for (const feature of this.features) {
        try {
          await feature.start();
        } catch (error) {
          logger.error(`Error starting feature ${feature.featureName}:`, error);
        }
      }
      logger.info('All enabled features started.');
    });

    this.mqttClient.on('error', (error: Error) => {
      logger.error('MQTT client error:', error);
    });

    this.mqttClient.on('close', () => {
      logger.info('Disconnected from MQTT broker.');
      // Reconnection logic should ideally be handled within the MqttClient implementation itself.
      // If not, it could be initiated from here.
    });

    this.mqttClient.on('offline', () => {
        logger.info('MQTT client is offline.');
    });

    this.mqttClient.on('reconnect', () => {
        logger.info('MQTT client is attempting to reconnect...');
    });

    // Initialize features after MQTT client setup but before connecting
    // This allows features to prepare (e.g. for subscriptions) if needed
    this.initializeFeatures();

    // Actual connection attempt
    this.mqttClient.connect().catch(error => {
      logger.error("Failed to connect to MQTT on agent start:", error);
      // Depending on desired behavior, we might want to retry or exit.
      // For now, MqttClient's internal reconnect logic will handle retries.
    });
    logger.info('Agent start sequence initiated, MQTT connection attempt in progress.');
  }

  public async stop(): Promise<void> {
    if (this.shuttingDown) {
      logger.warn('Shutdown already in progress.');
      return;
    }
    this.shuttingDown = true;
    logger.info('Stopping agent...');

    // Stop WebSocket server
    if (this.webSocketServer) {
      try {
        await this.webSocketServer.stop();
      } catch (error) {
        logger.error('Error stopping WebSocket server:', error);
      }
    }

    // Stop all features
    for (const feature of this.features) {
      try {
        await feature.stop();
      } catch (error) {
        logger.error(`Error stopping feature ${feature.featureName}:`, error);
      }
    }
    logger.info('All features stopped.');

    // Publish offline status and disconnect MQTT
    if (this.mqttClient && this.mqttClient.connected) { // Check if connected before trying to publish/end
      try {
        // Use a synchronous publish or handle promise if publish is async in MqttClient
        this.mqttClient.publish(this.availabilityTopic, this.payloadNotAvailable, { qos: 1, retain: true });
        logger.info(`Agent status published: ${this.payloadNotAvailable}`);
        this.mqttClient.end(true); // Force close
      } catch (error) {
        logger.error('Error during MQTT cleanup:', error);
      }
    } else if (this.mqttClient) { // If client exists but not connected, still try to end it.
        this.mqttClient.end(true);
    }
    logger.info('Agent stopped.');
    process.exit(0);
  }

  private setupSignalHandlers(): void {
    process.on('SIGINT', async () => {
      logger.info('SIGINT received. Shutting down gracefully...');
      await this.stop();
    });
    process.on('SIGTERM', async () => {
      logger.info('SIGTERM received. Shutting down gracefully...');
      await this.stop();
    });
    process.on('uncaughtException', async (error) => {
        logger.error('Uncaught Exception:', error);
        // Optionally try to gracefully stop, but be aware the state might be corrupted.
        // await this.stop();
        process.exit(1); // Exit with error
    });
    process.on('unhandledRejection', async (reason, promise) => {
        logger.error('Unhandled Rejection at:', promise, 'reason:', reason);
        // Optionally try to gracefully stop.
        // await this.stop();
        process.exit(1); // Exit with error
    });
  }
}
