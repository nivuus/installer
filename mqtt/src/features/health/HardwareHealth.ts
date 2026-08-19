// src/features/health/HardwareHealth.ts

import * as fs from 'fs';
import * as path from 'path';
import { BaseFeature } from '../../core/BaseFeature';
import { HaDiscoveryPayload } from '../../core/types';
import logger from '../../utils/logger';
import {
  DiskErrors, findChip, listDisks, readCpuCounters, readDiskErrors,
  readMemoryErrors, readInt, readRails,
} from './hardwareProbes';

const CHIP = 'nct6798';
// Learned and persisted by scripts/hw-blackbox.py, which samples every second.
// Sharing the file keeps one definition of "normal" per rail.
const BASELINE = '/var/lib/nivuus/blackbox-baseline.json';
// smartctl forks once per device, so it runs far slower than the feature tick.
const DISK_REFRESH_MS = 10 * 60 * 1000;

/**
 * Median and normal operating range of one rail, in millivolts, as written by
 * scripts/hw-blackbox.py. The range is p1/p99 rather than a symmetric band: a
 * rail's normal swing is not centred on its median.
 */
interface RailBaseline {
  med: number;
  lo: number;
  hi: number;
}

/**
 * Hardware fault surface: power rails plus CPU, memory and disk error counters.
 *
 * Exists because the host died four times in 2026 (04/08, 05/08, 07/08, plus a
 * boot that never completed) with the platform halting instantly — the iTCO
 * watchdog never fired, and the last reading 7 s before the 07/08 cut showed a
 * cool, steady machine. Nothing gradual explains that, and nothing was watching
 * the electrical side.
 *
 * nct6798 exposes in0..in14 as raw millivolts with no labels and the
 * board-specific dividers are not discoverable. The canonical nct679x mapping
 * puts +12V on in1 (x12) and +5V on in4 (x5); on this board that yields 11.90 V
 * and 5.00 V, both in ATX spec, which corroborates it. Those are published for
 * readability, but the alerting entity is `max_rail_drift`, derived from *every*
 * rail against its own baseline, so it stays correct even if the mapping is not.
 */
export class HardwareHealth extends BaseFeature {
  private chipDir: string | null = null;
  private baseline: Record<string, RailBaseline> = {};
  private disks: DiskErrors = { total: 0, perDisk: {} };
  private disksCheckedAt = 0;

  private static readonly RAILS = [
    { id: 'psu_12v', input: 'in1', scale: 12, name: 'Power PSU 12V Rail Sensor' },
    { id: 'psu_5v', input: 'in4', scale: 5, name: 'Power PSU 5V Rail Sensor' },
    { id: 'vcore', input: 'in0', scale: 1, name: 'Power CPU Vcore Sensor' },
  ];

  private static readonly COUNTERS = [
    { id: 'max_rail_drift', name: 'Power Max Rail Drift Sensor', unit: '%', icon: 'mdi:sine-wave' },
    { id: 'cpu_machine_checks', name: 'CPU Machine Check Errors Sensor', unit: null, icon: 'mdi:cpu-64-bit' },
    { id: 'cpu_thermal_events', name: 'CPU Thermal Throttle Events Sensor', unit: null, icon: 'mdi:thermometer-alert' },
    { id: 'memory_errors', name: 'Memory Errors Sensor', unit: null, icon: 'mdi:memory' },
    { id: 'disk_errors', name: 'Disk Hardware Errors Sensor', unit: null, icon: 'mdi:harddisk' },
  ];

  async publishDiscovery(): Promise<void> {
    const entities = [
      ...HardwareHealth.RAILS.map((rail) => ({
        id: rail.id, name: rail.name, unit: 'V', icon: 'mdi:flash', deviceClass: 'voltage',
      })),
      ...HardwareHealth.COUNTERS.map((counter) => ({ ...counter, deviceClass: undefined })),
    ];

    for (const entity of entities) {
      const payload: HaDiscoveryPayload = {
        unique_id: `${this.deviceInfo.identifiers[0]}_${entity.id}`,
        name: entity.name,
        state_topic: this.prefixTopic(`${this.featureName}/${entity.id}/state`),
        json_attributes_topic: this.prefixTopic(`${this.featureName}/${entity.id}/attributes`),
        availability_topic: this.availabilityTopic,
        payload_available: this.payloadAvailable,
        payload_not_available: this.payloadNotAvailable,
        device: this.deviceInfo,
        icon: entity.icon,
        entity_category: 'diagnostic',
      };
      if (entity.unit) payload.unit_of_measurement = entity.unit;
      if (entity.deviceClass) payload.device_class = entity.deviceClass;

      const topic =
        `homeassistant/sensor/${this.deviceInfo.identifiers[0]}/${payload.unique_id}/config`;
      await this.mqttClient.publish(topic, JSON.stringify(payload), { qos: 1, retain: true });
    }
  }

  private loadBaseline(): void {
    try {
      const loaded = JSON.parse(fs.readFileSync(BASELINE, 'utf8'));
      // Only accept entries that carry the full range. Anything older would
      // leave `hi`/`lo` undefined, which silently turns the drift entity back
      // into raw deviation and pegs it at ~35 % on Vcore alone.
      const usable = Object.values(loaded).every(
        (v: unknown) => typeof v === 'object' && v !== null
          && ['med', 'lo', 'hi'].every((k) => typeof (v as Record<string, unknown>)[k] === 'number'),
      );
      this.baseline = usable ? (loaded as Record<string, RailBaseline>) : {};
    } catch {
      // The black box needs ~5 min of uptime to learn one; absent is not an error.
      this.baseline = {};
    }
  }

  /**
   * Largest deviation of any rail *outside its own observed range*, in percent.
   *
   * The range matters: Vcore moves 0.62-0.87 V with CPU load, over 30 % of its
   * median and perfectly healthy. Measuring raw deviation would peg this entity
   * at ~35 % permanently and bury the 2 % sag on a fixed rail that would
   * actually mean something.
   */
  private maxDrift(rails: Record<string, number>): { drift: number | null; worst: string | null } {
    let drift: number | null = null;
    let worst: string | null = null;
    for (const [rail, reference] of Object.entries(this.baseline)) {
      const value = rails[rail];
      if (!reference?.med || value === undefined) continue;
      const outside = Math.max(reference.lo - value, value - reference.hi, 0);
      const pct = (outside / reference.med) * 100;
      if (drift === null || pct > drift) {
        drift = pct;
        worst = rail;
      }
    }
    return { drift, worst };
  }

  async update(): Promise<void> {
    if (!this.chipDir) {
      this.chipDir = findChip(CHIP);
      if (!this.chipDir) {
        logger.error(`[${this.featureName}] hwmon chip ${CHIP} not found`);
        return;
      }
    }
    this.loadBaseline();
    const rails = readRails(this.chipDir);

    for (const rail of HardwareHealth.RAILS) {
      const millivolts = readInt(path.join(this.chipDir, `${rail.input}_input`));
      const volts = millivolts === null ? null : (millivolts * rail.scale) / 1000;
      await this.publishValue(rail.id, volts === null ? 'unknown' : volts.toFixed(2), {
        raw_millivolts: millivolts, hwmon_input: rail.input, scale: rail.scale,
      });
    }

    const { drift, worst } = this.maxDrift(rails);
    await this.publishValue('max_rail_drift', drift === null ? 'unknown' : drift.toFixed(2), {
      worst_rail: worst, rails_tracked: Object.keys(this.baseline).length,
    });

    const cpu = readCpuCounters();
    await this.publishValue('cpu_machine_checks', cpu.mce === null ? 'unknown' : String(cpu.mce), {
      source: '/proc/interrupts MCE', meaning: 'hardware faults reported by the CPU itself',
    });
    await this.publishValue(
      'cpu_thermal_events', cpu.thermal === null ? 'unknown' : String(cpu.thermal),
      { source: '/proc/interrupts THR' },
    );

    const memory = readMemoryErrors();
    await this.publishValue(
      'memory_errors',
      memory.supported ? String(memory.correctable + memory.uncorrectable) : 'unavailable',
      memory.supported
        ? { correctable: memory.correctable, uncorrectable: memory.uncorrectable, supported: true }
        : {
            supported: false,
            reason: 'no EDAC controller: non-ECC RAM, memory faults are not observable in software',
            how_to_check: 'memtest86+ offline run',
          },
    );

    await this.refreshDisks();
    await this.publishValue('disk_errors', String(this.disks.total), {
      per_disk: this.disks.perDisk,
      note: 'aggregate of media/reallocated/pending/CRC counters; SmartStatus holds the detail',
    });
  }

  private async refreshDisks(): Promise<void> {
    const now = Date.now();
    if (this.disksCheckedAt && now - this.disksCheckedAt < DISK_REFRESH_MS) return;
    this.disksCheckedAt = now;
    this.disks = await readDiskErrors(listDisks());
  }

  private async publishValue(id: string, state: string, attributes: object): Promise<void> {
    await this.mqttClient.publish(
      this.prefixTopic(`${this.featureName}/${id}/state`), state, { qos: 0, retain: true });
    await this.mqttClient.publish(
      this.prefixTopic(`${this.featureName}/${id}/attributes`),
      JSON.stringify(attributes), { qos: 0, retain: true });
  }

  async setup(): Promise<void> {
    this.chipDir = findChip(CHIP);
  }

  async cleanup(): Promise<void> {
    // No cleanup needed
  }
}
