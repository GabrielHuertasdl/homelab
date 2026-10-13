#!/usr/bin/env python3
"""Telegram bot: Proxmox host metrics, down alerts, WireGuard peers.

Interfaz: panel de botones (inline keyboard). Pulsas Estado / Disco / VPN /
Servicios y se muestra el panel; también sirven los comandos de texto.

Diseño de mensajes: paneles monoespaciados, sin emojis, sobrios.
Notificaciones automáticas: SOLO cuando algo cae (servicio o VM/CT que pasa a
parado). No hay mensajes de recuperación ni de disco: nada más.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
import proxmox

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("telegrambot")

AUTHORIZED = config.CHAT_ID if isinstance(config.CHAT_ID, (list, tuple)) else [config.CHAT_ID]

# Estado previo para detectar caídas (edge-triggered).
_prev = {"services": {}, "guests": {}}


# ─────────────────────────── formato ───────────────────────────

def _human(nbytes):
    n = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


def _uptime(sec):
    d, rem = divmod(int(sec), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def _ago(sec):
    if sec is None:
        return "nunca"
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def _panel(title, subtitle, rows):
    """Panel monoespaciado: título, línea, filas [(label, value), ...]."""
    width = 34
    head = title if not subtitle else f"{title}   {subtitle}"
    lines = [head, "─" * width]
    lw = max((len(r[0]) for r in rows), default=0)
    for label, value in rows:
        lines.append(f"{label.ljust(lw)}  {value}")
    return "```\n" + "\n".join(lines) + "\n```"


def _kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Estado", callback_data="status"),
         InlineKeyboardButton("Disco", callback_data="disk")],
        [InlineKeyboardButton("VPN", callback_data="vpn"),
         InlineKeyboardButton("Servicios", callback_data="servicios")],
    ])


def _authorized_id(chat_id) -> bool:
    return chat_id in AUTHORIZED


# ─────────────────────── constructores de panel ───────────────────────
# Devuelven texto ya formateado; capturan sus propios errores.

def build_status():
    try:
        s = proxmox.host_status()
    except proxmox.ProxmoxError as e:
        return f"No disponible: {e}"
    mem_pct = round(s["mem_used"] / s["mem_total"] * 100) if s["mem_total"] else 0
    la = " ".join(f"{float(x):.2f}" for x in s["loadavg"][:3])
    rows = [
        ("CPU", f"{s['cpu_pct']} %"),
        ("RAM", f"{_human(s['mem_used'])} / {_human(s['mem_total'])}  ({mem_pct}%)"),
        ("Núcleos", str(s["cores"])),
        ("Carga", la),
        ("Uptime", _uptime(s["uptime"])),
    ]
    return _panel(config.SITE_NAME.upper(), s["node"], rows)


def build_disk():
    try:
        sts = proxmox.storages()
    except proxmox.ProxmoxError as e:
        return f"No disponible: {e}"
    if not sts:
        return "Sin almacenamientos activos."
    rows = [
        (st["name"], f"{_human(st['used'])} / {_human(st['total'])}  ({st['pct']:.0f}%)")
        for st in sts
    ]
    return _panel("ALMACENAMIENTO", "", rows)


def build_vpn():
    try:
        peers = proxmox.wg_peers()
    except proxmox.ProxmoxError as e:
        return f"No disponible: {e}"
    if not peers:
        return "Sin peers en el túnel."
    online = sum(1 for p in peers if p["online"])
    rows = []
    for p in peers:
        ip = p["allowed_ips"].split(",")[0].split("/")[0]
        estado = "online" if p["online"] else "offline"
        rows.append((ip, f"{estado.ljust(7)} {_ago(p['ago'])}"))
    return _panel(f"WIREGUARD  {config.WG['iface']}", f"{online}/{len(peers)} online", rows)


def build_servicios():
    rows = []
    try:
        if config.SERVICE_WATCH:
            svc = {s["name"]: s["state"] for s in proxmox.services()}
            for name in config.SERVICE_WATCH:
                rows.append((name, svc.get(name, "desconocido")))
        gs = proxmox.guests()
        watch = config.VMID_WATCH or [g["vmid"] for g in gs]
        for g in gs:
            if g["vmid"] in watch:
                rows.append((f"{g['kind']} {g['vmid']} {g['name']}", g["status"]))
    except proxmox.ProxmoxError as e:
        return f"No disponible: {e}"
    if not rows:
        return "Nada vigilado."
    return _panel("SERVICIOS Y MÁQUINAS", "", rows)


BUILDERS = {
    "status": build_status,
    "disk": build_disk,
    "vpn": build_vpn,
    "servicios": build_servicios,
}


def _help_text():
    return (
        f"*Monitor {config.SITE_NAME}*\n\n"
        "Usa los botones o los comandos:\n"
        "`/status` `/disk` `/vpn` `/servicios`\n\n"
        "_Aviso automático únicamente cuando un servicio o máquina cae._"
    )


# ─────────────────────────── handlers ───────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized_id(update.effective_chat.id):
        return
    await update.message.reply_text(
        _help_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=_kb()
    )


async def cmd_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str):
    if not _authorized_id(update.effective_chat.id):
        return
    await update.message.reply_text(
        BUILDERS[key](), parse_mode=ParseMode.MARKDOWN, reply_markup=_kb()
    )


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _authorized_id(q.message.chat.id):
        await q.answer("No autorizado")
        return
    await q.answer()
    text = BUILDERS.get(q.data, lambda: "?")()
    try:
        await q.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=_kb()
        )
    except BadRequest as e:
        # Mismo contenido = Telegram rechaza el edit; lo ignoramos.
        if "not modified" not in str(e).lower():
            raise


# ─────────────────────────── alertas (solo caídas) ───────────────────────────

async def _notify(ctx, text):
    for cid in AUTHORIZED:
        try:
            await ctx.bot.send_message(cid, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:  # noqa: BLE001
            log.warning("no se pudo enviar alerta a %s: %s", cid, e)


async def watch_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Envía aviso SOLO en la transición a caído. Nada más."""
    if config.SERVICE_WATCH:
        try:
            svc = {s["name"]: s["state"] for s in proxmox.services()}
            for name in config.SERVICE_WATCH:
                up = svc.get(name) == "running"
                if _prev["services"].get(name) and not up:
                    await _notify(ctx, f"*CAÍDA*\nServicio `{name}`: {svc.get(name, 'desconocido')}")
                _prev["services"][name] = up
        except proxmox.ProxmoxError as e:
            log.warning("watch servicios: %s", e)

    try:
        for g in proxmox.guests():
            if config.VMID_WATCH and g["vmid"] not in config.VMID_WATCH:
                continue
            up = g["status"] == "running"
            if _prev["guests"].get(g["vmid"]) and not up:
                await _notify(ctx, f"*CAÍDA*\n{g['kind']} {g['vmid']} `{g['name']}`: {g['status']}")
            _prev["guests"][g["vmid"]] = up
    except proxmox.ProxmoxError as e:
        log.warning("watch guests: %s", e)


# ─────────────────────────── main ───────────────────────────

def main():
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    for key in BUILDERS:
        app.add_handler(CommandHandler(key, lambda u, c, k=key: cmd_panel(u, c, k)))
    app.add_handler(CallbackQueryHandler(on_button))
    app.job_queue.run_repeating(watch_job, interval=config.POLL_SECONDS, first=15)
    log.info("bot arrancado; vigilando caídas cada %ss", config.POLL_SECONDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
