# Orange IPv6 prefix on the LANs — design

Follows `2026-10-11-wan-orange-dhcp-design.md`. The WAN runs on Orange DHCP
(VLAN 832) and receives a delegated `/56`, parked behind an `unreachable`
route. This design routes it to the LANs.

## Goal

Devices on `localBridge` (home) and `publicBridge` (guests) get a public IPv6
address and reach the IPv6 Internet through the router. The router itself too.
IPv4 is not changed in any way.

## Decisions taken with the owner (2026-10-11)

- Both LANs get IPv6: `localBridge` and `publicBridge`. `internalBridge`, Docker
  and the VMs do not.
- The firewall posture is the IPv4 one. firewalld zones and policies are
  family-agnostic, so this is the existing configuration, unchanged: the
  router's services answer on IPv6 as they do on IPv4; nothing is forwarded
  from `external` to a LAN zone; `internet-traffic` (ANY → external) lets the
  LANs out. No rule is added. The IPv4 port forward to the VM stays IPv4-only.

## What was measured on the link (2026-10-11)

- DHCPv6 answers come from `fe80::ba0:bab`. The Router Advertisement carries a
  default router (lifetime 4500 s), no prefix, M and O clear, MTU 1540.
- Orange answers Router and Neighbor Solicitations **only after a full DHCPv6
  exchange** (Solicit, Advertise, Request, Reply). A start that finds a lease
  file only Confirms it; Orange acknowledges and stays silent on ND, so no
  default route can ever be learnt.
- RS, NS and NA must leave with 802.1p priority 6: already done by the
  `nivuus_wan_orange` nftables table.

## Design

1. **Every start of the DHCPv6 client Solicits.** The unit removes the lease
   file before starting. The DUID file is kept, so Orange hands back the same
   prefix. Because the client then has no "old prefix" to give its hook, the
   hook cleans up from the prefix it recorded itself.
2. **Default route by Router Advertisement.** On the WAN: `accept_ra=2`
   (forwarding is on), `accept_ra_defrtr=1`, `accept_ra_pinfo=0`, `autoconf=0`,
   `accept_ra_mtu=0` (1540 is above the link MTU). When there is a prefix but no
   default route, a Router Solicitation is sent with `rdisc6`.
3. **One `/64` per LAN, numbered like IPv4.** `localBridge` gets subnet `00`
   (192.168.**0**.0/24), `publicBridge` subnet `02` (192.168.**2**.0/24). The
   router takes `::1`. The mapping is a two-column file,
   `/etc/nivuus/wan-orange-lan6.conf`. Addresses are set with `ip`, because the
   bridges belong to NetworkManager profiles whose IPv6 is `manual` (a ULA) and
   `ignore`; a reconcile loop puts them back if NetworkManager reactivates a
   bridge.
4. **Router Advertisements by radvd**, with a configuration rendered from the
   mapping: exactly the delegated `/64`s, nothing else. Lifetimes are short
   (valid 7200 s, preferred 3600 s, router 900 s) so a stale prefix fades.
   No RDNSS: the LANs keep the IPv4 resolver they get by DHCPv4, which returns
   AAAA records.
5. **One long-running unit owns all of it**, `nivuus-wan-orange-lan6.service`:
   every 15 s it reconciles sysctls, route, addresses and radvd with the prefix
   file. Active means IPv6 is handed out; stopping it, or it crashing, withdraws
   everything (radvd's shutdown advertisement deprecates the prefixes and the
   router).
6. **IPv6 is withdrawn when it does not work.** No prefix or no default route:
   no address, no advertisement. A gateway that stops answering Neighbor
   Solicitations (`ndisc6`, four runs in a row) gets the DHCPv6 client
   restarted, which is a full exchange again and restarts the LAN unit with it.

## Choices and what was considered

- **radvd, not dnsmasq.** dnsmasq is installed and its `constructor:` ranges
  follow an interface's addresses by themselves, but they would also start
  advertising the ULA `/64` already on `localBridge`, and cannot be told which
  prefix to announce. radvd announces exactly what is rendered.
- **`rdisc6` and `ndisc6` (package `ndisc6`), not a hand-written sender.** They
  are the maintained tools for exactly this, and the reference Linux setup for
  Orange (Vincent Bernat, 2019) uses `rdisc6` the same way.
- **Not NetworkManager's `ipv6.method shared`.** It delegates only a prefix
  NetworkManager obtained itself, and the WAN is deliberately not managed by it.
- **A loop, not a dhclient hook.** The hook runs inside dhclient's AppArmor
  profile and only on DHCPv6 events; it cannot see a bridge being reset or a
  route expiring.

## Out of scope

IPv6 for Docker, the VMs and `internalBridge`; opening any inbound IPv6 service
towards a LAN device; DNS records (AAAA) for the router; RDNSS; the Wi-Fi
throughput and DNSSEC items of the original brief.

## Risks

- **Broken IPv6 is worse than none**: clients prefer it and wait before falling
  back. Hence point 6, and a deployment that proves IPv6 from the router before
  any advertisement reaches a LAN.
- **The router's own traffic will use IPv6** once it has a global address,
  including the sessions that operate it. Same mitigation.
- **Every device becomes globally addressable.** Protection is the firewall
  alone; the acceptance checks an inbound connection from outside is refused.
- **Orange may renumber.** The prefix has been stable; a change restarts radvd,
  whose shutdown advertisement deprecates the old prefix.

## Amendment after the branch review (2026-10-11)

- **Withdrawal does not depend on the loop surviving.** The unit runs
  `withdraw` in `ExecStopPost`, and the radvd unit is bound to it: a killed loop
  no longer leaves radvd advertising alone. Every external tool has a timeout.
- **A prefix without a default route is treated like a silent gateway**: it is
  the state Orange leaves behind when it forgets the exchange, and it now leads
  to a DHCPv6 restart too. Restarts back off (after 4, then 20, 60, 240 runs
  without a usable gateway), because each one sends a RELEASE and a SOLICIT to
  Orange and makes the LANs lose and regain their prefix.
- **Advertisements at most 60 s apart, not 300.** The `public` zone rejects
  Router Solicitations (firewalld's `allow-host-ipv6` accepts NS, NA, RA and
  redirects only), so a guest cannot ask and must wait for the periodic one.
  A short period keeps the "no firewall rule added" decision.
- **Open before advertising on `localBridge`:** the Thread border router
  (OTBR, `OTBR_BACKBONE_IF=localBridge`) may already send Router Advertisements
  there. Measured at deployment, before radvd starts.
