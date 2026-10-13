# WireGuard: reaching a remote LAN that overlaps your local subnet

## Problem

The server LAN and my home LAN both use `192.168.1.0/24`. With a full-tunnel WireGuard client (`AllowedIPs = 0.0.0.0/0`) the server hosts were still unreachable while the tunnel was up.

## Why

`wg-quick` implements a full tunnel with policy routing: it installs its default route in a separate table and adds

```
ip rule add table main suppress_prefixlength 0
```

That rule lets the **main** table win for anything more specific than a default route. The kernel's connected route for the local network, `192.168.1.0/24 dev wlan0`, is more specific than `0.0.0.0/0`, so packets to `192.168.1.x` kept going to the home Wi-Fi instead of the tunnel.

## Fix

Add an explicit route for the remote subnet through the tunnel in the client config:

```ini
[Interface]
# ...
PostUp   = ip route add 192.168.1.0/24 dev %i
PostDown = ip route del 192.168.1.0/24 dev %i || true
```

The tunnel route is added with metric 0, so it beats the Wi-Fi route (metric 600) for the same prefix.

**Trade-off:** while the VPN is up, devices on the home LAN (router admin page, printer) are not reachable; bringing the tunnel down restores them. A narrower alternative is to route only the hosts you need with `/32` routes, which is how this started.

**Permanent fix:** renumber one of the two networks (for example the home router to `192.168.50.0/24`), which removes the overlap entirely.
