"""Proxmox VE API client (read-only) + WireGuard status over SSH.

Only the standard library plus `requests` is used. All host metrics come from
the Proxmox REST API with an API token, so no root SSH into the hypervisor is
needed. WireGuard peer state is read by SSHing to the WG host and running
`wg show <iface> dump`.
"""

import subprocess
import time

import requests
import urllib3

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ProxmoxError(Exception):
    pass


def _base_url():
    return f"https://{config.PROXMOX['host']}:{config.PROXMOX['port']}/api2/json"


def _headers():
    tok = f"{config.PROXMOX['token_id']}={config.PROXMOX['token_secret']}"
    return {"Authorization": f"PVEAPIToken={tok}"}


def _get(path):
    url = _base_url() + path
    try:
        r = requests.get(
            url,
            headers=_headers(),
            verify=config.PROXMOX["verify_tls"],
            timeout=10,
        )
    except requests.RequestException as e:
        raise ProxmoxError(f"no se pudo contactar con Proxmox: {e}")
    if r.status_code == 401:
        raise ProxmoxError("API token rechazado (401). Revisa token_id/secret y el rol PVEAuditor.")
    if not r.ok:
        raise ProxmoxError(f"HTTP {r.status_code} en {path}: {r.text[:120]}")
    return r.json().get("data")


def node():
    """Nombre del nodo. Usa el de config o autodetecta el primero."""
    if config.PROXMOX["node"]:
        return config.PROXMOX["node"]
    data = _get("/nodes")
    if not data:
        raise ProxmoxError("la API no devolvió ningún nodo")
    return data[0]["node"]


def host_status():
    """CPU, RAM, núcleos, carga y uptime del host."""
    n = node()
    s = _get(f"/nodes/{n}/status")
    mem = s.get("memory", {})
    return {
        "node": n,
        "cpu_pct": round(s.get("cpu", 0) * 100, 1),
        "cores": s.get("cpuinfo", {}).get("cpus", 0),
        "loadavg": s.get("loadavg", ["?", "?", "?"]),
        "mem_used": mem.get("used", 0),
        "mem_total": mem.get("total", 0),
        "uptime": s.get("uptime", 0),
    }


def storages():
    """Lista de almacenamientos activos con uso en bytes."""
    n = node()
    out = []
    for st in _get(f"/nodes/{n}/storage") or []:
        if st.get("active") != 1 or not st.get("total"):
            continue
        total = st.get("total", 0)
        used = st.get("used", 0)
        out.append({
            "name": st.get("storage"),
            "used": used,
            "total": total,
            "pct": round(used / total * 100, 1) if total else 0,
        })
    return out


def guests():
    """CT (lxc) y VM (qemu) con su estado running/stopped."""
    n = node()
    out = []
    for kind, path in (("CT", "lxc"), ("VM", "qemu")):
        for g in _get(f"/nodes/{n}/{path}") or []:
            out.append({
                "vmid": g.get("vmid"),
                "name": g.get("name", "?"),
                "kind": kind,
                "status": g.get("status", "unknown"),
            })
    return sorted(out, key=lambda x: x["vmid"])


def services():
    """Servicios systemd del host que Proxmox expone, con su estado."""
    n = node()
    out = []
    for s in _get(f"/nodes/{n}/services") or []:
        out.append({
            "name": s.get("name"),
            "state": s.get("state", "unknown"),  # running / stopped / dead
        })
    return out


# ─────────────────────────── WireGuard ───────────────────────────

def wg_peers():
    """Peers del túnel leyendo `wg show <iface> dump` por SSH.

    Devuelve lista de dicts: endpoint, allowed_ips, last_handshake (epoch),
    online (bool), rx, tx. Lanza ProxmoxError si el SSH falla.
    """
    cmd = [
        "ssh", "-i", config.WG["ssh_key"],
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=/opt/telegrambot/known_hosts",
        "-o", "ConnectTimeout=6",
        f"{config.WG['user']}@{config.WG['host']}",
        f"wg show {config.WG['iface']} dump",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise ProxmoxError("timeout SSH al host WireGuard")
    if r.returncode != 0:
        raise ProxmoxError(f"SSH/wg falló: {r.stderr.strip()[:120]}")

    now = int(time.time())
    peers = []
    # Primera línea = la interfaz; el resto son peers.
    for line in r.stdout.strip().splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 8:
            continue
        hs = int(f[4]) if f[4].isdigit() else 0
        peers.append({
            "pubkey": f[0],
            "endpoint": f[2] if f[2] != "(none)" else "-",
            "allowed_ips": f[3],
            "last_handshake": hs,
            "online": hs > 0 and (now - hs) < config.HANDSHAKE_ONLINE_SEC,
            "ago": now - hs if hs else None,
            "rx": int(f[5]) if f[5].isdigit() else 0,
            "tx": int(f[6]) if f[6].isdigit() else 0,
        })
    return peers
