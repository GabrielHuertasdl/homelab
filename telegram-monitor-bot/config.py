"""Bot configuration.

Secrets and host-specific values come from environment variables (loaded by
systemd from /opt/telegrambot/.env — see .env.example). Nothing secret lives
in this file.
"""

import os


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _int_list(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


# ═══════════════════════════════════════════════════════════════════════
#  Required (.env)
# ═══════════════════════════════════════════════════════════════════════

# Telegram bot token (@BotFather -> /newbot)
BOT_TOKEN = _env("BOT_TOKEN")

# Chat ids allowed to talk to the bot (@userinfobot). Comma-separated.
# The bot ignores everyone else.
CHAT_ID = _int_list(_env("CHAT_ID"))

# Proxmox API token (Datacenter > Permissions > API Tokens), role PVEAuditor
# on path /. Read-only host metrics, no SSH into the hypervisor.
PROXMOX = {
    "host": _env("PROXMOX_HOST"),
    "port": int(_env("PROXMOX_PORT", "8006")),
    "node": os.environ.get("PROXMOX_NODE", ""),  # empty = first node
    "token_id": _env("PROXMOX_TOKEN_ID"),
    "token_secret": _env("PROXMOX_TOKEN_SECRET"),
    "verify_tls": os.environ.get("PROXMOX_VERIFY_TLS", "false").lower() == "true",
}

# WireGuard host (where `wg show` runs). The bot logs in over SSH with its own
# dedicated key, authorised only on that host.
WG = {
    "host": _env("WG_HOST"),
    "user": os.environ.get("WG_USER", "root"),
    "iface": os.environ.get("WG_IFACE", "wg0"),
    "ssh_key": os.environ.get("WG_SSH_KEY", "/opt/telegrambot/botmon_ed25519"),
}

# ═══════════════════════════════════════════════════════════════════════
#  Optional tuning
# ═══════════════════════════════════════════════════════════════════════

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))  # down-check interval
HANDSHAKE_ONLINE_SEC = int(os.environ.get("HANDSHAKE_ONLINE_SEC", "180"))

# VM/CT ids whose shutdown triggers an alert, e.g. "100,101"
VMID_WATCH = _int_list(os.environ.get("VMID_WATCH", ""))

# Proxmox host systemd services to watch, e.g. "pveproxy,pve-cluster,sshd"
SERVICE_WATCH = [s for s in os.environ.get("SERVICE_WATCH", "").split(",") if s]

# Label shown in message headers
SITE_NAME = os.environ.get("SITE_NAME", "Proxmox")
