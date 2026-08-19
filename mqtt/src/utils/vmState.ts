// src/utils/vmState.ts

import { execute_argv } from './exec';
import logger from './logger';

let cachedState: { state: string; timestamp: number } | null = null;
const CACHE_TTL_MS = 30000; // 30s cache

export async function isWindowsVmRunning(): Promise<boolean> {
  const now = Date.now();
  if (cachedState && (now - cachedState.timestamp) < CACHE_TTL_MS) {
    return cachedState.state === 'running';
  }
  try {
    const result = await execute_argv('virsh', ['-c', 'qemu:///system', 'domstate', 'Windows']);
    const state = result.stdout.trim();
    cachedState = { state, timestamp: now };
    return state === 'running';
  } catch {
    cachedState = { state: 'unknown', timestamp: now };
    return false;
  }
}
