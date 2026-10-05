import json
from pathlib import Path
from threading import Lock

_DATA_DIR = Path(__file__).parent.parent / "data" / "processed"
_SESSIONS_PATH = _DATA_DIR / "customer_sessions.json"
_OFFERS_PATH = _DATA_DIR / "pending_offers.json"

_lock = Lock()

def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def _save(path: Path, data: dict):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)

def get_customer_session(phone: str) -> dict | None:
    with _lock:
        return _load(_SESSIONS_PATH).get(phone)

def set_customer_session(phone: str, request: dict):
    with _lock:
        data = _load(_SESSIONS_PATH)
        data[phone] = request
        _save(_SESSIONS_PATH, data)

def clear_customer_session(phone: str):
    with _lock:
        data = _load(_SESSIONS_PATH)
        data.pop(phone, None)
        _save(_SESSIONS_PATH, data)

def add_pending_offer(transporter_phone: str, shipment_id: str, transporter_name: str,
                       accept_id: str, decline_id: str):
    with _lock:
        data = _load(_OFFERS_PATH)
        data.setdefault(transporter_phone, [])
        data[transporter_phone].append({
            "shipment_id": shipment_id,
            "transporter_name": transporter_name,
            "accept_id": accept_id,
            "decline_id": decline_id,
            "resolved": False,
        })
        _save(_OFFERS_PATH, data)

def get_open_offer(transporter_phone: str) -> dict | None:
    with _lock:
        data = _load(_OFFERS_PATH)
        offers = data.get(transporter_phone, [])
        for offer in reversed(offers):
            if not offer.get("resolved"):
                return offer
        return None

def find_offer_by_button_id(transporter_phone: str, button_id: str) -> dict | None:
    with _lock:
        data = _load(_OFFERS_PATH)
        for offer in data.get(transporter_phone, []):
            if button_id in (offer.get("accept_id"), offer.get("decline_id")):
                return offer
        return None

def resolve_offer(transporter_phone: str, shipment_id: str):
    with _lock:
        data = _load(_OFFERS_PATH)
        for offer in data.get(transporter_phone, []):
            if offer["shipment_id"] == shipment_id:
                offer["resolved"] = True
        _save(_OFFERS_PATH, data)

def open_offers_for_shipment(shipment_id: str) -> list:
    with _lock:
        data = _load(_OFFERS_PATH)
        result = []
        for phone, offers in data.items():
            for offer in offers:
                if offer["shipment_id"] == shipment_id and not offer.get("resolved"):
                    result.append((phone, offer))
        return result
