import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
import requests
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = (os.environ.get("WHATSAPP_TOKEN") or "").strip() or None
WHATSAPP_PHONE_ID = (os.environ.get("WHATSAPP_PHONE_ID") or "").strip() or None
API_VERSION = (os.environ.get("WHATSAPP_API_VERSION") or "v23.0").strip()

OUTBOX_LOG = Path(__file__).parent.parent / "data" / "processed" / "outbox.log"
_LAST_INBOUND_PATH = Path(__file__).parent.parent / "data" / "processed" / "last_inbound.json"

DRY_RUN = not (WHATSAPP_TOKEN and WHATSAPP_PHONE_ID)

SESSION_WINDOW = timedelta(hours=23, minutes=50)
_window_lock = Lock()

def _load_last_inbound() -> dict:
    try:
        with open(_LAST_INBOUND_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def record_inbound(phone: str):
    wire = to_wire_number(phone)
    if not wire:
        return
    with _window_lock:
        data = _load_last_inbound()
        data[wire] = datetime.now().isoformat(timespec="seconds")
        _LAST_INBOUND_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LAST_INBOUND_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(_LAST_INBOUND_PATH)

def window_open(phone: str) -> bool:
    with _window_lock:
        at = _load_last_inbound().get(to_wire_number(phone))
    if not at:
        return False
    try:
        return datetime.now() - datetime.fromisoformat(at) < SESSION_WINDOW
    except ValueError:
        return False

def _base_url(phone_number_id: str = None):
    pid = phone_number_id or WHATSAPP_PHONE_ID
    return f"https://graph.facebook.com/{API_VERSION}/{pid}/messages"

def _log_outbox(payload: dict):
    OUTBOX_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTBOX_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **payload}) + "\n")

def _send(payload: dict, phone_number_id: str = None):
    _log_outbox(payload)

    if DRY_RUN:
        print(f"[whatsapp_client DRY RUN -- no WHATSAPP_TOKEN/WHATSAPP_PHONE_ID set]\n{json.dumps(payload, indent=2)}")
        return {"dry_run": True, "payload": payload}

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = requests.post(_base_url(phone_number_id), headers=headers, json=payload, timeout=15)
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp send failed ({resp.status_code}): {resp.text}")
    return resp.json()

def to_wire_number(phone: str) -> str:
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return digits

_to_wire_number = to_wire_number

def send_text(to_phone: str, body: str, phone_number_id: str = None) -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": _to_wire_number(to_phone),
        "type": "text",
        "text": {"body": body},
    }
    return _send(payload, phone_number_id)

def send_yes_no_buttons(to_phone: str, body: str, accept_id: str, decline_id: str,
                         phone_number_id: str = None,
                         accept_label: str = "Accept", decline_label: str = "Decline") -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": _to_wire_number(to_phone),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": accept_id, "title": (accept_label or "Accept")[:20]}},
                    {"type": "reply", "reply": {"id": decline_id, "title": (decline_label or "Decline")[:20]}},
                ]
            },
        },
    }
    return _send(payload, phone_number_id)

def send_buttons(to_phone: str, body: str, buttons: list, phone_number_id: str = None) -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": _to_wire_number(to_phone),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid[:256], "title": (title or "OK")[:20]}}
                    for bid, title in buttons[:3]
                ]
            },
        },
    }
    return _send(payload, phone_number_id)

def mark_read(message_id: str, phone_number_id: str = None) -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    return _send(payload, phone_number_id)

def _clean_param(value) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text or "-"

def send_template(to_phone: str, template_name: str, body_params: list = None,
                  language: str = None, button_payloads: list = None,
                  phone_number_id: str = None) -> dict:
    language = (language or os.environ.get("WA_TEMPLATE_LANG") or "en").strip()
    components = []
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": _clean_param(v)} for v in body_params],
        })
    for i, payload_value in enumerate(button_payloads or []):
        components.append({
            "type": "button",
            "sub_type": "quick_reply",
            "index": str(i),
            "parameters": [{"type": "payload", "payload": str(payload_value)}],
        })
    template = {"name": template_name, "language": {"code": language}}
    if components:
        template["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "to": _to_wire_number(to_phone),
        "type": "template",
        "template": template,
    }
    return _send(payload, phone_number_id)

def download_media(media_id: str) -> bytes:
    if DRY_RUN:
        raise RuntimeError("Cannot download files in dry-run mode (no WHATSAPP_TOKEN).")
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    meta = requests.get(f"https://graph.facebook.com/{API_VERSION}/{media_id}", headers=headers, timeout=15)
    if meta.status_code >= 400:
        raise RuntimeError(f"Media lookup failed ({meta.status_code}): {meta.text}")
    url = meta.json().get("url")
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"Media download failed ({resp.status_code})")
    return resp.content
