from pathlib import Path
import pandas as pd
from locations import suggest_region_from_coverage
from schema import (
    SHIPMENT_COLUMNS, SHIPMENT_REQUIRED,
    TRANSPORTER_COLUMNS, TRANSPORTER_REQUIRED,
    OPS_SUPPLIED_FIELDS,
    SHEET_SHIPMENTS, SHEET_TRANSPORTERS,
    REGIONAL_DISTANCE_THRESHOLD_KM,
)

DEFAULT_DATA_PATH = Path(__file__).parent.parent / "data" / "raw" / "Logistics_Master.xlsx"

class DataSourceError(Exception):
    pass

def _apply_schema(df: pd.DataFrame, column_map: dict, required: list, label: str) -> pd.DataFrame:
    present = {src: dst for src, dst in column_map.items() if src in df.columns}
    df = df[list(present.keys())].rename(columns=present)

    missing_required = [c for c in required if c not in df.columns]
    if missing_required:
        raise DataSourceError(
            f"{label}: required column(s) missing after mapping: {missing_required}. "
            f"Columns found in file: {list(df.columns)}. "
            f"Check schema.py if the source file layout has changed."
        )

    for dst in column_map.values():
        if dst not in df.columns:
            df[dst] = None
    return df

def load_shipments(path: Path = None) -> pd.DataFrame:
    path = Path(path) if path else DEFAULT_DATA_PATH
    if not path.exists():
        raise DataSourceError(f"Data file not found: {path}")
    df = pd.read_excel(path, sheet_name=SHEET_SHIPMENTS)
    df.columns = [str(c).strip() for c in df.columns]
    df = _apply_schema(df, SHIPMENT_COLUMNS, SHIPMENT_REQUIRED, "Shipments")
    df = df[df["voucher_no"].notna()]
    df = df[~df["customer"].astype(str).str.contains("Total", case=False, na=False)]
    df["distance_km"] = pd.to_numeric(df["distance_km"], errors="coerce")
    return df.reset_index(drop=True)

def load_transporters(path: Path = None) -> pd.DataFrame:
    path = Path(path) if path else DEFAULT_DATA_PATH
    if not path.exists():
        raise DataSourceError(f"Data file not found: {path}")

    df = pd.read_excel(path, sheet_name=SHEET_TRANSPORTERS)
    df.columns = [str(c).strip() for c in df.columns]
    df = _apply_schema(df, TRANSPORTER_COLUMNS, TRANSPORTER_REQUIRED, "Transporters")
    df = df[df["transporter_name"].notna()]
    df = df[~df["transporter_name"].astype(str).str.contains("Total", case=False, na=False)]
    df["historical_shipments"] = pd.to_numeric(df["historical_shipments"], errors="coerce").fillna(0)
    df["avg_distance_km"] = pd.to_numeric(df["avg_distance_km"], errors="coerce")
    df["vehicle_capacity_tons"] = pd.to_numeric(df["vehicle_capacity_tons"], errors="coerce")
    df["acceptance_rate"] = pd.to_numeric(df["acceptance_rate"], errors="coerce")

    return df.reset_index(drop=True)

def data_completeness_report(transporters: pd.DataFrame) -> dict:
    total = len(transporters)
    report = {}
    for field in OPS_SUPPLIED_FIELDS:
        if field in transporters.columns:
            filled = int(transporters[field].notna().sum())
        else:
            filled = 0
        report[field] = {"filled": filled, "total": total, "using_proxy": filled == 0}
    return report

DEFAULT_CONTACTS_PATH = Path(__file__).parent.parent / "data" / "ops_supplied" / "transporter_contacts.csv"
CONTACT_FIELDS = [
    "phone", "confirmed_type", "vehicle_capacity_tons",
    "route_coverage", "availability", "acceptance_rate",
    "language",
    "contact_person", "alt_phone", "pickup_from", "vehicle",
]

SAME_TRANSPORTER = {
    "NATH KRUPA TRAILER": "NATH KRUPA TRANSPORT",
    "NAEEM SHAIKH TRANSPORT": "SANIYA TRANSPORT",
    "BABU TRANSPORT": "ANNAMALAI TRANSPORT",
}

def _merge_same_transporters(transporters: pd.DataFrame) -> pd.DataFrame:
    names = transporters["transporter_name"].astype(str).str.strip().str.upper()
    for alias, keep in SAME_TRANSPORTER.items():
        a_idx = transporters.index[names == alias]
        k_idx = transporters.index[names == keep]
        if a_idx.empty:
            continue
        if k_idx.empty:
            transporters.loc[a_idx[0], "transporter_name"] = keep
            continue
        a, k = a_idx[0], k_idx[0]
        a_n = transporters.at[a, "historical_shipments"] or 0
        k_n = transporters.at[k, "historical_shipments"] or 0
        a_d, k_d = transporters.at[a, "avg_distance_km"], transporters.at[k, "avg_distance_km"]
        if pd.notna(a_d) and pd.notna(k_d) and (a_n + k_n):
            transporters.at[k, "avg_distance_km"] = (a_d * a_n + k_d * k_n) / (a_n + k_n)
        elif pd.notna(a_d):
            transporters.at[k, "avg_distance_km"] = a_d
        transporters.at[k, "historical_shipments"] = a_n + k_n
        for col in transporters.columns:
            if col in ("transporter_name", "historical_shipments", "avg_distance_km"):
                continue
            if pd.isna(transporters.at[k, col]) and pd.notna(transporters.at[a, col]):
                transporters.at[k, col] = transporters.at[a, col]
        transporters = transporters.drop(index=a)
        names = transporters["transporter_name"].astype(str).str.strip().str.upper()
    return transporters.reset_index(drop=True)


IN_HOUSE_VEHICLES = {"SELF VEHICLE (IN-HOUSE)"}


def load_transporters_with_contacts(data_path: Path = None, contacts_path: Path = None) -> pd.DataFrame:
    df = _load_transporters_with_contacts(data_path, contacts_path)
    names = df["transporter_name"].astype(str).str.strip().str.upper()
    return df[~names.isin(IN_HOUSE_VEHICLES)].reset_index(drop=True)


def _load_transporters_with_contacts(data_path: Path = None, contacts_path: Path = None) -> pd.DataFrame:
    transporters = load_transporters(data_path)
    contacts_path = Path(contacts_path) if contacts_path else DEFAULT_CONTACTS_PATH
    if not contacts_path.exists():
        return transporters

    contacts = pd.read_csv(contacts_path, dtype=str, keep_default_na=False)
    contacts = contacts.replace("", pd.NA)
    contacts.columns = [str(c).strip() for c in contacts.columns]
    if "transporter_name" not in contacts.columns:
        return transporters
    
    contacts = contacts.set_index(
        contacts["transporter_name"].astype(str).str.strip().str.upper()
    )

    key = transporters["transporter_name"].astype(str).str.strip().str.upper()
    for field in CONTACT_FIELDS:
        if field not in contacts.columns:
            continue
        looked_up = key.map(contacts[field])
        if field in transporters.columns:
            transporters[field] = transporters[field].where(
                transporters[field].notna(), looked_up
            )
        else:
            transporters[field] = looked_up

    for numeric_field in ("vehicle_capacity_tons", "acceptance_rate"):
        if numeric_field in transporters.columns:
            transporters[numeric_field] = pd.to_numeric(
                transporters[numeric_field], errors="coerce"
            )
    return _merge_same_transporters(_append_new_transporters(transporters))

DEFAULT_RATES_PATH = Path(__file__).parent.parent / "data" / "ops_supplied" / "transporter_rates.csv"

def load_rates(path: Path = None) -> list:
    path = Path(path) if path else DEFAULT_RATES_PATH
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    rows = []
    for r in df.to_dict("records"):
        try:
            fare = float(str(r.get("fare_inr", "")).replace(",", ""))
        except ValueError:
            continue
        rows.append({**r, "transporter_name": str(r.get("transporter_name", "")).strip().upper(),
                     "fare_inr": fare})
    return rows


def fare_for(transporter_name: str, request: dict, rates: list = None):
    fares = fares_for(transporter_name, request, rates)
    return min(fares) if fares else None

def fare_text(transporter_name: str, request: dict, rates: list = None) -> str:
    fares = sorted(set(fares_for(transporter_name, request, rates)))
    if not fares:
        return ""
    fmt = lambda f: _inr(int(f))
    return f"₹{fmt(fares[0])}" if len(fares) == 1 else f"₹{fmt(fares[0])}–{fmt(fares[-1])}"

def _inr(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:]); head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail

def fares_for(transporter_name: str, request: dict, rates: list = None) -> list:
    from matching import covers_destination
    rates = load_rates() if rates is None else rates
    name = str(transporter_name).strip().upper()
    found = []
    for r in rates:
        if r["transporter_name"] != name:
            continue
        if not covers_destination(r.get("destination"), request.get("destination")):
            continue
        if r.get("pickup") and request.get("pickup") and not covers_destination(r["pickup"], request["pickup"]):
            continue
        found.append(r["fare_inr"])
    return found

DEFAULT_NEW_TRANSPORTERS_PATH = Path(__file__).parent.parent / "data" / "ops_supplied" / "new_transporters.csv"

def _append_new_transporters(transporters: pd.DataFrame, path: Path = None) -> pd.DataFrame:
    path = Path(path) if path else DEFAULT_NEW_TRANSPORTERS_PATH
    if not path.exists():
        return transporters
    
    new_df = pd.read_csv(path, dtype=str, keep_default_na=False).replace("", pd.NA)
    new_df.columns = [str(c).strip() for c in new_df.columns]
    if "transporter_name" not in new_df.columns:
        return transporters

    existing_names = set(transporters["transporter_name"].astype(str).str.strip().str.upper())
    new_df = new_df[~new_df["transporter_name"].astype(str).str.strip().str.upper().isin(existing_names)]
    if new_df.empty:
        return transporters

    new_df["historical_shipments"] = 0
    new_df["avg_distance_km"] = pd.NA
    if "confirmed_type" not in new_df.columns:
        new_df["confirmed_type"] = pd.NA
    new_df["suggested_type"] = new_df.get("route_coverage", pd.Series(dtype=str)).apply(
        lambda coverage: suggest_region_from_coverage(coverage, REGIONAL_DISTANCE_THRESHOLD_KM)
    )
    if "vehicle_capacity_tons" in new_df.columns:
        new_df["vehicle_capacity_tons"] = pd.to_numeric(new_df["vehicle_capacity_tons"], errors="coerce")
    if "acceptance_rate" in new_df.columns:
        new_df["acceptance_rate"] = pd.to_numeric(new_df["acceptance_rate"], errors="coerce")

    for col in transporters.columns:
        if col not in new_df.columns:
            new_df[col] = pd.NA
    new_df = new_df[transporters.columns]

    return pd.concat([transporters, new_df], ignore_index=True)