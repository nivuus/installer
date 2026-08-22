// utils/winrm.ts
import winrm from 'nodejs-winrm';
import logger from './logger';
import { getConfigManager } from '../config';

interface WinRMConfig {
  hostname: string;
  username: string;
  password: string;
  port: number;
}

/**
 * WinRM Client for executing commands on Windows machines
 * Uses nodejs-winrm library for direct WinRM protocol communication
 * This avoids child_process issues when bundled with pkg
 */
export class WinRMClient {
  private config: WinRMConfig;

  constructor(config: WinRMConfig) {
    this.config = config;
  }

  /**
   * Execute a command on the remote Windows machine
   * @param command The command to execute
   * @returns Promise resolving to the command output
   */
  async executeCommand(command: string): Promise<string> {
    try {
      logger.debug(`[WinRM] Executing command: ${command}`);

      const output = await winrm.runCommand(
        command,
        this.config.hostname,
        this.config.username,
        this.config.password,
        this.config.port,
        true // returnStdout
      );

      logger.debug(`[WinRM] Command completed successfully`);
      return output;
    } catch (error) {
      logger.error('[WinRM] Command execution failed:', error);
      throw new Error(`WinRM execution failed: ${error}`);
    }
  }

  /**
   * Get NVIDIA GPU statistics via nvidia-smi
   * @returns CSV string with GPU metrics
   */
  async getNvidiaStats(): Promise<string> {
    const command = 'nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,power.draw,clocks.current.graphics,clocks.current.memory,fan.speed,pstate --format=csv,noheader,nounits';
    return this.executeCommand(command);
  }

  /**
   * Check if a Windows process is running
   * @param processName The name of the process (e.g., "sunshine.exe")
   * @returns Promise resolving to tasklist output
   */
  async getTasklist(processName: string): Promise<string> {
    // Use findstr instead of /FI filter for better compatibility with localized Windows
    const command = `tasklist /NH | findstr /i ${processName}`;
    return this.executeCommand(command);
  }

  /**
   * Check if a process is running
   * @param processName The name of the process
   * @returns True if process is running
   */
  async isProcessRunning(processName: string): Promise<boolean> {
    try {
      const output = await this.getTasklist(processName);
      // WinRM peut résoudre avec autre chose qu'une string (VM injoignable, réponse vide)
      return typeof output === 'string' && output.includes(processName);
    } catch (error) {
      logger.error(`[WinRM] Error checking process ${processName}:`, error);
      return false;
    }
  }
}

/**
 * Factory function to create a configured WinRMClient
 * Reads configuration from agent.yaml windows_vm section
 * Falls back to environment variables if not found in config
 */
export function createWinRMClient(): WinRMClient {
  const configManager = getConfigManager();
  const windowsVmConfig = configManager.config.windows_vm;

  const config: WinRMConfig = {
    hostname: windowsVmConfig?.hostname || process.env.WINDOWS_HOSTNAME || '192.168.3.2',
    username: windowsVmConfig?.username || process.env.WINDOWS_ADMIN_USERNAME || 'Administrateur',
    password: windowsVmConfig?.password || process.env.WINDOWS_ADMIN_PASSWORD || '',
    port: windowsVmConfig?.port || parseInt(process.env.WINDOWS_PORT || '5985', 10)
  };

  logger.info(`[WinRM] Creating client for ${config.hostname}:${config.port} as ${config.username}`);

  return new WinRMClient(config);
}
