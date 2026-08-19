// src/features/updates/AptUpdates.ts

import { BaseFeature } from '../../core/BaseFeature';
import { MqttClient, FeatureConfig } from '../../core/types';
import { execute_command, execute_argv } from '../../utils/exec';
import logger from '../../utils/logger';

interface AptUpdatesConfig extends FeatureConfig {
  check_interval_hours?: number;
}

interface AptPackageUpdate {
  name: string;
  currentVersion: string;
  newVersion: string;
}

const DEFAULT_CHECK_INTERVAL_HOURS = 12;

export class AptUpdates extends BaseFeature {
  protected featureConfig: AptUpdatesConfig;
  private packages: AptPackageUpdate[] = [];
  private lastCheckTimestamp: number = 0;
  private installing: boolean = false;
  private boundMessageHandler: (topic: string, payload: Buffer) => Promise<void>;

  constructor(mqttClient: MqttClient, featureName: string = 'apt_updates') {
    super(mqttClient, featureName);
    this.featureConfig = this.agentConfig.features[featureName] as AptUpdatesConfig ||
      { enabled: true, check_interval_hours: DEFAULT_CHECK_INTERVAL_HOURS };

    const checkIntervalSeconds = (this.featureConfig.check_interval_hours || DEFAULT_CHECK_INTERVAL_HOURS) * 3600;
    if (!this.featureConfig.update_interval_seconds || this.featureConfig.update_interval_seconds > checkIntervalSeconds) {
      this.featureConfig.update_interval_seconds = checkIntervalSeconds;
    }

    this.boundMessageHandler = this.handleMessage.bind(this);
  }

  protected async publishDiscovery(): Promise<void> {
    await this.publishEntityDiscovery('update', 'apt', {
      name: 'System APT',
      state_topic: `${this.featureName}/apt/state`,
      command_topic: `${this.featureName}/apt/command`,
      payload_install: 'INSTALL',
      entity_category: 'config',
    });
  }

  protected async setup(): Promise<void> {
    const commandTopic = this.prefixTopic(`${this.featureName}/apt/command`);
    await this.mqttClient.subscribe(commandTopic);
    this.mqttClient.on('message', this.boundMessageHandler);
  }

  private async handleMessage(topic: string, payload: Buffer): Promise<void> {
    const expected = this.prefixTopic(`${this.featureName}/apt/command`);
    if (topic !== expected) return;

    const message = payload.toString();
    if (message === 'INSTALL') {
      logger.info('APT install command received');
      await this.installUpdates();
    }
  }

  protected async update(): Promise<void> {
    const now = Date.now();
    const intervalMs = (this.featureConfig.check_interval_hours || DEFAULT_CHECK_INTERVAL_HOURS) * 3600 * 1000;

    if (now - this.lastCheckTimestamp > intervalMs) {
      await this.checkForUpdates();
    } else {
      await this.publishCurrentState();
    }
  }

  private async checkForUpdates(): Promise<void> {
    logger.info('Checking for APT updates...');
    this.lastCheckTimestamp = Date.now();

    try {
      await execute_argv('sudo', ['apt-get', 'update']);
      // Shell required for the LC_ALL env assignment + 2>/dev/null; fixed literal,
      // no externally-controlled value is interpolated.
      const result = await execute_command('/bin/sh -c "LC_ALL=C apt list --upgradable 2>/dev/null"', false);
      this.packages = this.parseUpgradable(result.stdout);
      logger.info(`APT: ${this.packages.length} upgradable packages found`);
    } catch (error: any) {
      logger.error('Error checking APT updates:', error.message);
      this.packages = [];
    }

    await this.publishCurrentState();
  }

  private parseUpgradable(output: string): AptPackageUpdate[] {
    if (!output) return [];
    const packages: AptPackageUpdate[] = [];

    for (const line of output.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('Listing') || !trimmed.includes('/')) continue;

      // Format: package/release new_version arch [upgradable from: old_version]
      const nameEnd = trimmed.indexOf('/');
      const name = trimmed.substring(0, nameEnd);
      if (!name) continue;

      const afterSlash = trimmed.substring(nameEnd + 1).trim();
      const parts = afterSlash.split(/\s+/);
      // parts[0]=release, parts[1]=new_version, parts[2]=arch, ...
      const newVersion = parts[1] || 'unknown';

      const oldMatch = trimmed.match(/\[upgradable from:\s*([\S]+)\]/);
      const currentVersion = oldMatch ? oldMatch[1] : 'installed';

      packages.push({ name, newVersion, currentVersion });
    }

    return packages;
  }

  private buildReleaseSummary(): string {
    if (this.packages.length === 0) return '';

    const lines = ['### Upgradable Packages\n', '| Package | Current | New |', '|---------|---------|-----|'];
    for (const pkg of this.packages) {
      lines.push(`| ${pkg.name} | ${pkg.currentVersion} | ${pkg.newVersion} |`);
    }
    return lines.join('\n');
  }

  private async publishCurrentState(): Promise<void> {
    const hasUpdates = this.packages.length > 0;
    const state = {
      installed_version: hasUpdates ? 'Installed' : 'Up to date',
      latest_version: hasUpdates ? `${this.packages.length} packages` : 'Up to date',
      title: 'APT System Packages',
      release_summary: this.buildReleaseSummary(),
      in_progress: this.installing,
      entity_picture: 'https://upload.wikimedia.org/wikipedia/commons/thumb/7/7d/Antu_debian.svg/240px-Antu_debian.svg.png',
    };

    const fullTopic = this.prefixTopic(`${this.featureName}/apt/state`);
    await this.mqttClient.publish(fullTopic, JSON.stringify(state), { retain: true, qos: 1 });
  }

  private async installUpdates(): Promise<void> {
    if (this.installing || this.packages.length === 0) return;

    const dockerUpdated = this.packages.some(p => p.name.startsWith('docker'));
    // These apt commands need a shell because of the leading environment
    // assignments (DEBIAN_FRONTEND=...) and the Dpkg::Options quoting. They are
    // only ever built from fixed literals plus package names parsed from apt's
    // own output — never from MQTT / external input.
    const aptEnv = 'sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1';
    const dpkgOpts = '-o Dpkg::Options::="--force-confold"';
    const upgradeTimeout = 1800000; // 30 minutes

    this.installing = true;
    await this.publishCurrentState();

    try {
      // Recover from any previously interrupted dpkg
      logger.info('Recovering any interrupted dpkg state...');
      await execute_command(`${aptEnv} dpkg --configure -a`, false, upgradeTimeout);

      // Main upgrade
      logger.info(`Installing ${this.packages.length} APT updates...`);
      const result = await execute_command(
        `${aptEnv} apt-get dist-upgrade -y ${dpkgOpts}`,
        false, upgradeTimeout,
      );
      if (result.exitCode !== 0) {
        throw new Error(`apt-get dist-upgrade failed: ${result.stderr}`);
      }

      // Install held-back packages that dist-upgrade skipped (e.g. backports with new deps)
      const recheck = await execute_command('/bin/sh -c "LC_ALL=C apt list --upgradable 2>/dev/null"', false);
      const remaining = this.parseUpgradable(recheck.stdout);
      if (remaining.length > 0) {
        const pkgNames = remaining.map(p => p.name).join(' ');
        logger.info(`Installing ${remaining.length} held-back packages: ${pkgNames}`);
        await execute_command(
          `${aptEnv} apt-get install -y ${dpkgOpts} ${pkgNames}`,
          false, upgradeTimeout,
        );
      }

      logger.info('APT updates installed successfully');

      // Clean up unused packages (old kernels, orphaned deps)
      logger.info('Removing unused packages...');
      await execute_command(`${aptEnv} apt-get autoremove -y ${dpkgOpts}`, false, upgradeTimeout);

      // If Docker packages were updated, restart all compose projects to recover containers
      if (dockerUpdated) {
        await this.restartComposeProjects();
      }
    } catch (error: any) {
      logger.error('Error installing APT updates:', error.message);
    }

    this.installing = false;
    // Re-check to refresh state
    await this.checkForUpdates();
  }

  private async restartComposeProjects(): Promise<void> {
    logger.info('Docker packages updated — restarting compose projects...');

    // Wait for Docker daemon to be ready after upgrade
    for (let i = 0; i < 30; i++) {
      const check = await execute_argv('docker', ['info']);
      if (check.exitCode === 0) break;
      await new Promise(r => setTimeout(r, 2000));
    }

    // Find all running compose projects
    const result = await execute_argv('docker', ['compose', 'ls', '--format', 'json']);
    if (result.exitCode !== 0 || !result.stdout.trim()) {
      logger.warn('Could not list compose projects');
      return;
    }

    try {
      const projects = JSON.parse(result.stdout);
      for (const project of projects) {
        const configFile = project.ConfigFiles;
        if (!configFile) continue;
        logger.info(`Restarting compose project: ${project.Name} (${configFile})`);
        // Remove stuck containers and recreate
        await execute_argv('docker', ['compose', '-f', configFile, 'up', '-d', '--force-recreate'], { timeoutMs: 300000 });
      }
      logger.info('All compose projects restarted');
    } catch (error: any) {
      logger.error('Error restarting compose projects:', error.message);
    }
  }

  protected async cleanup(): Promise<void> {
    this.mqttClient.removeListener('message', this.boundMessageHandler);
  }
}
