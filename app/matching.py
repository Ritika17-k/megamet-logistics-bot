import pandas as pd
import places
from schema import REGIONAL_DISTANCE_THRESHOLD_KM

_MUMBAI_METRO = {"mumbai", "navi mumbai", "thane"}

def _place_key(text):
    text = (text or "").strip().lower()
    if not text:
        return ""
    key = places.resolve_place(text) or text
    return "mumbai" if key in _MUMBAI_METRO else key

def _name_key(name) -> str:
    return "".join(ch for ch in str(name or "").upper() if ch.isalnum())

ANYWHERE_TRANSPORTERS = {_name_key(n) for n in (
    "NATH KRUPA TRANSPORT",
    "NATH KRUPA TRAILER",
    "VISHAL ROADLINES",
    "RH ROADLINE",
    "T B PANDEY",
    "O J MISHRA TRANSPORT",
    "KHUSHI LOGISTICS",
    "D V TRANSPORT",
    "NAFISA TRANSPORT",
    "ANNAMALAI TRANSPORT",
    "BABU TRANSPORT",
    "YUKTI ROADLINE",
)}

LOCAL_AND_PAN_INDIA = set(ANYWHERE_TRANSPORTERS)
LOCAL_PICKUP_ANYWHERE = {_name_key("SHREE SANT TRANSPORT")}
LOCAL_PICKUP_RADIUS_KM = 100

def is_anywhere_transporter(name) -> bool:
    return _name_key(name) in ANYWHERE_TRANSPORTERS

def _within_local_region(region_places, pickup, radius_km=LOCAL_PICKUP_RADIUS_KM) -> bool:
    if covers_destination(region_places, pickup):
        return True
    p_key = places.resolve_place(pickup or "")
    if not (p_key and isinstance(region_places, str)):
        return False
    p_state = (places.place_state(p_key) or "").lower()
    for r in region_places.split(","):
        r_key = places.resolve_place(r.strip().lower())
        if not r_key:
            continue
        
        if p_state and (places.place_state(r_key) or "").lower() == p_state:
            return True

        d = places.road_distance_km(r_key, p_key)
        if d is not None and d <= radius_km:
            return True
    return False

def covers_destination(routes, destination) -> bool:
    if not (isinstance(routes, str) and routes.strip() and destination):
        return False
    dest_key = _place_key(destination)
    dest_state = (places.place_state(places.resolve_place(destination) or "") or "").lower()
    for r in routes.split(","):
        r = r.strip().lower()
        if not r:
            continue
        if r in places.STATE_CENTERS and dest_state and _state_name(r) == dest_state:
            return True
        if _place_key(r) == dest_key:
            return True
    return False

def _state_name(state_text):
    center = places.STATE_CENTERS.get(state_text)
    return (places.place_state(center) or state_text).lower() if center else state_text

WEIGHTS = {
    "route_suitability": 0.30,
    "reliability": 0.25,
    "availability": 0.20,
    "capacity_fit": 0.15,
    "price": 0.10,
}

class MatchResult:
    def __init__(self, name, score, reasons, proxies_used, raw):
        self.name = name
        self.score = score
        self.reasons = reasons
        self.proxies_used = proxies_used
        self.raw = raw

    def __repr__(self):
        return f"<MatchResult {self.name} score={self.score:.3f}>"

def classify_region(transporter_row) -> str:
    confirmed = transporter_row.get("confirmed_type")
    if isinstance(confirmed, str) and confirmed.strip():
        return confirmed.strip()
    suggested = transporter_row.get("suggested_type")
    if isinstance(suggested, str) and suggested.strip():
        return suggested.strip()
    return "Unknown"

def classify_region_for_request(transporter_row, request: dict):
    if is_anywhere_transporter(transporter_row.get("transporter_name")):
        return "Pan-India", None
    
    route_confirmed_for_this_job = covers_destination(
        transporter_row.get("route_coverage"), request.get("destination")
    )
    confirmed = transporter_row.get("confirmed_type")
    if route_confirmed_for_this_job and isinstance(confirmed, str) and confirmed.strip():
        return confirmed.strip(), None
    
    avg_dist = transporter_row.get("avg_distance_km")
    req_dist = request.get("distance_km")
    if pd.notna(avg_dist) and req_dist:
        label = "Regional" if avg_dist <= REGIONAL_DISTANCE_THRESHOLD_KM else "Pan-India"
        note = (
            "region: no confirmed route coverage for this destination, so "
            f"classified from historical avg haul length ({int(avg_dist)}km) "
            "vs this request's distance -- not an ops-confirmed regional label"
        )
        return label, note

    if isinstance(confirmed, str) and confirmed.strip() in ("Regional", "Pan-India"):
        return confirmed.strip(), None

    if route_confirmed_for_this_job and req_dist:
        label = "Regional" if req_dist <= REGIONAL_DISTANCE_THRESHOLD_KM else "Pan-India"
        return label, "region: route confirmed but no type/history, classified by this load's distance"

    suggested = transporter_row.get("suggested_type")
    if isinstance(suggested, str) and suggested.strip():
        return (
            suggested.strip(),
            "region: falling back to the old data-derived Mumbai-centric label "
            "-- no route confirmation or distance history for this request",
        )

    return "Unknown", None

def _is_short_haul(row):
    confirmed = row.get("confirmed_type")
    if isinstance(confirmed, str) and confirmed.strip():
        c = confirmed.strip().lower()
        if c in ("local", "regional"):
            return True, f"ops type: {confirmed.strip()}"
        return False, None
    avg = row.get("avg_distance_km")
    if pd.notna(avg):
        if avg <= REGIONAL_DISTANCE_THRESHOLD_KM:
            return True, f"usual haul ~{int(avg)} km"
        return False, None
    suggested = row.get("suggested_type")
    if isinstance(suggested, str) and suggested.strip().lower() == "regional":
        return True, "suggested type: Regional"
    return False, None

def apply_hard_filters(transporters: pd.DataFrame, request: dict) -> tuple:

    survivors = []
    eliminated = []
    required_tons = request.get("weight_tons")

    for _, row in transporters.iterrows():
        name = row["transporter_name"]

        if is_anywhere_transporter(name):
            survivors.append(row)
            continue

        cap = row.get("vehicle_capacity_tons")
        if pd.notna(cap) and required_tons and cap < required_tons:
            eliminated.append((name, f"Vehicle capacity {cap}t is below required {required_tons}t"))
            continue
        avail = row.get("availability")
        if isinstance(avail, str) and avail.strip().lower() in ("no", "unavailable", "busy"):
            eliminated.append((name, "Marked unavailable"))
            continue

        pickup = request.get("pickup")
        pickup_from = row.get("pickup_from") if isinstance(row.get("pickup_from"), str) else ""
        own_region = row.get("route_coverage") if isinstance(row.get("route_coverage"), str) else ""
        ctype = row.get("confirmed_type")
        is_local = isinstance(ctype, str) and ctype.strip().lower() in ("regional", "local")
        if pickup:
            if _name_key(name) in LOCAL_PICKUP_ANYWHERE:
                pickup_ok = True
            elif is_local:
                pickup_ok = (covers_destination(pickup_from or "mumbai", pickup)
                             or covers_destination("mumbai", pickup)
                             or _within_local_region(own_region, pickup))
            else:
                pickup_ok = not pickup_from.strip() or covers_destination(pickup_from, pickup)
            if not pickup_ok:
                eliminated.append((name, f"Doesn't pick up from {pickup}"))
                continue

        routes = row.get("route_coverage")
        dest = request.get("destination")
        if isinstance(routes, str) and routes.strip() and dest:
            if not covers_destination(routes, dest):
                eliminated.append((name, f"Route coverage does not list {request['destination']}"))
                continue

        req_dist = request.get("distance_km")
        if (req_dist and req_dist > REGIONAL_DISTANCE_THRESHOLD_KM
                and not covers_destination(routes, dest)):
            short_haul, why = _is_short_haul(row)
            if short_haul:
                eliminated.append((name, f"Local/short-haul transporter ({why}) -- "
                                         f"not suited to a ~{int(req_dist)} km load"))
                continue
        survivors.append(row)

    return pd.DataFrame(survivors), eliminated

def score_transporter(row, request: dict, max_shipments: int) -> MatchResult:
    reasons = []
    proxies = []
    components = {}

    req_distance = request.get("distance_km")
    avg_dist = row.get("avg_distance_km")
    if pd.notna(avg_dist) and req_distance:
        ratio = min(avg_dist, req_distance) / max(avg_dist, req_distance)
        components["route_suitability"] = ratio
        reasons.append(
            f"Historically ships ~{int(avg_dist)}km on average vs ~{int(req_distance)}km needed here"
        )
    else:
        components["route_suitability"] = 0.5
        proxies.append("route_suitability: no distance history, used neutral 0.5")

    acc = row.get("acceptance_rate")
    if pd.notna(acc):
        components["reliability"] = float(acc)
        reasons.append(f"Confirmed acceptance rate: {acc}")
    else:
        shipments = row.get("historical_shipments") or 0
        if shipments:
            components["reliability"] = (shipments / max_shipments) if max_shipments else 0
            reasons.append(f"{int(shipments)} historical shipments with the company")
            proxies.append("reliability: using shipment count as a stand-in for acceptance history")
        else:
            components["reliability"] = 0.5
            reasons.append("New transporter -- no shipment history yet")
            proxies.append("reliability: new transporter with no history, used neutral 0.5")

    avail = row.get("availability")
    if isinstance(avail, str) and avail.strip().lower() in ("yes", "available", "free"):
        components["availability"] = 1.0
        reasons.append("Confirmed available")
    elif pd.notna(avail) and str(avail).strip():
        components["availability"] = 0.5
    else:
        components["availability"] = 0.5
        proxies.append("availability: unknown, used neutral 0.5")

    cap = row.get("vehicle_capacity_tons")
    req_tons = request.get("weight_tons")
    if pd.notna(cap) and req_tons:
        ratio = req_tons / cap if cap >= req_tons else 0
        components["capacity_fit"] = ratio
        reasons.append(f"Capacity {cap}t for a {req_tons}t load")
    else:
        components["capacity_fit"] = 0.5
        proxies.append("capacity_fit: vehicle capacity not supplied by ops, used neutral 0.5")

    fare = row.get("_fare")
    if fare is not None and pd.notna(fare) and fare > 0 and request.get("_min_fare"):
        components["price"] = request["_min_fare"] / fare
        reasons.append(f"Agreed fare from ₹{int(fare):,}")
    else:
        components["price"] = 0.5
        proxies.append("price: no agreed fare for this route, used neutral 0.5")

    score = sum(WEIGHTS[k] * v for k, v in components.items())

    return MatchResult(
        name=row["transporter_name"],
        score=score,
        reasons=reasons,
        proxies_used=proxies,
        raw=components,
    )

def find_matches(transporters: pd.DataFrame, request: dict, top_n: int = 5) -> dict:
    survivors, eliminated = apply_hard_filters(transporters, request)

    if survivors.empty:
        return {
            "regional": [],
            "pan_india": [],
            "eliminated": eliminated,
            "note": "No transporters passed the hard filters.",
        }
    max_shipments = survivors["historical_shipments"].max() or 1

    from data_loader import load_rates, fare_for
    rates = load_rates()
    survivors = survivors.copy()
    survivors["_fare"] = [fare_for(n, request, rates) for n in survivors["transporter_name"]]
    known = [f for f in survivors["_fare"] if f]
    request = dict(request, _min_fare=min(known) if known else None)
    regional, pan_india = [], []

    for _, row in survivors.iterrows():
        result = score_transporter(row, request, max_shipments)
        region, proxy_note = classify_region_for_request(row, request)
        result.region = region
        if proxy_note:
            result.proxies_used.append(proxy_note)

        if _name_key(row["transporter_name"]) in LOCAL_AND_PAN_INDIA:
            result.region = "Regional & Pan-India"
            result.reasons.append("Big transporter -- considered for every region")
            regional.append(result)
            pan_india.append(result)
        elif region == "Regional":
            regional.append(result)

        elif region == "Pan-India":
            pan_india.append(result)

        else:
            result.reasons.append("Region not yet confirmed by ops, and no distance history to estimate it")

    regional.sort(key=lambda r: (is_anywhere_transporter(r.name), -r.score))
    pan_india.sort(key=lambda r: r.score, reverse=True)

    return {
        "regional": regional[:top_n],
        "pan_india": pan_india[:top_n],
        "eliminated": eliminated,
        "note": None,
    }
