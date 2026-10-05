import csv
from datetime import datetime
from pathlib import Path
from threading import Lock

LOG_PATH = Path(__file__).parent.parent / "data" / "processed" / "dispatch_log.csv"

_lock = Lock()

COLUMNS = [
    "shipment_id",
    "transporter_name",
    "pickup",
    "destination",
    "weight_tons",
    "region_class",
    "rank_offered",
    "was_exploration",
    "offered_at",
    "responded_at",
    "response",
    "assigned",
    "cancelled_later",
]

def _ensure_log():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writeheader()

def log_dispatch(shipment_id, request, matches, exploration_names=None):
    with _lock:
        _ensure_log()
        exploration_names = set(exploration_names or [])
        now = datetime.now().isoformat(timespec="seconds")

        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            for pool in ("regional", "pan_india"):
                for rank, m in enumerate(matches.get(pool, []), start=1):
                    writer.writerow({
                        "shipment_id": shipment_id,
                        "transporter_name": m.name,
                        "pickup": request.get("pickup"),
                        "destination": request.get("destination"),
                        "weight_tons": request.get("weight_tons"),
                        "region_class": "Regional" if pool == "regional" else "Pan-India",
                        "rank_offered": rank,
                        "was_exploration": m.name in exploration_names,
                        "offered_at": now,
                        "responded_at": "",
                        "response": "NO_RESPONSE",
                        "assigned": False,
                        "cancelled_later": False,
                    })

def compute_transporter_stats(log_path: Path = None):
    log_path = Path(log_path) if log_path else LOG_PATH
    with _lock:
        if not log_path.exists():
            return {}

        import pandas as pd
        df = pd.read_csv(log_path)
    if df.empty:
        return {}

    stats = {}
    for name, grp in df.groupby("transporter_name"):
        offered = len(grp)
        answered = grp[grp["response"].isin(["ACCEPTED", "DECLINED"])]
        accepted = grp[grp["response"] == "ACCEPTED"]

        stats[name] = {
            "offered": offered,
            "acceptance_rate": (len(accepted) / offered) if offered else None,
            "response_rate": (len(answered) / offered) if offered else None,
            "cancellation_rate": (
                grp["cancelled_later"].sum() / len(accepted) if len(accepted) else None
            ),
            "sample_size_warning": offered < 10,
        }
    return stats

def record_response(shipment_id, transporter_name, response, log_path: Path = None):
    import pandas as pd

    log_path = Path(log_path) if log_path else LOG_PATH
    with _lock:
        if not log_path.exists():
            return False

        df = pd.read_csv(log_path, dtype=str, keep_default_na=False)
        mask = (df["shipment_id"] == str(shipment_id)) & (df["transporter_name"] == transporter_name)
        if not mask.any():
            return False

        now = datetime.now().isoformat(timespec="seconds")
        df.loc[mask, "responded_at"] = now
        df.loc[mask, "response"] = response
        df.to_csv(log_path, index=False)
        return True

def assign_transporter(shipment_id, transporter_name, log_path: Path = None):
    import pandas as pd

    log_path = Path(log_path) if log_path else LOG_PATH
    with _lock:
        if not log_path.exists():
            return False

        df = pd.read_csv(log_path, dtype=str, keep_default_na=False)
        mask = (df["shipment_id"] == str(shipment_id)) & (df["transporter_name"] == transporter_name)
        if not mask.any():
            return False

        df.loc[mask, "assigned"] = "True"
        df.to_csv(log_path, index=False)
        return True

def mark_cancelled(shipment_id, transporter_name, log_path: Path = None):
    import pandas as pd

    log_path = Path(log_path) if log_path else LOG_PATH
    with _lock:
        if not log_path.exists():
            return False

        df = pd.read_csv(log_path, dtype=str, keep_default_na=False)
        mask = (df["shipment_id"] == str(shipment_id)) & (df["transporter_name"] == transporter_name)
        if not mask.any():
            return False

        df.loc[mask, "cancelled_later"] = "True"
        df.to_csv(log_path, index=False)
        return True