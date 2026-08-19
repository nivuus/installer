// src/features/health/hardwareProbes.ts
//
// Low-level readers behind the HardwareHealth feature. Kept apart so the feature
// itself stays about publishing, and so each probe can state plainly what it can
// and cannot see on this machine.

import * as fs from 'fs';
import * as path from 'path';
import { execute_argv } from '../../utils/exec';
import logger from '../../utils/logger';

const HWMON_ROOT = '/sys/class/hwmon';
const EDAC_ROOT = '/sys/devices/system/edac/mc';
const INTERRUPTS = '/proc/interrupts';

/** Resolve an hwmon directory by chip name — the indexes are not stable. */
export function findChip(name: string): string | null {
  try {
    for (const entry of fs.readdirSync(HWMON_ROOT).sort()) {
      const dir = path.join(HWMON_ROOT, entry);
      try {
        if (fs.readFileSync(path.join(dir, 'name'), 'utf8').trim() === name) return dir;
      } catch {
        // hwmon entries come and go; skip anything unreadable
      }
    }
  } catch (error) {
    logger.error('hardwareProbes: cannot list hwmon:', error);
  }
  return null;
}

export function readInt(file: string): number | null {
  try {
    const value = parseInt(fs.readFileSync(file, 'utf8').trim(), 10);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

/** Every in*_input the chip exposes, as raw millivolts. */
export function readRails(chipDir: string): Record<string, number> {
  const rails: Record<string, number> = {};
  try {
    for (const entry of fs.readdirSync(chipDir)) {
      if (!entry.startsWith('in') || !entry.endsWith('_input')) continue;
      const value = readInt(path.join(chipDir, entry));
      if (value !== null) rails[entry.replace('_input', '')] = value;
    }
  } catch {
    // fail open: an unreadable chip yields no rails rather than an exception
  }
  return rails;
}

/**
 * Machine-check and thermal interrupt counters, summed across CPUs.
 *
 * MCE counts hardware faults the CPU itself reported (uncorrectable memory,
 * cache or bus errors). THR counts thermal-throttle interrupts. These are the
 * only CPU-level fault visibility this box has.
 */
export function readCpuCounters(): { mce: number | null; thermal: number | null } {
  const totals: Record<string, number | null> = { MCE: null, THR: null };
  try {
    for (const line of fs.readFileSync(INTERRUPTS, 'utf8').split('\n')) {
      const [label, rest] = line.split(':');
      const key = label?.trim();
      if (key && key in totals && rest) {
        totals[key] = rest
          .trim()
          .split(/\s+/)
          .filter((token) => /^\d+$/.test(token))
          .reduce((sum, token) => sum + parseInt(token, 10), 0);
      }
    }
  } catch (error) {
    logger.error('hardwareProbes: cannot read /proc/interrupts:', error);
  }
  return { mce: totals.MCE, thermal: totals.THR };
}

/**
 * Correctable/uncorrectable memory errors.
 *
 * `supported` is false on this host and that is the point: the B660 board has
 * non-ECC DIMMs, igen6_edac loads but registers no controller, so memory faults
 * are physically unobservable in software. A silent 0 would read as "no memory
 * errors" when the truth is "memory errors cannot be seen" — only a memtest run
 * can rule RAM in or out. The probe reports the distinction explicitly.
 */
export function readMemoryErrors(): { supported: boolean; correctable: number; uncorrectable: number } {
  let supported = false;
  let correctable = 0;
  let uncorrectable = 0;
  try {
    for (const entry of fs.readdirSync(EDAC_ROOT)) {
      if (!entry.startsWith('mc')) continue;
      const ce = readInt(path.join(EDAC_ROOT, entry, 'ce_count'));
      const ue = readInt(path.join(EDAC_ROOT, entry, 'ue_count'));
      if (ce === null && ue === null) continue;
      supported = true;
      correctable += ce ?? 0;
      uncorrectable += ue ?? 0;
    }
  } catch {
    // no EDAC subsystem at all — same conclusion as no controller
  }
  return { supported, correctable, uncorrectable };
}

export interface DiskErrors {
  total: number;
  perDisk: Record<string, Record<string, number>>;
}

/** SMART attributes that mean real data loss or a degrading link, per device type. */
const NVME_FIELDS: Record<string, string> = {
  media_errors: 'media_errors',
  critical_warning: 'critical_warning',
  num_err_log_entries: 'error_log_entries',
};
const SATA_IDS: Record<number, string> = {
  5: 'reallocated_sectors',
  187: 'reported_uncorrect',
  197: 'pending_sectors',
  198: 'offline_uncorrectable',
  199: 'udma_crc_errors',
};

/**
 * Hard error counters across every disk, via smartctl JSON.
 *
 * Deliberately separate from the SmartStatus feature, which reports per-disk
 * health and wear: this yields one number to alarm on. smartctl forks per
 * device, so callers should refresh it on a slow cadence, not every update.
 */
export async function readDiskErrors(devices: string[]): Promise<DiskErrors> {
  const perDisk: Record<string, Record<string, number>> = {};
  let total = 0;

  for (const device of devices) {
    try {
      const result = await execute_argv('smartctl', ['-A', '-H', '-j', device]);
      if (!result.stdout) continue;
      const data = JSON.parse(result.stdout);
      const counters: Record<string, number> = {};

      const nvme = data.nvme_smart_health_information_log;
      if (nvme) {
        for (const [field, label] of Object.entries(NVME_FIELDS)) {
          const value = Number(nvme[field]);
          if (Number.isFinite(value)) counters[label] = value;
        }
      }

      for (const attr of data.ata_smart_attributes?.table ?? []) {
        const label = SATA_IDS[attr.id];
        const value = Number(attr.raw?.value);
        if (label && Number.isFinite(value)) counters[label] = value;
      }

      if (Object.keys(counters).length) {
        perDisk[device] = counters;
        total += Object.values(counters).reduce((sum, value) => sum + value, 0);
      }
    } catch (error) {
      logger.warn(`hardwareProbes: smartctl failed on ${device}: ${error}`);
    }
  }

  return { total, perDisk };
}

/** Block devices worth polling: every NVMe controller and every SATA disk. */
export function listDisks(): string[] {
  const devices: string[] = [];
  try {
    for (const entry of fs.readdirSync('/dev')) {
      if (/^nvme\d+$/.test(entry) || /^sd[a-z]$/.test(entry)) devices.push(`/dev/${entry}`);
    }
  } catch (error) {
    logger.error('hardwareProbes: cannot list /dev:', error);
  }
  return devices.sort();
}
