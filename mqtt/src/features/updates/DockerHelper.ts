// src/features/updates/DockerHelper.ts

import { execute_argv } from '../../utils/exec';
import logger from '../../utils/logger';
import https from 'https';
import http from 'http';

export interface ContainerInfo {
  id: string;
  name: string;
  serviceName: string;
  image: string;
  imageId: string;
  composeFile: string;
  composeService: string;
  installedVersion: string;
  sourceUrl: string;
}

export interface UpdateCheckResult {
  hasUpdate: boolean;
  newVersion: string;
  pullOutput: string;
}

export interface ChangelogResult {
  releaseSummary: string;
  releaseUrl: string;
}

/**
 * Lists running Docker containers with the watchtower enable label.
 */
export async function listWatchtowerContainers(): Promise<ContainerInfo[]> {
  const format = '{{.ID}}|{{.Names}}|{{.Image}}';
  const result = await execute_argv('docker', [
    'ps', '--filter', 'label=com.centurylinklabs.watchtower.enable=true', '--format', format,
  ]);

  if (result.exitCode !== 0 || !result.stdout.trim()) return [];

  const containers: ContainerInfo[] = [];

  for (const line of result.stdout.trim().split('\n')) {
    const [id, name, image] = line.split('|');
    if (!id || !name) continue;

    const info = await inspectContainer(id, name, image);
    if (info) containers.push(info);
  }

  return containers;
}

async function inspectContainer(id: string, name: string, image: string): Promise<ContainerInfo | null> {
  const format = [
    '{{index .Config.Labels "com.docker.compose.service"}}',
    '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
    '{{index .Config.Labels "org.opencontainers.image.version"}}',
    '{{index .Config.Labels "org.opencontainers.image.source"}}',
    '{{.Image}}',
    '{{.Config.Image}}',
  ].join('|');

  const result = await execute_argv('docker', ['inspect', '--format', format, id]);
  if (result.exitCode !== 0 || !result.stdout.trim()) return null;

  const parts = result.stdout.trim().split('|');
  const composeService = parts[0] || name;
  const composeFile = (parts[1] || '').split(',')[0]; // Take first compose file
  const version = parts[2] || '';
  const sourceUrl = parts[3] || '';
  const imageId = parts[4] || '';
  const configImage = parts[5] || ''; // Full image name from Config.Image

  // Use Config.Image (e.g. "linuxserver/plex:latest") instead of docker ps Image
  // which can show a short hash when the tag has moved
  const resolvedImage = configImage || image;

  return {
    id,
    name,
    serviceName: composeService,
    image: resolvedImage,
    imageId,
    composeFile,
    composeService,
    installedVersion: version || imageId.substring(7, 19), // Fallback to short digest
    sourceUrl,
  };
}

/**
 * Pulls the latest image and checks if an update is available by comparing
 * the running container's image ID with the latest pulled image ID.
 */
export async function checkContainerUpdate(container: ContainerInfo): Promise<UpdateCheckResult> {
  const result = await execute_argv('docker', ['pull', container.image], { timeoutMs: 300000 });
  const output = result.stdout + result.stderr;

  if (result.exitCode !== 0 && !/Status:/i.test(output)) {
    logger.warn(`Docker pull failed for ${container.image}: ${output}`);
    return { hasUpdate: false, newVersion: container.installedVersion, pullOutput: output };
  }

  // Compare the running container's image ID with the latest local image ID
  const latestImageId = await getImageId(container.image);
  if (latestImageId && latestImageId !== container.imageId) {
    const newVersion = await getImageVersion(container.image);
    return {
      hasUpdate: true,
      newVersion: newVersion || 'new',
      pullOutput: output,
    };
  }

  return { hasUpdate: false, newVersion: container.installedVersion, pullOutput: output };
}

/**
 * Gets the full image ID (sha256:...) of a local image.
 */
async function getImageId(image: string): Promise<string> {
  const result = await execute_argv('docker', ['inspect', '--format', '{{.Id}}', image]);
  return result.exitCode === 0 ? result.stdout.trim() : '';
}

/**
 * Reads the version label from a pulled image (not a running container).
 */
async function getImageVersion(image: string): Promise<string> {
  const result = await execute_argv('docker', [
    'inspect', '--format', '{{index .Config.Labels "org.opencontainers.image.version"}}', image,
  ]);
  const version = result.stdout.trim();
  return version && version !== '<no value>' ? version : '';
}

/**
 * Fetches changelog from GitHub releases API.
 */
export async function fetchChangelog(sourceUrl: string): Promise<ChangelogResult> {
  const empty: ChangelogResult = { releaseSummary: '', releaseUrl: '' };
  if (!sourceUrl) return empty;

  // Extract owner/repo from GitHub URL
  const match = sourceUrl.match(/github\.com\/([^/]+\/[^/]+)/);
  if (!match) return empty;

  const ownerRepo = match[1].replace(/\.git$/, '');
  const releaseUrl = `https://github.com/${ownerRepo}/releases`;

  try {
    const apiUrl = `https://api.github.com/repos/${ownerRepo}/releases/latest`;
    const body = await httpGet(apiUrl);
    const data = JSON.parse(body);

    if (data.body) {
      return { releaseSummary: data.body, releaseUrl };
    }
  } catch (error: any) {
    logger.debug(`Failed to fetch changelog for ${ownerRepo}: ${error.message}`);
  }

  return { releaseSummary: '', releaseUrl };
}

/**
 * Recreates a container via docker compose, then restarts dependent services.
 */
export async function updateContainer(container: ContainerInfo): Promise<boolean> {
  if (!container.composeFile || !container.composeService) {
    logger.error(`Cannot update ${container.name}: missing compose info`);
    return false;
  }

  // Pull the service image via compose
  const pullResult = await execute_argv('docker', [
    'compose', '-f', container.composeFile, 'pull', container.composeService,
  ], { timeoutMs: 300000 });
  if (pullResult.exitCode !== 0) {
    logger.error(`Failed to pull ${container.composeService}: ${pullResult.stderr}`);
    return false;
  }

  // Recreate the service
  const upResult = await execute_argv('docker', [
    'compose', '-f', container.composeFile, 'up', '-d', container.composeService,
  ], { timeoutMs: 300000 });
  if (upResult.exitCode !== 0) {
    logger.error(`Failed to recreate ${container.composeService}: ${upResult.stderr}`);
    return false;
  }

  logger.info(`Successfully updated container ${container.name}`);

  // Restart services that depend on this one
  const dependents = await getDependentServices(container.composeFile, container.composeService);
  if (dependents.length > 0) {
    logger.info(`Restarting dependent services: ${dependents.join(', ')}`);
    await execute_argv('docker', [
      'compose', '-f', container.composeFile, 'restart', ...dependents,
    ], { timeoutMs: 300000 });
  }

  return true;
}

/**
 * Finds services that depend on the given service in a compose file.
 */
async function getDependentServices(composeFile: string, serviceName: string): Promise<string[]> {
  const result = await execute_argv('docker', ['compose', '-f', composeFile, 'config', '--format', 'json']);
  if (result.exitCode !== 0 || !result.stdout.trim()) return [];

  try {
    const config = JSON.parse(result.stdout);
    const dependents: string[] = [];

    for (const [name, service] of Object.entries(config.services || {})) {
      const deps = (service as any).depends_on;
      if (!deps) continue;

      // depends_on can be an array or an object
      const depNames = Array.isArray(deps) ? deps : Object.keys(deps);
      if (depNames.includes(serviceName)) {
        dependents.push(name);
      }
    }

    return dependents;
  } catch (error: any) {
    logger.warn(`Failed to parse compose config: ${error.message}`);
    return [];
  }
}

function httpGet(url: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https') ? https : http;
    const req = client.get(url, { headers: { 'User-Agent': 'NivuusAgent/1.0' } }, (res) => {
      if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return httpGet(res.headers.location).then(resolve).catch(reject);
      }
      if (res.statusCode && res.statusCode >= 400) {
        return reject(new Error(`HTTP ${res.statusCode}`));
      }
      let data = '';
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => resolve(data));
      res.on('error', reject);
    });
    req.on('error', reject);
    req.setTimeout(10000, () => { req.destroy(); reject(new Error('Timeout')); });
  });
}
