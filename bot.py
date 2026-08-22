#!/usr/bin/env python3
"""
Telegram Bot - Hybrid SMS Panel Monitor
Complete Admin Approval System - Desi Style 😆
"""

import os
import json
import re
import html
import time
import base64
import subprocess
import socket
from pathlib import Path
from typing import Optional, Dict, List, Any

import requests
from dotenv import load_dotenv

load_dotenv()

# =====================================================================
# CONFIG
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in environment")

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
if not SUPER_ADMIN_ID:
    raise ValueError("SUPER_ADMIN_ID not set in environment")

ADMIN_CHAT_IDS = set(map(int, os.getenv("ADMIN_CHAT_IDS", "").split(",")) if os.getenv("ADMIN_CHAT_IDS") else [])

# Files
PANELS_FILE = "data/panels.json"
KNOWN_CHATS_FILE = "data/known_chats.json"
STATUS_FILE = "data/monitor_status.json"
APPROVAL_FILE = "data/approved_users.json"
PENDING_FILE = "data/pending_users.json"

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# =====================================================================
# TELEGRAM HELPERS
# =====================================================================

def tg(method: str, **params) -> Optional[Dict]:
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=30)
        j = r.json()
        return j.get("result") if j.get("ok") else None
    except Exception as e:
        print(f"TG error: {e}")
        return None

def send(chat_id: int, text: str, keyboard: Optional[List] = None, reply_to: Optional[int] = None) -> Optional[Dict]:
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to:
        params["reply_to_message_id"] = reply_to
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("sendMessage", **params)

def edit(chat_id: int, message_id: int, text: str, keyboard: Optional[List] = None) -> Optional[Dict]:
    params = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("editMessageText", **params)

def answer_callback(query_id: str, text: Optional[str] = None):
    tg("answerCallbackQuery", callback_query_id=query_id, text=text or "")

def get_updates(offset: Optional[int] = None) -> List[Dict]:
    return tg("getUpdates", offset=offset, timeout=25) or []

# =====================================================================
# STORAGE
# =====================================================================

def load_json(path: str, default: Any = None) -> Any:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default or {}

def save_json(path: str, data: Any):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_panels() -> Dict:
    return load_json(PANELS_FILE, {})

def save_panels(panels: Dict):
    save_json(PANELS_FILE, panels)

def load_approved_users() -> Dict:
    return load_json(APPROVAL_FILE, {})

def save_approved_users(users: Dict):
    save_json(APPROVAL_FILE, users)

def load_pending_users() -> Dict:
    return load_json(PENDING_FILE, {})

def save_pending_users(users: Dict):
    save_json(PENDING_FILE, users)

def load_known_chats() -> set:
    return set(load_json(KNOWN_CHATS_FILE, []))

def save_known_chats(chats: set):
    save_json(KNOWN_CHATS_FILE, list(chats))

def get_monitor_status() -> Dict:
    return load_json(STATUS_FILE, {})

def is_user_approved(user_id: int) -> bool:
    return str(user_id) in load_approved_users()

def is_super_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID

def is_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID or user_id in ADMIN_CHAT_IDS

# =====================================================================
# PANEL DECODER
# =====================================================================

KEY = "ZXKAIv1_Xk9mP2wN7qL4vR6jH3cF8yT1ZbE5sA09"

def decode_zxkai_link(link: str):
    m = re.search(r"s=([^&]+)", link)
    if not m:
        return None
    s = m.group(1)
    b64 = s.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    try:
        raw = base64.b64decode(b64)
        dec = bytes(b ^ KEY[i % len(KEY)].encode()[0] for i, b in enumerate(raw))
        obj = json.loads(dec)
    except Exception:
        return None
    return obj.get("u", ""), obj.get("k", "")

def decode_profex_link(link: str):
    m = re.search(r"s=([^&]+)", link)
    if not m:
        return None
    s = m.group(1)
    b64 = s.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    try:
        raw = base64.b64decode(b64)
        txt = raw.decode("utf-8", errors="replace")
    except Exception:
        return None
    parts = txt.split("|||")
    url = parts[0].strip()
    key = parts[1].strip() if len(parts) > 1 else ""
    if not url.startswith("http"):
        return None
    return url, key

def parse_panel_link(link: str):
    link = link.strip().strip("<>")
    d = decode_zxkai_link(link)
    if d and d[0] and d[1]:
        return d
    d = decode_profex_link(link)
    if d and d[0]:
        return d
    m = re.search(r"(https://[^/?]+firebaseio\.com)", link)
    if not m:
        return None
    url = m.group(1)
    auth = re.search(r"auth=([A-Za-z0-9_\-]+)", link)
    return url, (auth.group(1) if auth else "")

def label_from_url(url: str) -> str:
    m = re.search(r"https://([a-z0-9\-]+)\.firebaseio\.com", url)
    return m.group(1) if m else url

# =====================================================================
# KEYBOARDS
# =====================================================================

def main_keyboard(user_id: int):
    kb = []
    kb.append([{"text": "📊 Status", "callback_data": "status"}])
    kb.append([{"text": "📋 My Panels", "callback_data": "mypanels"}])
    kb.append([{"text": "➕ Add Panel", "callback_data": "add"}])
    kb.append([{"text": "❌ Remove Panel", "callback_data": "remove"}])
    if is_admin(user_id):
        kb.append([{"text": "👥 User Management", "callback_data": "user_mgmt"}])
        kb.append([{"text": "📊 Admin Dashboard", "callback_data": "admin_dashboard"}])
    return kb

def admin_keyboard():
    return [
        [{"text": "📋 Pending Requests", "callback_data": "pending_requests"}],
        [{"text": "👥 Approved Users", "callback_data": "approved_users"}],
        [{"text": "➕ Add Admin", "callback_data": "add_admin"}],
        [{"text": "🔙 Back", "callback_data": "back"}],
    ]

# =====================================================================
# USER APPROVAL FUNCTIONS
# =====================================================================

def request_access(chat_id: int, username: str = "", first_name: str = ""):
    pending = load_pending_users()
    approved = load_approved_users()
    user_id = str(chat_id)
    if user_id in approved:
        send(chat_id, "✅ Babu! Tum already access rakhte ho! /start karo.")
        return
    if user_id in pending:
        send(chat_id, "⏳ Tumhara request already pending hai. Admin approve karega babu!")
        return
    pending[user_id] = {
        "chat_id": chat_id,
        "username": username or "Unknown",
        "first_name": first_name or "Unknown",
        "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending"
    }
    save_pending_users(pending)

    send(chat_id,
        "📨 <b>Access Request Sent!</b>\n\n"
        "Tumhara request admin ke paas bhej diya hai.\n"
        "Approve hote hi tumhe notification milega.\n\n"
        f"👤 Name: {first_name or username or 'Unknown'}\n"
        f"🆔 ID: <code>{chat_id}</code>\n"
        f"⏰ Requested: {pending[user_id]['requested_at']}\n\n"
        "Thoda sabar rakho babu! 😊"
    )

    text = (
        "🆕 <b>Naya Access Request Aaya Hai!</b>\n\n"
        f"👤 Name: {first_name or username or 'Unknown'}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📱 Chat ID: <code>{chat_id}</code>\n"
        f"⏰ Requested: {pending[user_id].get('requested_at', 'Just now')}\n\n"
        "📋 Pending Requests me check karo."
    )
    keyboard = [[{"text": "📋 View Pending", "callback_data": "pending_requests"}]]
    send(SUPER_ADMIN_ID, text, keyboard=keyboard)
    for admin_id in ADMIN_CHAT_IDS:
        send(admin_id, text, keyboard=keyboard)

def approve_user(user_id: str, approver_id: int):
    pending = load_pending_users()
    approved = load_approved_users()
    if user_id not in pending:
        return False, "User not found"
    user_info = pending[user_id]
    chat_id = user_info.get("chat_id")
    name = user_info.get("first_name") or user_info.get("username") or "User"
    approved[user_id] = {
        "chat_id": chat_id,
        "name": name,
        "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "approved_by": str(approver_id)
    }
    save_approved_users(approved)
    del pending[user_id]
    save_pending_users(pending)

    if chat_id:
        send(chat_id,
            "🎉 <b>Ujala Follows My Brother!</b>\n\n"
            "Tumhara access approve ho gaya hai!\n"
            "Ab tum bot use kar sakte ho.\n\n"
            "Send /start to begin.\n\n"
            "Welcome babu! 🎊"
        )

    for admin_id in ADMIN_CHAT_IDS:
        send(admin_id, f"✅ <code>{name}</code> ko approve kar diya! 🎉")
    send(SUPER_ADMIN_ID, f"✅ <code>{name}</code> ko approve kar diya! 🎉")
    return True, "Approved"

def reject_user(user_id: str, approver_id: int):
    pending = load_pending_users()
    if user_id not in pending:
        return False, "User not found"
    user_info = pending[user_id]
    chat_id = user_info.get("chat_id")
    name = user_info.get("first_name") or user_info.get("username") or "User"
    del pending[user_id]
    save_pending_users(pending)

    if chat_id:
        send(chat_id,
            "❌ <b>Access Denied</b>\n\n"
            "Tumhara request reject kar diya gaya hai.\n"
            "Kisi aur admin se contact karo.\n\n"
            "Sorry babu! 😔"
        )

    for admin_id in ADMIN_CHAT_IDS:
        send(admin_id, f"❌ <code>{name}</code> ko reject kar diya! 😔")
    send(SUPER_ADMIN_ID, f"❌ <code>{name}</code> ko reject kar diya! 😔")
    return True, "Rejected"

def add_admin(user_id: str, approver_id: int):
    if not is_super_admin(approver_id):
        return False, "Sirf super admin hi admin bana sakta hai babu!"
    approved = load_approved_users()
    if user_id not in approved:
        return False, "User approved nahi hai. Pehle approve karo."
    global ADMIN_CHAT_IDS
    ADMIN_CHAT_IDS.add(int(user_id))
    chat_id = approved[user_id].get("chat_id")
    if chat_id:
        send(chat_id,
            "⭐ <b>Babu! Tum Admin Ban Gaye Ho!</b>\n\n"
            "Ab tum admin features use kar sakte ho:\n"
            "• User Management\n"
            "• Approve/Reject Requests\n"
            "• Admin Dashboard\n\n"
            "/start karo aur naye options dekho! 🎉"
        )
    return True, f"User {user_id} admin ban gaya! 🎉"

def remove_admin(user_id: str, approver_id: int):
    if not is_super_admin(approver_id):
        return False, "Sirf super admin hi admin hata sakta hai!"
    if int(user_id) == SUPER_ADMIN_ID:
        return False, "Super admin nahi hata sakte babu!"
    global ADMIN_CHAT_IDS
    if int(user_id) in ADMIN_CHAT_IDS:
        ADMIN_CHAT_IDS.remove(int(user_id))
        return True, f"Admin {user_id} hata diya!"
    return False, "Ye admin nahi hai"

# =====================================================================
# DASHBOARD GENERATION
# =====================================================================

def generate_dashboard(panels: Dict, status: Dict) -> str:
    total_devices = status.get("devices_total", 0)
    online_devices = status.get("devices_online", 0)
    offline_devices = status.get("devices_offline", 0)
    messages_detected = status.get("messages_detected", 0)
    messages_sent = status.get("messages_sent", 0)
    duplicates = status.get("duplicates_ignored", 0)
    old_ignored = status.get("old_ignored", 0)
    requests_total = status.get("requests_total", 0)
    requests_failed = status.get("requests_failed", 0)
    active_panels = status.get("active_panels", len(panels))
    timestamp = status.get("timestamp", 0)
    last_update = "Just now" if timestamp == 0 else f"{int((time.time() - timestamp/1000) / 60)} min ago"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        "      📊 LIVE DASHBOARD",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🟢 Status: {'ACTIVE' if active_panels > 0 else 'IDLE'}",
        f"📡 Firebase: {'Connected' if panels else 'No Panels'}",
        f"📱 Total Panels: {len(panels)}",
        f"📦 Total Devices: {total_devices}",
        f"🟢 Online: {online_devices}",
        f"🔴 Offline: {offline_devices}",
        f"🕐 Updated: {last_update}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📈 MESSAGE STATS",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📨 Detected: {messages_detected}",
        f"📩 Sent: {messages_sent}",
        f"🔄 Duplicates: {duplicates}",
        f"⏰ Old Ignored: {old_ignored}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚡ PERFORMANCE",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📤 Requests: {requests_total}",
        f"❌ Failed: {requests_failed}",
        f"📶 Success Rate: {int((requests_total - requests_failed) / max(requests_total, 1) * 100)}%",
        "",
    ]

    panel_status = status.get("panel_status", {})
    if panel_status:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📋 TOP PANELS")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for name, info in list(panel_status.items())[:5]:
            online = info.get("online", 0)
            total = info.get("total", 0)
            ratio = f"{online}/{total}"
            icon = "✅" if online == total else "🟡" if online > 0 else "❌"
            lines.append(f"{icon} {name[:20]}  🟢 {ratio}")
        if len(panel_status) > 5:
            lines.append(f"... and {len(panel_status) - 5} more")

    lines.append("")
    lines.append("[🔄 Refresh]")
    return "\n".join(lines)

def generate_admin_dashboard() -> str:
    approved = load_approved_users()
    pending = load_pending_users()
    panels = load_panels()
    status = get_monitor_status()
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        "   👑 ADMIN DASHBOARD",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "👥 USERS",
        f"✅ Approved: {len(approved)}",
        f"⏳ Pending: {len(pending)}",
        f"👤 Admins: {len(ADMIN_CHAT_IDS) + 1}",
        "",
        "📡 SYSTEM",
        f"📱 Panels: {len(panels)}",
        f"📦 Devices: {status.get('devices_total', 0)}",
        f"🟢 Online: {status.get('devices_online', 0)}",
        f"📨 Messages Sent: {status.get('messages_sent', 0)}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "👑 Super Admin: Ghare Jake Sutt Babu! 😆",
    ]
    return "\n".join(lines)

# =====================================================================
# COMMAND HANDLERS
# =====================================================================

def cmd_start(chat_id: int, message_id: int):
    user_id = chat_id
    if not is_user_approved(user_id) and not is_admin(user_id):
        pending = load_pending_users()
        if str(user_id) in pending:
            send(chat_id,
                "⏳ <b>Access Request Pending</b>\n\n"
                "Tumhara request abhi admin ke approval me hai.\n"
                "Jab approve hoga toh notification aa jayega.\n\n"
                "Thoda intezaar karo... 🕐",
                reply_to=message_id
            )
            return
        user_info = tg("getChat", chat_id=chat_id)
        first_name = user_info.get("first_name", "User") if user_info else "User"
        username = user_info.get("username", "") if user_info else ""
        keyboard = [[{"text": "📨 Request Access", "callback_data": "request_access"}]]
        send(chat_id,
            "🔒 <b>Ghare Jake Sutt Babu!</b>\n\n"
            "Ye bot sirf authorized users ke liye hai. \n"
            "Pehle access lena hoga.\n\n"
            f"👤 Your ID: <code>{chat_id}</code>\n"
            f"📛 Name: {first_name}\n"
            f"👤 Username: @{username if username else 'None'}\n\n"
            "Neeche button dabao aur access request karo.",
            keyboard=keyboard,
            reply_to=message_id
        )
        return

    known_chats = load_known_chats()
    known_chats.add(chat_id)
    save_known_chats(known_chats)

    text = (
        "🤖 <b>Hybrid SMS Panel Monitor</b>\n\n"
        "✅ 200+ Panels Supported\n"
        "✅ ZXKAI / Profex / Firebase\n"
        "✅ Real-time Promo Detection\n\n"
        "• 📊 Status — Live Dashboard\n"
        "• 📋 My Panels — Panel List\n"
        "• ➕ Add Panel — Add Multiple\n"
        "• ❌ Remove Panel — Delete\n\n"
        f"👤 Chat ID: <code>{chat_id}</code>"
    )
    send(chat_id, text, keyboard=main_keyboard(chat_id), reply_to=message_id)

def handle_status(chat_id: int, message_id: int):
    if not is_user_approved(chat_id) and not is_admin(chat_id):
        send(chat_id, "🔒 Access denied babu! Request karo.")
        return
    panels = load_panels()
    status = get_monitor_status()
    text = generate_dashboard(panels, status)
    edit(chat_id, message_id, text, [[{"text": "🔄 Refresh", "callback_data": "refresh"}], [{"text": "🔙 Back", "callback_data": "back"}]])

def handle_mypanels(chat_id: int, message_id: int):
    if not is_user_approved(chat_id) and not is_admin(chat_id):
        send(chat_id, "🔒 Access denied babu! Request karo.")
        return
    panels = load_panels()
    if not panels:
        edit(chat_id, message_id, "📭 No panels added babu!", main_keyboard(chat_id))
        return
    status = get_monitor_status()
    panel_status = status.get("panel_status", {})
    lines = ["━━━━━━━━━━━━━━━━━━━━", "      📋 MY PANELS", "━━━━━━━━━━━━━━━━━━━━", ""]
    for i, name in enumerate(panels, 1):
        info = panel_status.get(name, {})
        online = info.get("online", "?")
        total = info.get("total", "?")
        icon = "🟢" if online == total else "🔴" if online == 0 and total != 0 else "🟡"
        lines.append(f"{i}. {icon} {html.escape(name)}  ({online}/{total})")
    lines.append("")
    lines.append(f"📦 Total: {len(panels)} panels")
    edit(chat_id, message_id, "\n".join(lines), main_keyboard(chat_id))

def handle_add(chat_id: int, message_id: int):
    if not is_user_approved(chat_id) and not is_admin(chat_id):
        send(chat_id, "🔒 Access denied babu! Request karo.")
        return
    state = load_json("data/chat_state.json", {})
    state[str(chat_id)] = "add"
    save_json("data/chat_state.json", state)
    edit(chat_id, message_id,
        "➕ <b>Add New Panels</b>\n\n"
        "Panel links bhejo (ek line me ek).\n"
        "Supported: ZXKAI (?s=...), Profex, Firebase\n\n"
        "Example:\n"
        "https://panel.firebaseio.com\n"
        "https://zxkai.com?s=...",
        [[{"text": "🔙 Back", "callback_data": "back"}]]
    )

def handle_remove(chat_id: int, message_id: int):
    if not is_user_approved(chat_id) and not is_admin(chat_id):
        send(chat_id, "🔒 Access denied babu! Request karo.")
        return
    panels = load_panels()
    if not panels:
        edit(chat_id, message_id, "📭 No panels added babu!", main_keyboard(chat_id))
        return
    kb = []
    for i, name in enumerate(panels, 1):
        kb.append([{"text": f"❌ {i}. {name[:28]}", "callback_data": f"rm:{i}"}])
    kb.append([{"text": "🔙 Back", "callback_data": "back"}])
    edit(chat_id, message_id, "❌ <b>Remove Panel</b>\n\nKon sa panel delete karna hai babu?", kb)

def handle_remove_confirm(chat_id: int, message_id: int, name: str):
    panels = load_panels()
    if name in panels:
        del panels[name]
        save_panels(panels)
        with open("data/panel_update.txt", "w") as f:
            f.write(f"REMOVE|{name}|{int(time.time())}")
    edit(chat_id, message_id,
        f"❌ <b>{html.escape(name)}</b> delete ho gaya babu!\n\nRemaining: {len(panels)} panels",
        main_keyboard(chat_id)
    )

def handle_user_management(chat_id: int, message_id: int):
    if not is_admin(chat_id):
        send(chat_id, "⛔ Admin access required babu!")
        return
    pending = load_pending_users()
    approved = load_approved_users()
    text = (
        "👥 <b>User Management</b>\n\n"
        f"⏳ Pending Requests: {len(pending)}\n"
        f"✅ Approved Users: {len(approved)}\n"
        f"👤 Admins: {len(ADMIN_CHAT_IDS) + 1}\n\n"
        "Neeche buttons se manage karo."
    )
    edit(chat_id, message_id, text, admin_keyboard())

def handle_pending_requests(chat_id: int, message_id: int):
    if not is_admin(chat_id):
        send(chat_id, "⛔ Admin access required babu!")
        return
    pending = load_pending_users()
    if not pending:
        edit(chat_id, message_id, "📭 No pending requests.\n\nSab clear hai babu!", admin_keyboard())
        return
    lines = ["━━━━━━━━━━━━━━━━━━━━", "   ⏳ PENDING REQUESTS", "━━━━━━━━━━━━━━━━━━━━", ""]
    for user_id, info in pending.items():
        name = info.get("first_name") or info.get("username") or "Unknown"
        requested_at = info.get("requested_at", "Unknown")
        lines.append(f"👤 {name}")
        lines.append(f"🆔 <code>{user_id}</code>")
        lines.append(f"⏰ {requested_at}")
        lines.append("")
    kb = []
    for user_id in list(pending.keys())[:10]:
        kb.append([
            {"text": f"✅ {user_id[:8]}", "callback_data": f"approve:{user_id}"},
            {"text": f"❌ {user_id[:8]}", "callback_data": f"reject:{user_id}"},
        ])
    kb.append([{"text": "🔙 Back", "callback_data": "user_mgmt"}])
    text = "\n".join(lines)
    edit(chat_id, message_id, text, kb)

def handle_approved_users(chat_id: int, message_id: int):
    if not is_admin(chat_id):
        send(chat_id, "⛔ Admin access required babu!")
        return
    approved = load_approved_users()
    if not approved:
        edit(chat_id, message_id, "📭 No approved users yet babu!", admin_keyboard())
        return
    lines = ["━━━━━━━━━━━━━━━━━━━━", "   ✅ APPROVED USERS", "━━━━━━━━━━━━━━━━━━━━", ""]
    for user_id, info in list(approved.items())[:20]:
        name = info.get("name", "Unknown")
        approved_at = info.get("approved_at", "Unknown")
        is_admin_user = "⭐ Admin" if int(user_id) in ADMIN_CHAT_IDS else "👤 User"
        lines.append(f"👤 {name} {is_admin_user}")
        lines.append(f"🆔 <code>{user_id}</code>")
        lines.append(f"⏰ {approved_at}")
        lines.append("")
    if len(approved) > 20:
        lines.append(f"... and {len(approved) - 20} more")
    kb = []
    if is_super_admin(chat_id):
        kb.append([{"text": "➕ Add Admin", "callback_data": "add_admin"}])
        for user_id in list(approved.keys())[:5]:
            if int(user_id) != SUPER_ADMIN_ID and int(user_id) in ADMIN_CHAT_IDS:
                kb.append([{"text": f"❌ Remove Admin {user_id[:8]}", "callback_data": f"remove_admin:{user_id}"}])
    kb.append([{"text": "🔙 Back", "callback_data": "user_mgmt"}])
    text = "\n".join(lines)
    edit(chat_id, message_id, text, kb)

def handle_add_admin(chat_id: int, message_id: int):
    if not is_super_admin(chat_id):
        send(chat_id, "⛔ Sirf super admin hi admin bana sakta hai babu!")
        return
    state = load_json("data/chat_state.json", {})
    state[str(chat_id)] = "add_admin"
    save_json("data/chat_state.json", state)
    edit(chat_id, message_id,
        "➕ <b>Add Admin</b>\n\n"
        "Jisko admin banana hai uska User ID bhejo.\n\n"
        "User already approved hona chahiye.\n\n"
        "Example: <code>123456789</code>",
        [[{"text": "🔙 Back", "callback_data": "user_mgmt"}]]
    )

def handle_admin_dashboard(chat_id: int, message_id: int):
    if not is_admin(chat_id):
        send(chat_id, "⛔ Admin access required babu!")
        return
    text = generate_admin_dashboard()
    edit(chat_id, message_id, text,
        [[{"text": "🔄 Refresh", "callback_data": "admin_dashboard"}], [{"text": "🔙 Back", "callback_data": "back"}]]
    )

# =====================================================================
# TEXT & CALLBACK HANDLERS
# =====================================================================

def handle_text_message(chat_id: int, text: str, message_id: int):
    if not is_user_approved(chat_id) and not is_admin(chat_id):
        pending = load_pending_users()
        if str(chat_id) in pending:
            send(chat_id, "⏳ Tumhara request pending hai babu. Admin approve karega!")
            return
        send(chat_id, "🔒 Access required babu! /start karo aur request karo.")
        return

    state = load_json("data/chat_state.json", {})

    if state.get(str(chat_id)) == "add_admin":
        if not is_super_admin(chat_id):
            send(chat_id, "⛔ Sirf super admin hi admin bana sakta hai babu!")
            return
        user_id = text.strip()
        if not user_id.isdigit():
            send(chat_id, "❌ Invalid User ID. Numeric ID bhejo babu!")
            return
        approved = load_approved_users()
        if user_id not in approved:
            send(chat_id, f"❌ User {user_id} approved nahi hai. Pehle approve karo.")
            return
        success, msg = add_admin(user_id, chat_id)
        state.pop(str(chat_id), None)
        save_json("data/chat_state.json", state)
        send(chat_id, f"{'✅' if success else '❌'} {msg}", keyboard=main_keyboard(chat_id))
        return

    if state.get(str(chat_id)) == "add":
        links = [l for l in re.split(r"[\s,;]+", text) if l.strip()]
        panels = load_panels()
        added, failed = [], []
        for link in links:
            res = parse_panel_link(link)
            if not res or not res[0]:
                failed.append(link[:50])
                continue
            url, key = res
            name = label_from_url(url)
            if any(p["url"] == url for p in panels.values()):
                failed.append(f"{name} (already exists)")
                continue
            panels[name] = {"url": url, "key": key, "added": time.strftime("%Y-%m-%d %H:%M")}
            added.append(name)
        if added:
            save_panels(panels)
            with open("data/panel_update.txt", "w") as f:
                f.write(f"ADD|{int(time.time())}")
            text_msg = f"✅ <b>{len(added)} Panels added!</b>\n━━━━━━━━━━━━━━━━━━━\n"
            for name in added:
                text_msg += f"✅ {html.escape(name)}\n"
            text_msg += "\n📡 Monitor started for new panels."
        else:
            text_msg = "⚠️ No valid links found babu!"
        state.pop(str(chat_id), None)
        save_json("data/chat_state.json", state)
        send(chat_id, text_msg, keyboard=main_keyboard(chat_id), reply_to=message_id)
        if failed:
            send(chat_id, "⚠️ Failed to add:\n" + "\n".join(f"• {f}" for f in failed), reply_to=message_id)
        return

    if text.startswith("/"):
        send(chat_id, "🤖 Commands:\n/start - Menu\n\nButtons use karo babu!", keyboard=main_keyboard(chat_id), reply_to=message_id)

def handle_callback(chat_id: int, message_id: int, query_id: str, data: str):
    answer_callback(query_id)

    if data == "request_access":
        user_info = tg("getChat", chat_id=chat_id)
        first_name = user_info.get("first_name", "User") if user_info else "User"
        username = user_info.get("username", "") if user_info else ""
        request_access(chat_id, username, first_name)
        edit(chat_id, message_id, "📨 Request sent babu!\n\nAdmin approve karega!", main_keyboard(chat_id))
        return

    if data.startswith("approve:"):
        if not is_admin(chat_id):
            send(chat_id, "⛔ Admin access required babu!")
            return
        user_id = data.split(":")[1]
        success, msg = approve_user(user_id, chat_id)
        send(chat_id, f"{'✅' if success else '❌'} {msg}")
        handle_pending_requests(chat_id, message_id)
        return

    if data.startswith("reject:"):
        if not is_admin(chat_id):
            send(chat_id, "⛔ Admin access required babu!")
            return
        user_id = data.split(":")[1]
        success, msg = reject_user(user_id, chat_id)
        send(chat_id, f"{'✅' if success else '❌'} {msg}")
        handle_pending_requests(chat_id, message_id)
        return

    if data.startswith("remove_admin:"):
        if not is_super_admin(chat_id):
            send(chat_id, "⛔ Sirf super admin hi admin hata sakta hai!")
            return
        user_id = data.split(":")[1]
        success, msg = remove_admin(user_id, chat_id)
        send(chat_id, f"{'✅' if success else '❌'} {msg}")
        handle_approved_users(chat_id, message_id)
        return

    if data == "status" or data == "refresh":
        handle_status(chat_id, message_id)
        return

    if data == "mypanels":
        handle_mypanels(chat_id, message_id)
        return

    if data == "add":
        handle_add(chat_id, message_id)
        return

    if data == "remove":
        handle_remove(chat_id, message_id)
        return

    if data == "user_mgmt":
        handle_user_management(chat_id, message_id)
        return

    if data == "admin_dashboard":
        handle_admin_dashboard(chat_id, message_id)
        return

    if data == "pending_requests":
        handle_pending_requests(chat_id, message_id)
        return

    if data == "approved_users":
        handle_approved_users(chat_id, message_id)
        return

    if data == "add_admin":
        handle_add_admin(chat_id, message_id)
        return

    if data == "back":
        state = load_json("data/chat_state.json", {})
        state.pop(str(chat_id), None)
        save_json("data/chat_state.json", state)
        edit(chat_id, message_id, "👍 Menu", main_keyboard(chat_id))
        return

    if data.startswith("rm:"):
        panels = load_panels()
        try:
            idx = int(data[3:]) - 1
            names = list(panels.keys())
            if 0 <= idx < len(names):
                handle_remove_confirm(chat_id, message_id, names[idx])
            else:
                edit(chat_id, message_id, "⚠️ Panel nahi mila babu!", main_keyboard(chat_id))
        except (ValueError, IndexError):
            edit(chat_id, message_id, "⚠️ Invalid selection babu!", main_keyboard(chat_id))
        return

# =====================================================================
# NODE.JS MONITOR STARTER
# =====================================================================

def start_node_monitor():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 3000))
        sock.close()
        if result == 0:
            print("✅ Node.js monitor already running")
            return
        node_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "node", "monitor.js")
        if os.path.exists(node_path):
            subprocess.Popen(
                ["node", node_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                start_new_session=True,
            )
            print("🚀 Node.js monitor started")
        else:
            print(f"⚠️ Node.js monitor not found at: {node_path}")
    except Exception as e:
        print(f"⚠️ Failed to start Node.js monitor: {e}")

# =====================================================================
# MAIN LOOP
# =====================================================================

def main():
    print("=" * 50)
    print("🔐 HYBRID SMS PANEL MONITOR - DESI STYLE 😆")
    print("=" * 50)

    start_node_monitor()

    me = tg("getMe")
    if me:
        print(f"🤖 Bot: @{me.get('username', 'unknown')}")
    print(f"👑 Super Admin: {SUPER_ADMIN_ID}")
    print(f"👤 Admins: {', '.join(str(a) for a in ADMIN_CHAT_IDS)}")
    print("=" * 50)

    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    cb = update["callback_query"]
                    msg = cb.get("message")
                    if msg and "chat" in msg:
                        handle_callback(msg["chat"]["id"], msg["message_id"], cb["id"], cb.get("data", ""))
                    continue
                if "message" in update:
                    m = update["message"]
                    chat_id = m["chat"]["id"]
                    text = m.get("text") or ""
                    mid = m["message_id"]
                    if text == "/start":
                        cmd_start(chat_id, mid)
                    else:
                        handle_text_message(chat_id, text, mid)
        except Exception as e:
            print(f"⚠️ Main loop error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
