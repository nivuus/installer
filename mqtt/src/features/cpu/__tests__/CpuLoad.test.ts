// src/features/cpu/__tests__/CpuLoad.test.ts

import { CpuLoad } from '../CpuLoad';
import { MockMqttClient } from '../../../mqtt/__tests__/mocks/MockMqttClient';
// configManager is mocked below, so we don't need a direct import for it here for the test logic itself.
// However, if CpuLoad or its dependencies internally call initializeConfigManager or getConfigManager,
// we need to ensure the mock setup is compatible.
import * as si from 'systeminformation';

jest.mock('systeminformation');
const mockedSi = si as jest.Mocked<typeof si>;

// Mock initializeConfigManager and getConfigManager
jest.mock('../../../config', () => {
  const actualConfig = jest.requireActual('../../../config');
  const mockConfigManagerInstance: any = {
    config: {
      mqtt: { host: 'localhost', port: 1883, base_topic: 'system_agent' },
      device_info: { name: 'TestDevice', identifiers: ['test-agent-123'], manufacturer: 'Test', model: 'Agent', sw_version: '1.0' },
      features: {
        cpu_load: { enabled: true, update_interval_seconds: 5 }
      },
      logging: { level: 'info' }
    },
    // Add any methods of ConfigManager that are used by BaseFeature or CpuLoad, e.g., getFeatureConfig
    getFeatureConfig: jest.fn((featureName: string) => mockConfigManagerInstance.config.features[featureName] || { enabled: false })
  };
  return {
    ...actualConfig, // Spread actual exports like AgentConfig, DeviceInfo types if needed
    initializeConfigManager: jest.fn(() => mockConfigManagerInstance),
    getConfigManager: jest.fn(() => mockConfigManagerInstance),
  };
});


const flushPromises = () => new Promise(process.nextTick);

describe('CpuLoad Feature', () => {
  let mockMqttClient: MockMqttClient;
  let cpuLoadFeature: CpuLoad;
  let setIntervalSpy: jest.SpyInstance;

  beforeEach(() => {
    mockedSi.currentLoad.mockReset();
    mockMqttClient = new MockMqttClient();
    cpuLoadFeature = new CpuLoad(mockMqttClient);

    setIntervalSpy = jest.spyOn(global, 'setInterval');
    jest.spyOn(global, 'clearInterval'); 
  });

  afterEach(async () => {
    await cpuLoadFeature.stop();
    jest.restoreAllMocks();
    mockMqttClient.reset();
  });

  it('should publish discovery messages and initial state on start', async () => {
    const mockLoadData: si.Systeminformation.CurrentLoadData = {
      avgLoad: 0.15, // system's average load
      currentLoad: 25.5, // total current load
      currentLoadUser: 15.0,
      currentLoadSystem: 10.0,
      currentLoadNice: 0.0,
      currentLoadIdle: 74.5,
      currentLoadIrq: 0.5,
      rawCurrentLoadUser: 1500,
      rawCurrentLoadSystem: 1000,
      rawCurrentLoadNice: 0,
      rawCurrentLoadIdle: 7450,
      rawCurrentLoadIrq: 50,
      currentLoadSteal: 0.0, // Added
      currentLoadGuest: 0.0, // Added
      rawCurrentLoad: 2550,    // Added (sum of raw user, system, nice, irq for currentLoad)
      rawCurrentLoadSteal: 0, // Added
      rawCurrentLoadGuest: 0, // Added
      cpus: [
        { load: 30.0, loadUser: 20.0, loadSystem: 10.0, loadNice: 0.0, loadIdle: 70.0, loadIrq: 0.0, loadSteal: 0.0, loadGuest: 0.0, rawLoad: 300, rawLoadUser: 200, rawLoadSystem: 100, rawLoadNice: 0, rawLoadIdle: 700, rawLoadIrq: 0, rawLoadSteal: 0, rawLoadGuest: 0  },
        { load: 20.0, loadUser: 10.0, loadSystem: 10.0, loadNice: 0.0, loadIdle: 80.0, loadIrq: 0.0, loadSteal: 0.0, loadGuest: 0.0, rawLoad: 200, rawLoadUser: 100, rawLoadSystem: 100, rawLoadNice: 0, rawLoadIdle: 800, rawLoadIrq: 0, rawLoadSteal: 0, rawLoadGuest: 0 }
      ]
    };
    mockedSi.currentLoad.mockResolvedValue(mockLoadData as si.Systeminformation.CurrentLoadData);

    mockMqttClient.connect();
    await cpuLoadFeature.start();
    await flushPromises();

    // console.log('CpuLoad Test - Published messages:', JSON.stringify(mockMqttClient.publishedMessages, null, 2));
    
    // Use process.nextTick to ensure assertions run after the current event loop turn + microtask queue is empty
    await new Promise(resolve => process.nextTick(resolve));

    // Check average load discovery
    let avgLoadDiscovery;
    for (const msg of mockMqttClient.publishedMessages) {
      if (msg.topic.endsWith('cpu_load_avg_load/config')) {
        avgLoadDiscovery = msg;
        break;
      }
    }
    expect(avgLoadDiscovery).toBeDefined();
    const avgPayload = JSON.parse(avgLoadDiscovery!.message.toString());
    expect(avgPayload.name).toBe('CPU Load Average');

    // Check core 1 load discovery
    const core1LoadDiscovery = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_load_core_1_load/config')
    );
    expect(core1LoadDiscovery).toBeDefined();

    // Check initial average load state
    const avgStateMsg = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_load/average/state')
    );
    expect(avgStateMsg).toBeDefined();
    expect(avgStateMsg!.message.toString()).toBe('25.50');

    // Check initial core 1 load state
    const core1StateMsg = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_load/core_1/state')
    );
    expect(core1StateMsg).toBeDefined();
    expect(core1StateMsg!.message.toString()).toBe('30.00');
  }, 15000);

  it('should call setInterval on start', async () => {
    mockedSi.currentLoad.mockResolvedValue({ currentLoad: 10, cpus: [] } as any);
    mockMqttClient.connect();
    await cpuLoadFeature.start();
    await flushPromises();
    
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 5000);
  }, 15000);

  it('should handle errors from systeminformation gracefully', async () => {
    mockedSi.currentLoad.mockRejectedValue(new Error('SI Load Error'));
    const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});

    mockMqttClient.connect();
    await cpuLoadFeature.start();
    await flushPromises();

    expect(consoleErrorSpy).toHaveBeenCalledWith(
      expect.stringContaining('Error updating CPU load'),
      expect.any(Error)
    );
  }, 15000);
});
