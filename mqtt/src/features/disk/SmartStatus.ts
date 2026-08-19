// src/features/disk/SmartStatus.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_argv } from '../../utils/exec';
import * as si from 'systeminformation'; // Keep for si.blockDevices if still used by getMonitoredDisks and potentially for SiDisksIoData type if needed elsewhere
import logger from '../../utils/logger';
import { getNativeDiskIOStats, DiskIOStats as NativeDiskIOStats } from '../../utils/diskStats';
import { createWinRMClient } from '../../utils/winrm';
import { isWindowsVmRunning } from '../../utils/vmState';

interface RemoteDiskConfig {
  name: string;
  device: string;
  friendly_name: string;
}

interface SmartStatusFeatureConfig extends FeatureConfig {
  disks_to_monitor?: string[]; // e.g., ["sda", "sdb", "nvme0n1"]
  remote_disks?: RemoteDiskConfig[]; // Remote disks via WinRM
}

interface SmartAttribute {
  id: number;
  name: string;
  value: number;
  worst: number;
  thresh: number;
  type: string;
  updated: string;
  when_failed: string;
  raw_value: string | number;
  parsed_value_gb?: number; // For specific attributes like LBAs written/read
}

export class SmartStatus extends BaseFeature {
  protected featureConfig: SmartStatusFeatureConfig;
  private previousBlockStats: { [diskName: string]: NativeDiskIOStats } = {};
  private readonly LBA_SIZE_BYTES = 512; // Standard LBA size
  private winrmClient = createWinRMClient();

  constructor(mqttClient: MqttClient, featureName: string = 'smart_status') {
    super(mqttClient, featureName);
    this.featureConfig = this.agentConfig.features[featureName] as SmartStatusFeatureConfig ||
                         { enabled: true, update_interval_seconds: 3600 };
  }

  private async getMonitoredDisks(): Promise<string[]> {
    if (this.featureConfig.disks_to_monitor && this.featureConfig.disks_to_monitor.length > 0) {
      return this.featureConfig.disks_to_monitor;
    }
    // Auto-detect physical disks
    try {
      const blockDevices = await si.blockDevices();
      logger.debug(`[${this.featureName}] Raw block devices detected:`, JSON.stringify(blockDevices, null, 2));
      // Filter for actual disks, not partitions or virtual devices like loop/ram
      const filteredDisks = blockDevices
        .filter(dev => {
          const isDisk = dev.type === 'disk';
          const isNotLoop = !dev.name.startsWith('loop');
          const isNotRam = !dev.name.startsWith('ram');
          const passesFilter = isDisk && isNotLoop && isNotRam;
          if (!passesFilter) {
            logger.debug(`[${this.featureName}] Disk ${dev.name} (type: ${dev.type}) filtered out. IsDisk: ${isDisk}, IsNotLoop: ${isNotLoop}, IsNotRam: ${isNotRam}`);
          }
          return passesFilter;
        })
        .map(dev => dev.name);
      logger.debug(`[${this.featureName}] Filtered physical disks for monitoring:`, JSON.stringify(filteredDisks, null, 2));
      return filteredDisks;
    } catch (error) {
      logger.error(`Error auto-detecting disks for ${this.featureName}:`, error);
      return [];
    }
  }

  private parseSmartOverallHealth(jsonData: any): string {
    if (!jsonData || !jsonData.smart_status) {
      return 'UNKNOWN';
    }
    return jsonData.smart_status.passed ? 'PASSED' : 'FAILED';
  }

  private parseSmartAttributes(jsonData: any, isNvme: boolean = false): SmartAttribute[] {
    const attributes: SmartAttribute[] = [];
    if (!jsonData) return attributes;

    if (isNvme) {
      // NVMe attributes are in `nvme_smart_health_information_log`
      const nvmeLog = jsonData.nvme_smart_health_information_log;
      if (!nvmeLog) return attributes;

      const nvmeAttrMap: { [key: string]: string } = {
        critical_warning: "critical_warning",
        temperature: "temperature_celsius",
        available_spare: "available_spare_percent",
        available_spare_threshold: "available_spare_threshold_percent",
        percentage_used: "percentage_used",
        data_units_read: "data_units_read", // This is in 512,000 byte units, not LBAs
        data_units_written: "data_units_written", // This is in 512,000 byte units, not LBAs
        host_read_commands: "host_read_commands",
        host_write_commands: "host_write_commands",
        controller_busy_time: "controller_busy_time_minutes",
        power_cycles: "power_cycles",
        power_on_hours: "power_on_hours",
        unsafe_shutdowns: "unsafe_shutdowns",
        media_errors: "media_data_integrity_errors", // Renamed from media_and_data_integrity_errors
        num_err_log_entries: "error_log_entries",
      };
      
      // Handle temperature sensors if they exist
      for (let i = 1; i <= 8; i++) { // smartctl supports up to 8 temperature sensors
        if (nvmeLog[`temperature_sensor_${i}`] !== undefined) {
          nvmeAttrMap[`temperature_sensor_${i}`] = `temperature_sensor_${i}_celsius`;
        }
      }


      for (const key in nvmeLog) {
        if (Object.prototype.hasOwnProperty.call(nvmeLog, key)) {
          const attributeName = nvmeAttrMap[key] || key.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/gi, '');
          let numericValue = parseFloat(nvmeLog[key]);
          if (isNaN(numericValue)) {
            // If it's an object like { value: X, units: "Y" }, try to get value
            if (typeof nvmeLog[key] === 'object' && nvmeLog[key] !== null && nvmeLog[key].value !== undefined) {
                numericValue = parseFloat(nvmeLog[key].value);
            }
             if (isNaN(numericValue)) numericValue = 0; // Default if still not a number
          }
          
          const attributeEntry: SmartAttribute = {
            id: 0, // NVMe attributes don't have standard IDs
            name: attributeName,
            value: numericValue,
            worst: 0, // Not applicable for NVMe in the same way as SATA
            thresh: 0, // Not applicable
            type: 'NVMe',
            updated: 'Always', // Assumed
            when_failed: '-', // Not applicable
            raw_value: String(nvmeLog[key].value !== undefined ? nvmeLog[key].value : nvmeLog[key]), // Store the original value as string
          };

          // Special handling for data units read/written for NVMe
          // smartctl JSON reports these in 1000s of 512-byte blocks.
          // So, value * 1000 * 512 bytes.
          if (attributeName === 'data_units_read' || attributeName === 'data_units_written') {
            const rawBlocks = numericValue; // This is already the value from nvmeLog[key]
            attributeEntry.value = rawBlocks; // Keep the value as reported by smartctl (in 1000s of 512B blocks)
            // Convert to GB: (value * 1000 * 512) / (1024^3)
            attributeEntry.parsed_value_gb = (rawBlocks * 1000 * 512) / (1024 * 1024 * 1024);
          }
          attributes.push(attributeEntry);
        }
      }
    } else { // SATA/SAS attributes are in `ata_smart_attributes.table`
      const ataAttributes = jsonData.ata_smart_attributes?.table;
      if (!ataAttributes || !Array.isArray(ataAttributes)) return attributes;

      for (const item of ataAttributes) {
        try {
          const attribute: SmartAttribute = {
            id: item.id,
            name: item.name,
            value: item.value,
            worst: item.worst,
            thresh: item.thresh,
            type: item.flags?.prefailure ? 'Pre-fail' : (item.flags?.old_age ? 'Old_age' : 'Unknown'),
            updated: item.flags?.updated_online ? 'Online' : 'Offline', // Simplified
            when_failed: item.when_failed || '-',
            raw_value: item.raw?.string || String(item.raw?.value) || '0',
          };

          if (attribute.name === 'Total_LBAs_Written' || attribute.name === 'Total_LBAs_Read') {
            const rawLbas = parseFloat(attribute.raw_value as string);
            if (!isNaN(rawLbas)) {
              attribute.parsed_value_gb = (rawLbas * this.LBA_SIZE_BYTES) / (1024 * 1024 * 1024);
            }
          }
          attributes.push(attribute);
        } catch (e) {
          logger.warn(`Could not parse SATA SMART attribute from JSON: "${JSON.stringify(item)}"`, e);
        }
      }
    }
    return attributes;
  }

  protected async publishDiscovery(): Promise<void> {
    const disks = await this.getMonitoredDisks();
    for (const diskName of disks) {
      const objectId = diskName.replace(/[^a-zA-Z0-9_-]/g, '_');
      const baseName = `Disk ${diskName}`;

      // SMART Health Status Sensor
      await this.publishEntityDiscovery('sensor', `${objectId}_smart_health`, {
        name: `${baseName} SMART Health`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/health/state`),
        icon: 'mdi:heart-pulse',
        entity_category: 'diagnostic' as 'diagnostic',
        json_attributes_topic: this.prefixTopic(`${this.featureName}/${objectId}/attributes`),
      });

      // Disk Read Speed
      await this.publishEntityDiscovery('sensor', `${objectId}_read_speed`, {
        name: `${baseName} Read Speed`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/read_speed/state`),
        unit_of_measurement: 'MB/s',
        icon: 'mdi:arrow-down-box',
        state_class: 'measurement' as 'measurement',
        entity_category: 'diagnostic' as 'diagnostic',
      });

      // Disk Write Speed
      await this.publishEntityDiscovery('sensor', `${objectId}_write_speed`, {
        name: `${baseName} Write Speed`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/write_speed/state`),
        unit_of_measurement: 'MB/s',
        icon: 'mdi:arrow-up-box',
        state_class: 'measurement' as 'measurement',
        entity_category: 'diagnostic' as 'diagnostic',
      });

      // Total LBAs Written
      await this.publishEntityDiscovery('sensor', `${objectId}_lbas_written`, {
        name: `${baseName} Total Data Written`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/lbas_written/state`),
        unit_of_measurement: 'GB', // Or TB, will convert in update
        icon: 'mdi:database-arrow-down-outline',
        state_class: 'total_increasing' as 'total_increasing',
        entity_category: 'diagnostic' as 'diagnostic',
      });

      // Total LBAs Read
      await this.publishEntityDiscovery('sensor', `${objectId}_lbas_read`, {
        name: `${baseName} Total Data Read`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/lbas_read/state`),
        unit_of_measurement: 'GB', // Or TB
        icon: 'mdi:database-arrow-up-outline',
        state_class: 'total_increasing' as 'total_increasing',
        entity_category: 'diagnostic' as 'diagnostic',
      });
    }

    // Publish discovery for remote disks (Windows VM via WinRM)
    const remoteDisks = this.featureConfig.remote_disks || [];
    for (const remoteDisk of remoteDisks) {
      const objectId = remoteDisk.name.replace(/[^a-zA-Z0-9_-]/g, '_');
      const baseName = `Disk ${remoteDisk.friendly_name}`;

      // SMART Health Status Sensor
      await this.publishEntityDiscovery('sensor', `${objectId}_smart_health`, {
        name: `${baseName} SMART Health`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/health/state`),
        icon: 'mdi:heart-pulse',
        entity_category: 'diagnostic' as 'diagnostic',
        json_attributes_topic: this.prefixTopic(`${this.featureName}/${objectId}/attributes`),
      });

      // Total Data Written
      await this.publishEntityDiscovery('sensor', `${objectId}_lbas_written`, {
        name: `${baseName} Total Data Written`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/lbas_written/state`),
        unit_of_measurement: 'GB',
        icon: 'mdi:database-arrow-down-outline',
        state_class: 'total_increasing' as 'total_increasing',
        entity_category: 'diagnostic' as 'diagnostic',
      });

      // Total Data Read
      await this.publishEntityDiscovery('sensor', `${objectId}_lbas_read`, {
        name: `${baseName} Total Data Read`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/lbas_read/state`),
        unit_of_measurement: 'GB',
        icon: 'mdi:database-arrow-up-outline',
        state_class: 'total_increasing' as 'total_increasing',
        entity_category: 'diagnostic' as 'diagnostic',
      });

      // Note: No read/write speed sensors for remote disks since we can't monitor I/O stats remotely
    }
  }

  protected async update(): Promise<void> {
    const disks = await this.getMonitoredDisks();
    // const diskIoData = await si.disksIO(); // OLD WAY
    const nativeDiskIOData = await getNativeDiskIOStats(); // NEW WAY
    
    // Ensure currentDiskIO is an array (nativeDiskIOData should already be an array)
    const currentDiskIOArray: NativeDiskIOStats[] = Array.isArray(nativeDiskIOData) ? nativeDiskIOData : [];
    logger.info(`[${this.featureName}] Raw disk I/O data from getNativeDiskIOStats():`, JSON.stringify(currentDiskIOArray, null, 2)); // TEMPORARY LOG


    for (const diskName of disks) {
      const objectId = diskName.replace(/[^a-zA-Z0-9_-]/g, '_');
      logger.debug(`[${this.featureName}] Processing disk: ${diskName}, objectId: ${objectId}`);
      try {
        // Get all SMART data in JSON format (requires sudo)
        logger.debug(`[${this.featureName}] Getting SMART data (JSON) for /dev/${diskName}`);
        // Using -j for JSON output. -H (health) and -A (attributes) are implicitly included or can be derived from full JSON.
        const smartctlOutput = await execute_argv('sudo', ['smartctl', '-j', '-a', `/dev/${diskName}`]);
        logger.debug(`[${this.featureName}] SMART JSON output for /dev/${diskName}:`, smartctlOutput.stdout);

        // Check if stdout is valid
        if (!smartctlOutput.stdout || smartctlOutput.stdout.trim() === '') {
          logger.error(`[${this.featureName}] smartctl returned empty stdout for ${diskName}. ExitCode: ${smartctlOutput.exitCode}, Stderr: ${smartctlOutput.stderr}`);
          await this.publishState(`${this.featureName}/${objectId}/health/state`, 'ERROR_NO_OUTPUT', true);
          await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, {
            error: 'smartctl returned no output',
            exitCode: smartctlOutput.exitCode,
            stderr: smartctlOutput.stderr
          }, true);
          continue; // Skip to next disk
        }

        let jsonData;
        try {
          jsonData = JSON.parse(smartctlOutput.stdout);
        } catch (parseError) {
          logger.error(`[${this.featureName}] Failed to parse SMART JSON for /dev/${diskName}:`, parseError);
          logger.error(`[${this.featureName}] Raw stdout was:`, smartctlOutput.stdout);
          logger.error(`[${this.featureName}] Raw stderr was:`, smartctlOutput.stderr);
          await this.publishState(`${this.featureName}/${objectId}/health/state`, 'ERROR_PARSING_JSON', true);
          await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, {
            error: 'Failed to parse smartctl JSON output',
            parseError: String(parseError),
            stdoutPreview: smartctlOutput.stdout.substring(0, 200)
          }, true);
          continue; // Skip to next disk
        }
        
        // Parse and publish SMART Health
        const healthStatus = this.parseSmartOverallHealth(jsonData);
        logger.debug(`[${this.featureName}] Parsed health for /dev/${diskName}: ${healthStatus}`);
        await this.publishState(`${this.featureName}/${objectId}/health/state`, healthStatus, true);

        // Parse and publish SMART Attributes
        const isNvmeDisk = diskName.startsWith('nvme') || (jsonData.device && jsonData.device.type === 'nvme');
        logger.debug(`[${this.featureName}] Parsing SMART attributes for /dev/${diskName} (isNvme: ${isNvmeDisk})`);
        const attributes = this.parseSmartAttributes(jsonData, isNvmeDisk);
        logger.debug(`[${this.featureName}] Parsed attributes for /dev/${diskName}:`, JSON.stringify(attributes, null, 2));

        const attributesForMqtt = attributes.map(attr => {
            let isFailed = false;
            if (attr.id !== 0) { // SATA/SCSI like attributes (NVMe attributes have id: 0 in our parsing)
                // An attribute is considered failed if 'WHEN_FAILED' is set (not '-')
                // or if its current value is less than or equal to the threshold (and threshold is meaningful)
                isFailed = (attr.when_failed && attr.when_failed !== '-') ||
                           (attr.thresh > 0 && attr.value <= attr.thresh);
            } else { // NVMe specific failure conditions
                // Based on common NVMe critical warnings or error counters
                if (attr.name === 'critical_warning' && attr.value !== 0) { // 0x00 is no warning
                    isFailed = true;
                } else if (attr.name === 'media_data_integrity_errors' && attr.value !== 0) {
                    isFailed = true;
                } else if (attr.name === 'error_log_entries' && attr.value !== 0) {
                    // Depending on policy, any error log entry might be considered a failed state for this attribute's check
                    isFailed = true; 
                }
                // Add other NVMe specific checks if needed, e.g., available_spare_percent < available_spare_threshold_percent
            }
            return {
                id: attr.id,
                name: attr.name,
                value: attr.value, // Normalized value from smartctl
                raw: attr.raw_value, // Raw value string from smartctl
                failed: isFailed
            };
        });
        logger.debug(`[${this.featureName}] All SMART attributes for MQTT for /dev/${diskName}:`, JSON.stringify(attributesForMqtt, null, 2));
        
        await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, { smart_attributes: attributesForMqtt }, true);

        // Publish Total LBAs Written/Read
        const lbasWrittenAttr = attributes.find(attr => attr.name === 'Total_LBAs_Written' || attr.name === 'data_units_written');
        if (lbasWrittenAttr && lbasWrittenAttr.parsed_value_gb !== undefined) {
          logger.debug(`[${this.featureName}] Publishing LBAs written for /dev/${diskName}: ${lbasWrittenAttr.parsed_value_gb.toFixed(2)} GB`);
          await this.publishState(`${this.featureName}/${objectId}/lbas_written/state`, lbasWrittenAttr.parsed_value_gb.toFixed(2), true);
        }
        const lbasReadAttr = attributes.find(attr => attr.name === 'Total_LBAs_Read' || attr.name === 'data_units_read');
        if (lbasReadAttr && lbasReadAttr.parsed_value_gb !== undefined) {
          logger.debug(`[${this.featureName}] Publishing LBAs read for /dev/${diskName}: ${lbasReadAttr.parsed_value_gb.toFixed(2)} GB`);
          await this.publishState(`${this.featureName}/${objectId}/lbas_read/state`, lbasReadAttr.parsed_value_gb.toFixed(2), true);
        }

        // Disk I/O
        logger.info(`[${this.featureName}] Disk I/O for ${diskName}: Searching in currentDiskIOArray...`); // TEMPORARY LOG

        const ioStat = currentDiskIOArray.find(stat => stat.device === diskName || stat.device === `/dev/${diskName}`);
        const prevIoStat = this.previousBlockStats[diskName];
        logger.info(`[${this.featureName}] Disk I/O for ${diskName}: Found ioStat: ${JSON.stringify(ioStat)}, Found prevIoStat: ${JSON.stringify(prevIoStat)}`); // TEMPORARY LOG

        if (!ioStat) {
            logger.warn(`[${this.featureName}] Disk I/O for ${diskName}: Could not find matching I/O statistics. This may be because systeminformation.disksIO() did not provide a 'device' field for mapping. Raw data logged earlier.`);
        }

        if (ioStat && this.featureConfig.update_interval_seconds) {
            logger.info(`[${this.featureName}] Disk I/O for ${diskName}: ioStat and update_interval_seconds (${this.featureConfig.update_interval_seconds}) are valid.`); // TEMPORARY LOG
            if (prevIoStat) {
                logger.info(`[${this.featureName}] Disk I/O for ${diskName}: prevIoStat exists. Calculating speed.`); // TEMPORARY LOG
                const interval = this.featureConfig.update_interval_seconds;
                const readDiff = ioStat.rIO - prevIoStat.rIO;
                const writeDiff = ioStat.wIO - prevIoStat.wIO;
                logger.info(`[${this.featureName}] Disk I/O for ${diskName}: rIO_current=${ioStat.rIO}, rIO_prev=${prevIoStat.rIO}, readDiff=${readDiff}`); // TEMPORARY LOG
                logger.info(`[${this.featureName}] Disk I/O for ${diskName}: wIO_current=${ioStat.wIO}, wIO_prev=${prevIoStat.wIO}, writeDiff=${writeDiff}`); // TEMPORARY LOG

                // Ensure no negative values if counters reset or roll over (though unlikely for rIO/wIO)
                const readSpeed = Math.max(0, readDiff) / interval; // Bytes per second
                const writeSpeed = Math.max(0, writeDiff) / interval; // Bytes per second
                logger.info(`[${this.featureName}] Disk I/O for ${diskName}: Calculated readSpeed (B/s)=${readSpeed}, writeSpeed (B/s)=${writeSpeed}`); // TEMPORARY LOG

                await this.publishState(`${this.featureName}/${objectId}/read_speed/state`, (readSpeed / (1024*1024)).toFixed(2), true);
                await this.publishState(`${this.featureName}/${objectId}/write_speed/state`, (writeSpeed / (1024*1024)).toFixed(2), true);
                logger.info(`[${this.featureName}] Disk I/O for ${diskName}: Published read_speed=${(readSpeed / (1024*1024)).toFixed(2)} MB/s, write_speed=${(writeSpeed / (1024*1024)).toFixed(2)} MB/s`); // TEMPORARY LOG
            } else {
                logger.info(`[${this.featureName}] Disk I/O for ${diskName}: No prevIoStat. Publishing 0.00 for speeds (first run).`); // TEMPORARY LOG
                // First run, no previous data to calculate speed, publish 0.
                await this.publishState(`${this.featureName}/${objectId}/read_speed/state`, '0.00', true);
                await this.publishState(`${this.featureName}/${objectId}/write_speed/state`, '0.00', true);
            }
        } else {
            logger.warn(`[${this.featureName}] Disk I/O for ${diskName}: Skipped speed calculation. ioStat valid: ${!!ioStat}, update_interval_seconds: ${this.featureConfig.update_interval_seconds}`); // TEMPORARY LOG
        }
        if (ioStat) {
            this.previousBlockStats[diskName] = { ...ioStat }; // Store a copy
            logger.info(`[${this.featureName}] Disk I/O for ${diskName}: Stored current ioStat to previousBlockStats.`); // TEMPORARY LOG
        }


      } catch (error: any) {
        const errorMessage = error.message || 'Unknown error';
        logger.error(`Error updating SMART status for disk ${diskName} (${this.featureName}):`, errorMessage);
        await this.publishState(`${this.featureName}/${objectId}/health/state`, 'ERROR', true);
        await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, { error: errorMessage }, true);
      }
    }

    // Process remote disks (Windows VM via WinRM) - only if VM is running
    const remoteDisks = this.featureConfig.remote_disks || [];
    if (remoteDisks.length > 0 && !await isWindowsVmRunning()) {
      logger.debug(`[${this.featureName}] Windows VM not running, skipping remote disks`);
      return;
    }
    for (const remoteDisk of remoteDisks) {
      const objectId = remoteDisk.name.replace(/[^a-zA-Z0-9_-]/g, '_');
      logger.debug(`[${this.featureName}] Processing remote disk: ${remoteDisk.name}, device: ${remoteDisk.device}`);
      try {
        // Execute smartctl on Windows VM via WinRM
        logger.debug(`[${this.featureName}] Getting SMART data via WinRM for ${remoteDisk.device}`);
        const smartctlOutput = await this.winrmClient.executeCommand(`smartctl -j -a ${remoteDisk.device}`);

        if (!smartctlOutput || smartctlOutput.trim() === '') {
          logger.error(`[${this.featureName}] WinRM smartctl returned empty output for ${remoteDisk.name}`);
          await this.publishState(`${this.featureName}/${objectId}/health/state`, 'ERROR_NO_OUTPUT', true);
          await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, {
            error: 'smartctl returned no output via WinRM'
          }, true);
          continue;
        }

        let jsonData;
        try {
          jsonData = JSON.parse(smartctlOutput);
        } catch (parseError) {
          logger.error(`[${this.featureName}] Failed to parse SMART JSON for remote ${remoteDisk.name}:`, parseError);
          await this.publishState(`${this.featureName}/${objectId}/health/state`, 'ERROR_PARSING_JSON', true);
          await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, {
            error: 'Failed to parse smartctl JSON output',
            parseError: String(parseError)
          }, true);
          continue;
        }

        // Parse and publish SMART Health
        const healthStatus = this.parseSmartOverallHealth(jsonData);
        await this.publishState(`${this.featureName}/${objectId}/health/state`, healthStatus, true);

        // Parse and publish SMART Attributes
        const isNvmeDisk = (jsonData.device && jsonData.device.type === 'nvme');
        const attributes = this.parseSmartAttributes(jsonData, isNvmeDisk);

        const attributesForMqtt = attributes.map(attr => {
          let isFailed = false;
          if (attr.id !== 0) {
            isFailed = (attr.when_failed && attr.when_failed !== '-') ||
                       (attr.thresh > 0 && attr.value <= attr.thresh);
          } else {
            if (attr.name === 'critical_warning' && attr.value !== 0) {
              isFailed = true;
            } else if (attr.name === 'media_data_integrity_errors' && attr.value !== 0) {
              isFailed = true;
            } else if (attr.name === 'error_log_entries' && attr.value !== 0) {
              isFailed = true;
            }
          }
          return {
            id: attr.id,
            name: attr.name,
            value: attr.value,
            raw: attr.raw_value,
            failed: isFailed
          };
        });

        await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, { smart_attributes: attributesForMqtt }, true);

        // Publish Total LBAs Written/Read
        const lbasWrittenAttr = attributes.find(attr => attr.name === 'Total_LBAs_Written' || attr.name === 'data_units_written');
        if (lbasWrittenAttr && lbasWrittenAttr.parsed_value_gb !== undefined) {
          await this.publishState(`${this.featureName}/${objectId}/lbas_written/state`, lbasWrittenAttr.parsed_value_gb.toFixed(2), true);
        }
        const lbasReadAttr = attributes.find(attr => attr.name === 'Total_LBAs_Read' || attr.name === 'data_units_read');
        if (lbasReadAttr && lbasReadAttr.parsed_value_gb !== undefined) {
          await this.publishState(`${this.featureName}/${objectId}/lbas_read/state`, lbasReadAttr.parsed_value_gb.toFixed(2), true);
        }

        // Note: Disk I/O speed is not available for remote disks
        // We only monitor SMART health and attributes
        logger.debug(`[${this.featureName}] Remote disk ${remoteDisk.name} processed successfully`);

      } catch (error: any) {
        const errorMessage = error.message || 'Unknown error';
        logger.error(`Error updating SMART status for remote disk ${remoteDisk.name} (${this.featureName}):`, errorMessage);
        await this.publishState(`${this.featureName}/${objectId}/health/state`, 'ERROR', true);
        await this.publishAttributes(`${this.featureName}/${objectId}/attributes`, { error: errorMessage }, true);
      }
    }
  }

  private analyzeSmartHealth(attributes: SmartAttribute[], overallHealth: string, isNvme: boolean = false): {
    status: 'HEALTHY' | 'WARNING' | 'CRITICAL' | 'UNKNOWN';
    issues: string[];
    temperature?: number;
    wear_level?: number;
    reallocated_sectors?: number;
    power_on_hours?: number;
  } {
    const result = {
      status: 'HEALTHY' as 'HEALTHY' | 'WARNING' | 'CRITICAL' | 'UNKNOWN',
      issues: [] as string[],
      temperature: undefined as number | undefined,
      wear_level: undefined as number | undefined,
      reallocated_sectors: undefined as number | undefined,
      power_on_hours: undefined as number | undefined,
    };

    // If overall SMART health failed, immediately mark as critical
    if (overallHealth === 'FAILED') {
      result.status = 'CRITICAL';
      result.issues.push('SMART self-test failed');
    }

    if (isNvme) {
      // NVMe-specific health analysis
      for (const attr of attributes) {
        const value = attr.value;
        
        switch (attr.name) {
          case 'critical_warning':
            if (value > 0) {
              result.status = 'CRITICAL';
              result.issues.push(`Critical warning: ${value}`);
            }
            break;
          
          case 'temperature_celsius':
          case 'temperature':
            result.temperature = value;
            if (value > 80) {
              result.status = result.status === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
              result.issues.push(`High temperature: ${value}°C`);
            }
            break;
          
          case 'available_spare_percent':
          case 'available_spare':
            if (value < 10) {
              result.status = 'CRITICAL';
              result.issues.push(`Low spare blocks: ${value}%`);
            } else if (value < 20) {
              result.status = result.status === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
              result.issues.push(`Spare blocks getting low: ${value}%`);
            }
            break;
          
          case 'percentage_used':
            result.wear_level = value;
            if (value > 90) {
              result.status = 'CRITICAL';
              result.issues.push(`High wear level: ${value}%`);
            } else if (value > 80) {
              result.status = result.status === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
              result.issues.push(`Moderate wear level: ${value}%`);
            }
            break;
          
          case 'media_data_integrity_errors':
          case 'media_errors':
            if (value > 0) {
              result.status = 'CRITICAL';
              result.issues.push(`Media integrity errors: ${value}`);
            }
            break;
          
          case 'power_on_hours':
            result.power_on_hours = value;
            break;
        }
      }
    } else {
      // SATA/SAS-specific health analysis
      for (const attr of attributes) {
        const value = attr.value;
        const rawValue = typeof attr.raw_value === 'string' ? parseInt(attr.raw_value) : attr.raw_value;
        
        switch (attr.id) {
          case 5: // Reallocated_Sector_Ct
            result.reallocated_sectors = rawValue;
            if (rawValue > 0) {
              if (rawValue > 100) {
                result.status = 'CRITICAL';
                result.issues.push(`High reallocated sectors: ${rawValue}`);
              } else {
                result.status = result.status === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
                result.issues.push(`Reallocated sectors detected: ${rawValue}`);
              }
            }
            break;
          
          case 196: // Reallocated_Event_Count
            if (rawValue > 0) {
              result.status = result.status === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
              result.issues.push(`Reallocation events: ${rawValue}`);
            }
            break;
          
          case 197: // Current_Pending_Sector
            if (rawValue > 0) {
              result.status = 'CRITICAL';
              result.issues.push(`Pending bad sectors: ${rawValue}`);
            }
            break;
          
          case 198: // Offline_Uncorrectable
            if (rawValue > 0) {
              result.status = 'CRITICAL';
              result.issues.push(`Offline uncorrectable sectors: ${rawValue}`);
            }
            break;
          
          case 194: // Temperature_Celsius
            result.temperature = rawValue;
            if (rawValue > 60) {
              result.status = result.status === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
              result.issues.push(`High temperature: ${rawValue}°C`);
            }
            break;
          
          case 9: // Power_On_Hours
            result.power_on_hours = rawValue;
            break;
          
          case 202: // Data_Address_Mark_Errs (wear level indicator for some SSDs)
          case 233: // Media_Wearout_Indicator
            if (value < 10) {
              result.status = 'CRITICAL';
              result.issues.push(`Low wear level remaining: ${value}`);
            } else if (value < 20) {
              result.status = result.status === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
              result.issues.push(`Wear level getting low: ${value}`);
            }
            break;
        }
        
        // Check if attribute value is below threshold
        if (attr.thresh > 0 && value <= attr.thresh && attr.type === 'Pre-fail') {
          result.status = 'CRITICAL';
          result.issues.push(`${attr.name} below threshold (${value} <= ${attr.thresh})`);
        }
      }
    }

    return result;
  }

  private formatHealthSummary(analysis: ReturnType<typeof this.analyzeSmartHealth>): string {
    const parts: string[] = [analysis.status];
    
    if (analysis.temperature !== undefined) {
      parts.push(`${analysis.temperature}°C`);
    }
    
    if (analysis.wear_level !== undefined) {
      parts.push(`${analysis.wear_level}% worn`);
    }
    
    if (analysis.issues.length > 0) {
      parts.push(`(${analysis.issues.length} issues)`);
    }
    
    return parts.join(' - ');
  }
}
