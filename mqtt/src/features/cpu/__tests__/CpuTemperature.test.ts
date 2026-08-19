// src/features/cpu/__tests__/CpuTemperature.test.ts

import { CpuTemperature } from '../CpuTemperature';
import { MockMqttClient } from '../../../mqtt/__tests__/mocks/MockMqttClient';
// configManager is mocked below
import * as si from 'systeminformation';

// Mock systeminformation
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
        cpu_temperature: { enabled: true, update_interval_seconds: 5 }
      },
      logging: { level: 'info'}
    },
    getFeatureConfig: jest.fn((featureName: string) => mockConfigManagerInstance.config.features[featureName] || { enabled: false })
  };
  return {
    ...actualConfig,
    initializeConfigManager: jest.fn(() => mockConfigManagerInstance),
    getConfigManager: jest.fn(() => mockConfigManagerInstance),
  };
});


describe('CpuTemperature Feature', () => {
  let mockMqttClient: MockMqttClient;
  let cpuTemperatureFeature: CpuTemperature;
  let setIntervalSpy: jest.SpyInstance;
  let clearIntervalSpy: jest.SpyInstance;

  beforeEach(() => {
    mockedSi.cpuTemperature.mockReset(); 
    mockMqttClient = new MockMqttClient();
    cpuTemperatureFeature = new CpuTemperature(mockMqttClient);
    
    // Spy on setInterval and clearInterval without using fake timers
    setIntervalSpy = jest.spyOn(global, 'setInterval');
    clearIntervalSpy = jest.spyOn(global, 'clearInterval');
  });

  afterEach(async () => {
    await cpuTemperatureFeature.stop(); // This will call clearInterval
    // Restore all mocks, including spies on global timers
    jest.restoreAllMocks(); 
    mockMqttClient.reset();
  });

  it('should publish discovery messages and initial state on start', async () => {
    mockedSi.cpuTemperature.mockResolvedValue({
      main: 45.6, cores: [50.1, 52.3], max: 95.0, socket: [], chipset: undefined,
    } as si.Systeminformation.CpuTemperatureData);

    mockMqttClient.connect(); // Mock connect is synchronous
    await cpuTemperatureFeature.start(); 
    
    // Allow async operations within start() to complete
    await new Promise(resolve => process.nextTick(resolve)); // Use process.nextTick

    // console.log('Published messages for discovery test:', JSON.stringify(mockMqttClient.publishedMessages, null, 2)); 

    let mainTempDiscovery;
    for (const msg of mockMqttClient.publishedMessages) {
      if (msg.topic.endsWith('cpu_temperature_main_temp/config')) {
        mainTempDiscovery = msg;
        break;
      }
    }
    expect(mainTempDiscovery).toBeDefined();
    const mainPayload = JSON.parse(mainTempDiscovery!.message.toString());
    expect(mainPayload.name).toBe('CPU Temperature');
    expect(mainPayload.device_class).toBe('temperature');
    expect(mainPayload.unit_of_measurement).toBe('°C');
    expect(mainPayload.json_attributes_topic).toBeDefined();


    const core1TempDiscovery = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_temperature_core_1_temp/config')
    );
    expect(core1TempDiscovery).toBeDefined();
    const core1Payload = JSON.parse(core1TempDiscovery!.message.toString());
    expect(core1Payload.name).toBe('CPU Core 1 Temperature');
    
    const core2TempDiscovery = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_temperature_core_2_temp/config')
    );
    expect(core2TempDiscovery).toBeDefined();


    const mainStateMsg = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_temperature/main/state')
    );
    expect(mainStateMsg).toBeDefined();
    expect(mainStateMsg!.message.toString()).toBe('45.6');

    const core1StateMsg = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_temperature/core_1/state')
    );
    expect(core1StateMsg).toBeDefined();
    expect(core1StateMsg!.message.toString()).toBe('50.1');
    
    const mainAttributesMsg = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_temperature/main/attributes')
    );
    expect(mainAttributesMsg).toBeDefined();
    const attributes = JSON.parse(mainAttributesMsg!.message.toString());
    expect(attributes.max).toBe(95.0);

  }, 15000); 

  it('should call setInterval on start if update_interval_seconds is set', async () => {
    mockedSi.cpuTemperature.mockResolvedValue({ main: 40, cores: [], max: 80 } as any);
    
    mockMqttClient.connect();
    await cpuTemperatureFeature.start();
    await new Promise(resolve => process.nextTick(resolve)); // Use process.nextTick

    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 5000);
  }, 15000);
  
  it('should derive main temperature from cores when main is null or -1', async () => {
    mockedSi.cpuTemperature.mockResolvedValue({
      main: -1, cores: [45.0], max: 80, socket: [], chipset: undefined
    } as si.Systeminformation.CpuTemperatureData);

    mockMqttClient.connect();
    await cpuTemperatureFeature.start();
    await new Promise(resolve => process.nextTick(resolve)); // Use process.nextTick

    // Main temp is unavailable (-1), so the feature falls back to the max valid core temp.
    const mainStateMsg = mockMqttClient.publishedMessages.find(
      msg => msg.topic.endsWith('cpu_temperature/main/state')
    );
    expect(mainStateMsg).toBeDefined();
    expect(mainStateMsg!.message.toString()).toBe('45.0');
  }, 15000);

  it('should handle errors from systeminformation gracefully during initial update', async () => {
    mockedSi.cpuTemperature.mockRejectedValue(new Error('SI Error'));
    const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});

    mockMqttClient.connect();
    await cpuTemperatureFeature.start();
    await new Promise(resolve => process.nextTick(resolve)); // Use process.nextTick

    expect(consoleErrorSpy).toHaveBeenCalledWith(
      expect.stringContaining('Error updating CPU temperature'),
      expect.any(Error)
    );
    // consoleErrorSpy.mockRestore(); // Not needed due to jest.restoreAllMocks() in afterEach
  }, 15000);
});
