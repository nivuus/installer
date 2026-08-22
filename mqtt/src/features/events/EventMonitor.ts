// src/features/events/EventMonitor.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig, HaDiscoveryPayload } from '../../core/types';
import { execute_stream_command } from '../../utils/exec_stream'; // Changed import
import { ChildProcess } from 'child_process';
import logger from '../../utils/logger';

interface EventMonitorFeatureConfig extends FeatureConfig {
  fail2ban_log?: string;
  auth_log?: string;
  clamav_log?: string;
  syslog_watch_level?: ('error' | 'critical' | 'alert' | 'emergency')[]; // Syslog levels to monitor
  // cron_log_dir is harder to monitor generically, would need specific setup. Deferring.
}

const DEFAULT_FAIL2BAN_LOG = '/var/log/fail2ban.log';
const DEFAULT_AUTH_LOG = '/var/log/auth.log'; // Common, but /var/log/secure on others
const DEFAULT_SYSLOG = '/var/log/syslog'; // Common, but /var/log/messages on others
const DEFAULT_CLAMAV_LOG = '/var/log/clamav/clamonacc.log';
const DEFAULT_SYSLOG_LEVELS: ('error' | 'critical' | 'alert' | 'emergency')[] = ['error', 'critical'];

// The agent's own stdout ends up in journald/syslog. Any line it emits must be ignored
// by the syslog watchers, otherwise publishing an error event that mentions "Error:"
// re-triggers the watcher and creates an infinite self-feeding loop (2026-07-17:
// ~190k state updates in a few hours, message doubling in size at every iteration).
const SELF_LOG_TAG = 'mqtt-system-agent';
// Cap event fields so a single pathological log line cannot flood MQTT/HA subscribers.
const MAX_EVENT_FIELD_LENGTH = 500;

function truncateField(value: string): string {
  return value.length > MAX_EVENT_FIELD_LENGTH ? value.slice(0, MAX_EVENT_FIELD_LENGTH) + '…' : value;
}

// Store last seen lines/timestamps to avoid re-processing. Simplistic for mock.
// This might not be needed if we process streams directly.
// const lastProcessed: { [logPath: string]: string } = {}; 

export class EventMonitor extends BaseFeature {
  protected featureConfig: EventMonitorFeatureConfig;
  private logWatchers: ChildProcess[] = []; 
  private shuttingDown: boolean = false; // Added property

  constructor(mqttClient: MqttClient, featureName: string = 'event_monitor') {
    super(mqttClient, featureName);
    // update_interval_seconds is no longer relevant for log tailing, but keep for other potential events.
    this.featureConfig = this.agentConfig.features[featureName] as EventMonitorFeatureConfig ||
                         { 
                           enabled: true, 
                           fail2ban_log: DEFAULT_FAIL2BAN_LOG,
                           auth_log: DEFAULT_AUTH_LOG,
                           syslog_watch_level: DEFAULT_SYSLOG_LEVELS,
                         };
    // Ensure update_interval_seconds is not used for log tailing logic by setting it high or not using it in startLogWatchers
    this.featureConfig.update_interval_seconds = this.featureConfig.update_interval_seconds || 3600; // Default to 1hr if not set
  }

  private sanitizeEventPayload(payload: object): object {
    const clean: Record<string, any> = {};
    for (const [key, value] of Object.entries(payload)) {
      clean[key] = typeof value === 'string' ? truncateField(value) : value;
    }
    return clean;
  }

  private async publishEvent(eventType: string, rawPayload: object): Promise<void> {
    const payload = this.sanitizeEventPayload(rawPayload);
    const topic = this.prefixTopic(`events/${eventType}`);
    // For HA, events are typically fired via MQTT event topic or specific event components.
    // Here, we'll publish to a general topic and HA can use an MQTT trigger.
    // HA event entities are less common for arbitrary events like this, usually sensors/binary_sensors are used.
    // We can create a sensor that briefly shows the last event of each type.
    
    const objectId = `last_${eventType}_event`;
    // Ensure discovery for this event type's sensor if not already done
    // This is a simplified dynamic discovery. A real app might pre-declare or have a more robust way.
    // For now, we'll assume discovery is handled once.

    await this.mqttClient.publish(topic, JSON.stringify({ timestamp: new Date().toISOString(), ...payload }), { retain: false, qos: 1 });

    // Also update a "last event" sensor for HA dashboard
    await this.publishState(this.prefixTopic(`${this.featureName}/${objectId}/state`), new Date().toISOString(), true);
    await this.publishAttributes(this.prefixTopic(`${this.featureName}/${objectId}/attributes`), payload, true);

    // Never dump the payload here: this log line goes to journald/syslog and would be
    // picked up again by the syslog watcher (see SELF_LOG_TAG note above).
    logger.info(`Published event: ${eventType}`);
  }

  protected async publishDiscovery(): Promise<void> {
    const eventTypes = [
      { id: 'fail2ban_ban', name: 'Security Last Fail2ban Ban Sensor', icon: 'mdi:bell-alert' },
      { id: 'ssh_login', name: 'Security Last SSH Login Sensor', icon: 'mdi:bell-alert' },
      { id: 'syslog_error', name: 'System Last Syslog Error Sensor', icon: 'mdi:bell-alert' },
      { id: 'clamav_virus', name: 'Security Last ClamAV Virus Sensor', icon: 'mdi:virus' }
    ];
    for (const eventType of eventTypes) {
      const objectId = `last_${eventType.id}_event`;
      await this.publishEntityDiscovery('sensor', objectId, {
        name: `${eventType.name}`,
        state_topic: this.prefixTopic(`${this.featureName}/${objectId}/state`), // Timestamp of last event
        json_attributes_topic: this.prefixTopic(`${this.featureName}/${objectId}/attributes`), // Event details
        icon: eventType.icon,
        entity_category: 'diagnostic' as 'diagnostic',
      });
    }
  }

  protected async update(): Promise<void> {
    // Initialize states with last known events from log files
    // This runs periodically to ensure states are always current
    await this.initializeLastEvents();
  }

  private async initializeLastEvents(): Promise<void> {
    try {
      // Get last fail2ban event
      if (this.featureConfig.fail2ban_log) {
        const lastBan = await this.getLastLogMatch(
          this.featureConfig.fail2ban_log,
          /Ban\s+([\d.]+)/,
          (match) => ({ subType: 'ban', ip: match[1] })
        );
        if (lastBan) {
          await this.publishState(this.prefixTopic(`${this.featureName}/last_fail2ban_ban_event/state`), lastBan.timestamp, true);
          await this.publishAttributes(this.prefixTopic(`${this.featureName}/last_fail2ban_ban_event/attributes`), lastBan.data, true);
        }
      }

      // Get last SSH login from journalctl (more reliable on systemd systems)
      const lastSsh = await this.getLastJournalMatch(
        'sshd',
        /Accepted publickey for (\w+) from ([\d.]+) port (\d+)/,
        (match, line) => ({ subType: 'login', user: match[1], ip: match[2], method: 'publickey' })
      );
      if (lastSsh) {
        await this.publishState(this.prefixTopic(`${this.featureName}/last_ssh_login_event/state`), lastSsh.timestamp, true);
        await this.publishAttributes(this.prefixTopic(`${this.featureName}/last_ssh_login_event/attributes`), lastSsh.data, true);
      }

      // Get last syslog error
      const lastSyslogError = await this.getLastLogMatch(
        DEFAULT_SYSLOG,
        /(error|critical)/i,
        (match, line) => ({ subType: 'error', level: match[1].toLowerCase(), message: truncateField(line.substring(line.indexOf(match[1]))) })
      );
      if (lastSyslogError) {
        await this.publishState(this.prefixTopic(`${this.featureName}/last_syslog_error_event/state`), lastSyslogError.timestamp, true);
        await this.publishAttributes(this.prefixTopic(`${this.featureName}/last_syslog_error_event/attributes`), lastSyslogError.data, true);
      } else {
        // No recent errors found - publish "No recent errors" state
        await this.publishState(this.prefixTopic(`${this.featureName}/last_syslog_error_event/state`), 'No recent errors', true);
        await this.publishAttributes(this.prefixTopic(`${this.featureName}/last_syslog_error_event/attributes`), {
          subType: 'no_error',
          level: 'info',
          message: 'No errors found in last 100 syslog lines'
        }, true);
      }

      // Get last ClamAV virus detection
      const clamavLog = this.featureConfig.clamav_log || DEFAULT_CLAMAV_LOG;
      const lastVirus = await this.getLastLogMatch(
        clamavLog,
        /^(.+):\s+(.+)\s+FOUND$/,
        (match) => ({ subType: 'virus_found', file: match[1], virus_name: match[2] })
      );
      if (lastVirus) {
        await this.publishState(this.prefixTopic(`${this.featureName}/last_clamav_virus_event/state`), lastVirus.timestamp, true);
        await this.publishAttributes(this.prefixTopic(`${this.featureName}/last_clamav_virus_event/attributes`), lastVirus.data, true);
      } else {
        // No viruses detected - publish "No viruses detected" state
        await this.publishState(this.prefixTopic(`${this.featureName}/last_clamav_virus_event/state`), 'No viruses detected', true);
        await this.publishAttributes(this.prefixTopic(`${this.featureName}/last_clamav_virus_event/attributes`), {
          subType: 'no_virus',
          level: 'info',
          message: 'No virus detections in last 100 ClamAV log lines'
        }, true);
      }
    } catch (error) {
      logger.error('Error initializing last events:', error);
    }
  }

  private async getLastLogMatch(
    logPath: string,
    pattern: RegExp,
    dataExtractor: (match: RegExpMatchArray, line: string) => any
  ): Promise<{ timestamp: string; data: any } | null> {
    try {
      const { execute_argv } = await import('../../utils/exec');
      // Use tail to get last 100 lines; reverse in JS (replaces the shell `| tac`)
      // so the most recent line is processed first. Use sudo for root-only logs.
      const result = await execute_argv('sudo', ['tail', '-n', '100', logPath]);
      const lines = result.stdout.split('\n').reverse();

      for (const line of lines) {
        if (line.includes(SELF_LOG_TAG)) continue; // Anti-feedback: skip our own log lines
        const match = line.match(pattern);
        if (match) {
          // Extract timestamp from syslog format (e.g., "Nov  8 15:30:45")
          const timestampMatch = line.match(/^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})/);
          const timestamp = timestampMatch ? timestampMatch[1] : new Date().toISOString();

          return {
            timestamp: new Date().toISOString(), // Use current time as we don't have full date in syslog
            data: { ...dataExtractor(match, line), original_log_line: truncateField(line) }
          };
        }
      }
    } catch (error) {
      logger.debug(`Could not read last event from ${logPath}:`, error);
    }
    return null;
  }

  private async getLastJournalMatch(
    unit: string,
    pattern: RegExp,
    dataExtractor: (match: RegExpMatchArray, line: string) => any
  ): Promise<{ timestamp: string; data: any } | null> {
    try {
      const { execute_argv } = await import('../../utils/exec');
      // Use journalctl to get last 100 entries for the specified unit
      const result = await execute_argv('sudo', ['journalctl', '-t', unit, '-n', '100', '--no-pager', '--output=short-iso']);
      const lines = result.stdout.split('\n').reverse(); // Reverse to get most recent first

      for (const line of lines) {
        const match = line.match(pattern);
        if (match) {
          // Extract ISO timestamp from journalctl output (e.g., "2025-11-08T15:30:45+0100")
          const timestampMatch = line.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})/);
          const timestamp = timestampMatch ? new Date(timestampMatch[1]).toISOString() : new Date().toISOString();

          return {
            timestamp,
            data: { ...dataExtractor(match, line), original_log_line: line }
          };
        }
      }
    } catch (error) {
      logger.debug(`Could not read last event from journalctl unit ${unit}:`, error);
    }
    return null;
  }

  protected async setup(): Promise<void> {
    this.shuttingDown = false;
    this.stopLogWatchers();
    this.startLogWatchers();
  }

  private startLogWatcher(logPath: string, eventTypePrefix: string, lineParser: (line: string) => object | null): void {
    if (!logPath) return;

    try {
      logger.info(`Starting log watcher for ${logPath} (${eventTypePrefix})`);
      const tailProcess = execute_stream_command('tail', ['-F', '-n', '0', logPath]); // -n 0 to only get new lines
      this.logWatchers.push(tailProcess);

      tailProcess.stdout?.on('data', (data: Buffer) => {
        const lines = data.toString().split('\n');
        for (const line of lines) {
          if (line.trim() === '') continue;
          const parsedEvent = lineParser(line);
          if (parsedEvent) {
            let eventType = eventTypePrefix;
            if ((parsedEvent as any).subType) { // Allow parser to define a sub-type
                eventType = `${eventTypePrefix}_${(parsedEvent as any).subType}`;
            }
            this.publishEvent(eventType, { ...parsedEvent, original_log_line: line });
          }
        }
      });

      tailProcess.stderr?.on('data', (data: Buffer) => {
        logger.warn(`Stderr from tail for ${logPath}: ${data.toString()}`);
      });

      tailProcess.on('exit', (code, signal) => {
        logger.info(`Log watcher for ${logPath} exited with code ${code}, signal ${signal}.`);
        // Optionally, try to restart it, or remove from this.logWatchers
        this.logWatchers = this.logWatchers.filter(p => p !== tailProcess);
        // Basic restart logic (could be improved with backoff)
        if (!this.shuttingDown) {
            logger.info(`Restarting log watcher for ${logPath}...`);
            setTimeout(() => this.startLogWatcher(logPath, eventTypePrefix, lineParser), 5000);
        }
      });
    } catch (error) {
      logger.error(`Failed to start log watcher for ${logPath}:`, error);
    }
  }

  private startLogWatchers(): void {
    // Fail2Ban
    if (this.featureConfig.fail2ban_log) {
      this.startLogWatcher(this.featureConfig.fail2ban_log, 'fail2ban', (line) => {
        if (line.includes('Ban ')) {
          const ipMatch = line.match(/Ban\s+([\d.]+)/);
          if (ipMatch && ipMatch[1]) return { subType: 'ban', ip: ipMatch[1] };
        } else if (line.includes('Unban ')) {
            const ipMatch = line.match(/Unban\s+([\d.]+)/);
            if (ipMatch && ipMatch[1]) return { subType: 'unban', ip: ipMatch[1] };
        }
        return null;
      });
    }

    // SSH Login
    if (this.featureConfig.auth_log) {
      this.startLogWatcher(this.featureConfig.auth_log, 'ssh', (line) => {
        // Accepted public key
        if (line.includes('sshd') && line.includes('Accepted publickey for')) {
          const userMatch = line.match(/Accepted publickey for\s+(\S+)\s+from\s+([\d.]+)/);
          if (userMatch && userMatch[1] && userMatch[2]) {
            return { subType: 'login', method: 'publickey', user: userMatch[1], ip: userMatch[2] };
          }
        }
        // Accepted password
        else if (line.includes('sshd') && line.includes('Accepted password for')) {
          const userMatch = line.match(/Accepted password for\s+(\S+)\s+from\s+([\d.]+)/);
          if (userMatch && userMatch[1] && userMatch[2]) {
            return { subType: 'login', method: 'password', user: userMatch[1], ip: userMatch[2] };
          }
        }
        // Session opened (generic, often follows an accepted login)
        else if (line.includes('sshd') && line.includes('session opened for user')) {
            const userMatch = line.match(/session opened for user\s+(\S+)\s+by\s+\(uid=(\d+)\)/);
            // This log often doesn't contain the IP directly, but it confirms a session.
            // We might have already captured the IP from the "Accepted" log line.
            // For simplicity, we'll report it, but it might be redundant if an "Accepted" line was also parsed.
            if (userMatch && userMatch[1]) {
                 return { subType: 'session_opened', user: userMatch[1] };
            }
        }
        // Failed password
        else if (line.includes('sshd') && line.includes('Failed password for')) {
            const parts = line.match(/Failed password for (invalid user\s+)?(\S+)\s+from\s+([\d.]+)/);
            if (parts && parts[3]) {
                 return { subType: 'login_failed', user: parts[2], ip: parts[3], invalid_user: !!parts[1] };
            }
        }
        return null;
      });
    }
    
    // Syslog Errors
    const syslogPath = DEFAULT_SYSLOG; 
    const levelsToWatch = this.featureConfig.syslog_watch_level || DEFAULT_SYSLOG_LEVELS;
    if (syslogPath && levelsToWatch.length > 0) {
      const regex = new RegExp(`(${levelsToWatch.join('|')}):`, 'i');
      this.startLogWatcher(syslogPath, 'syslog', (line) => {
        if (line.includes(SELF_LOG_TAG)) return null; // Anti-feedback: skip our own log lines
        const match = line.match(regex);
        if (match && match[1]) {
          return { subType: 'error', level: match[1].toLowerCase(), message: truncateField(line.substring(line.indexOf(match[0]))) };
        }
        return null;
      });
    }
    // Note: Cron job monitoring is more complex and not implemented here.
    // It would typically involve watching specific log files for cron outputs or using a wrapper for cron jobs.
  }


  private stopLogWatchers(): void {
    if (this.logWatchers.length > 0) {
      logger.info(`Stopping ${this.logWatchers.length} log watcher(s)...`);
      this.logWatchers.forEach(watcher => {
        if (!watcher.killed) {
          watcher.kill('SIGTERM');
          logger.debug(`Sent SIGTERM to watcher PID ${watcher.pid}`);
        }
      });
      this.logWatchers = [];
    }
  }

  protected async cleanup(): Promise<void> {
    logger.info(`Cleaning up ${this.featureName}. Stopping log watchers...`);
    this.shuttingDown = true;
    this.stopLogWatchers();
    logger.debug(`${this.featureName} cleanup complete.`);
  }
}
