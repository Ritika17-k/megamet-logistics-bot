import json
from datetime import datetime
from pathlib import Path
from threading import Lock

_DATA_DIR = Path(__file__).parent.parent / "data" / "processed"
_WAVES_PATH = _DATA_DIR / "dispatch_waves.json"

_lock = Lock()

def _load() -> dict:
    if not _WAVES_PATH.exists():
        return {}
    with open(_WAVES_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def _save(data: dict):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _WAVES_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(_WAVES_PATH)

def create_wave_state(shipment_id: str, wave_index: int, contacted_names: list,
                       ranked_names: dict, request: dict, next_escalation_at: datetime,
                       requester_phone: str = None, requester_phone_id: str = None):
    with _lock:
        data = _load()
        data[shipment_id] = {
            "wave_index": wave_index,
            "contacted": list(contacted_names),
            "ranked_names": {pool: list(names) for pool, names in ranked_names.items()},
            "request": dict(request),
            "next_escalation_at": next_escalation_at.isoformat(timespec="seconds"),
            "resolved": False,
            "requester_phone": requester_phone,
            "requester_phone_id": requester_phone_id,
        }
        _save(data)

def get_requester(shipment_id: str):
    state = get_wave_state(shipment_id)
    if not state:
        return None, None
    return state.get("requester_phone"), state.get("requester_phone_id")

def pool_for_transporter(shipment_id: str, transporter_name: str) -> str:
    state = get_wave_state(shipment_id)
    if not state:
        return "unknown"
    ranked = state.get("ranked_names", {})
    if transporter_name in ranked.get("regional", []):
        return "regional"
    if transporter_name in ranked.get("pan_india", []):
        return "pan_india"
    return "unknown"

def get_wave_state(shipment_id: str):
    with _lock:
        return _load().get(shipment_id)

def advance_wave(shipment_id: str, wave_index: int, newly_contacted_names: list, next_escalation_at: datetime):
    with _lock:
        data = _load()
        state = data.get(shipment_id)
        if not state:
            return
        state["wave_index"] = wave_index
        state["contacted"] = list(state.get("contacted", [])) + list(newly_contacted_names)
        state["next_escalation_at"] = next_escalation_at.isoformat(timespec="seconds")
        _save(data)

def mark_resolved(shipment_id: str):
    with _lock:
        data = _load()
        state = data.get(shipment_id)
        if state:
            state["resolved"] = True
            _save(data)

def due_for_escalation(now: datetime = None):
    now = now or datetime.now()
    with _lock:
        data = _load()
    due = []
    for shipment_id, state in data.items():
        if state.get("resolved"):
            continue
        try:
            next_at = datetime.fromisoformat(state["next_escalation_at"])
        except (KeyError, ValueError):
            continue
        if now >= next_at:
            due.append((shipment_id, state))
    return due

def set_due_now(shipment_id: str):
    with _lock:
        data = _load()
        state = data.get(shipment_id)
        if state and not state.get("resolved"):
            state["next_escalation_at"] = datetime.now().isoformat(timespec="seconds")
            _save(data)
