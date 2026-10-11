# WAN Orange: from PPPoE (VLAN 835) to DHCP (VLAN 832) — design

Date: 2026-10-11. Scope: the reference host only (`enp5s0`, Orange/Sosh FTTH).

## Why

The LAN has no public IPv6. Orange delivers IPv6 on fibre through DHCPv6-PD on
VLAN 832; its PPPoE service on VLAN 835 is IPv4 only and has been the fallback
mode since 2016. Getting IPv6 therefore means moving the WAN, not adding a
setting. Side benefits: MTU 1500 instead of 1492, and no PPPoE decapsulation,
which today pins all inbound traffic to one core (88 % softirq on one E-core at
830-900 Mbit/s forwarded).

## What was measured on this line (2026-10-11)

- **Orange answers DHCP on VLAN 832 once PPPoE is down.** PADT at 18:00:48, OFFER
  at 18:01:11: `90.114.164.116/21`, router `90.114.160.1`, server
  `80.10.233.133`, lease 259200 s, options 1, 3, 6, 28, 51, 58, 59, 61, 119,
  125. A public address, not a `172.19.x.y` parking address: the authentication
  built from the PPPoE credentials is accepted. No lease was bound.
- **The two modes are exclusive.** A DISCOVER on 832 while PPPoE is up gets no
  answer, and Orange terminates the PPPoE session (`LCP terminated by peer` at
  17:50:02 and 17:51:25, the instants of two probes). NetworkManager redialled
  in 2 s with the same address, which hid it: read `journalctl -t pppd` after
  any experiment, never only the address and a ping.
- **A clean PPPoE teardown costs a cooldown.** Orange rejects reconnections for
  3-5 minutes after a PADT; a manual `nmcli con up` succeeded 4 min 12 s later.
- The frame that was answered: 802.1Q id 832 priority 6, options 53, 61
  (`01` + MAC), 60 (`sagem`), 77 (`+FSVDSL_livebox.Internet.softathome.Livebox5`),
  90 (70 bytes), 55 (`1,3,6,15,28,51,58,59,90,119,120,125`).

## What Orange requires

From the lafibre.info conformity thread (opened 2022-11-07, last edited
2025-09-04) and the two configurations it agrees with (akhamar
`orange-bypass-debian`, OPNsense how-to):

- Option 90 (DHCPv4) and option 11 (DHCPv6) carry the same bytes: 11 zero
  bytes, `1a 09 00 00 05 58 01 03 41`, `01 <len+2> <login>`,
  `3c 12 <16-char salt>`, `03 13 <1-char id> <MD5(id + password + salt)>`.
- DHCPv6: DUID-LL (`00:03:00:01` + MAC), option 15 = `00 2b` + the user class,
  option 16 = `00 00 04 0e 00 05` + `sagem`, IA_PD only, no Rapid Commit.
- 802.1p priority 6 on DHCPv4, DHCPv6, ARP and ICMPv6 RS/NS/NA; **0 on
  everything else**, DSCP included.
- Both protocols must stay coherent. An inconsistent client is parked on a
  non-routable address or has its line disconnected. Stop with a RELEASE.
- Liveness: if the gateway stops answering for 150 s, restart from DISCOVER.

## Decisions

1. **Client: ISC `dhclient`**, already installed (4.4.3-P1). It is the only
   client with a complete, current, Debian-specific Orange recipe.
   `systemd-networkd` has a reported working setup and is maintained, but
   handing the delegated prefix to `localBridge` would make two network
   managers share that bridge. Named debt: `dhclient` is end-of-life upstream.
2. **Priority is set on the VLAN, not in the client.** `tc` egress filters on
   `enp5s0.832` mark control traffic 6 and everything else 0, so the rule holds
   for any client, for the kernel's own ARP and ND, and for forwarded traffic
   whose TOS would otherwise map to priority 6.
3. **NetworkManager does not manage `enp5s0.832`.** Three systemd units own it:
   one prepares the interface, two run the DHCP clients in the foreground.
4. **PPPoE stays installed and is the rollback.** Its profile only loses
   `autoconnect`. The cutover script falls back to it by itself.
5. **Two plans.** Plan 1 moves the WAN: IPv4 in service, the /56 obtained and
   parked behind an `unreachable` route, no IPv6 default route. Plan 2 hands
   IPv6 to the LAN: address on `localBridge`, router advertisements, IPv6
   default route and firewall policy, all together. The seam is safety: until
   the firewall policy exists, no IPv6 packet may be forwarded to Orange.
6. **The operator runs the cutover.** It cuts the uplink the Claude session
   depends on, and the session's classifier refuses WAN changes. The script
   detaches, needs nobody, and requires the phone tether to be up first.

## Out of scope

IPTV and VoIP VLANs; the installer wizard (which only knows PPPoE); the `mqtt`
agent's `PppoeCredentials` feature, noted as a follow-up in that repository.
