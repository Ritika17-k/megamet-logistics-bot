import re
from datetime import datetime, timedelta
import places

REQUIRED_FIELDS = ["pickup", "destination", "weight_tons", "distance_km"]

_WEIGHT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ton|tons|tonne|tonnes|t\b|kg|kgs|kilo|kilos)",
    re.IGNORECASE,
)
_BARE_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")

_VEHICLE_KEYWORDS = {
    "container": "Container",
    "trailer": "Trailer",
    "open body": "Open Body",
    "closed body": "Closed Body",
    "tempo": "Tempo",
    "32 ft": "32ft Multi-axle",
    "multi axle": "Multi-axle",
    "multi-axle": "Multi-axle",
    "16 wheeler": "16 Wheeler",
    "10 wheeler": "10 Wheeler",
    "6 wheeler": "6 Wheeler",
    "12 wheeler": "12 Wheeler",
    "14 wheeler": "14 Wheeler",
    "lorry": "Lorry",
}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DATE_NUMERIC_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
_MONTH_ALTERNATION = "|".join(sorted(_MONTHS.keys(), key=len, reverse=True))
_DATE_TEXT_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALTERNATION})(?:\s+(\d{{4}}))?\b|"
    rf"\b({_MONTH_ALTERNATION})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)

_FROM_TO_RE = re.compile(
    r"(?:from\s+)?([a-z\s]+?)\s+(?:to|se)\s+([a-z\s]+?)(?:\s+tak\b)?"
    r"(?=[.,;/-]|\s+\d|\s+on\b|\s+for\b|\s+with\b|\s+by\b|\s+from\b|\s+tomorrow\b|\s+today\b|\s*$)",
    re.IGNORECASE,
)

def _extract_pickup_destination(text_lower: str):
    m = _FROM_TO_RE.search(text_lower)
    if m:
        pickup_raw, dest_raw = m.group(1).strip(), m.group(2).strip()
        pickup_key, _ = places.find_place_in_text(pickup_raw)
        dest_key, _ = places.find_place_in_text(dest_raw)
        if pickup_key or dest_key:
            return pickup_key, dest_key

    key, _ = places.find_place_in_text(text_lower)
    if key:
        return "__ambiguous__", key
    return None, None

_FILLER = r"(?:\s*(?:from|at|is|to|in|point|place|city|location|address|change|changed|should\s+be|will\s+be|:|-|=))*"
_CHUNK = r"\s*([a-z][a-z\s]{1,40}?)"
_STOP = (r"(?=\s+(?:to|and|se|tak|from|for|on|by|with|pickup|pick|drop|destination|delivery|"
         r"loading|unloading|going|small|big|chota|chhota|bada|need|want|tomorrow|today|kal|on)\b|"
         r"\s*\d|[.,;/!?\n]|\s*$)")
_PICKUP_SPECIFIC_RE = re.compile(
    r"\b(?:pick\s*-?\s*up|loading(?:\s+point)?|origin|source|dispatch\s+from|despatch\s+from)\b"
    + _FILLER + _CHUNK + _STOP, re.IGNORECASE)
_DEST_SPECIFIC_RE = re.compile(
    r"\b(?:destination|dest|drop(?:\s*-?\s*off)?|deliver(?:y|ed)?|unloading(?:\s+point)?|unload|going\s+to|goes\s+to|send\s+to|reach)\b"
    + _FILLER + _CHUNK + _STOP, re.IGNORECASE)
_PICKUP_GENERIC_RE = re.compile(r"\bfrom" + _CHUNK + _STOP, re.IGNORECASE)
_DEST_GENERIC_RE = re.compile(r"\bto" + _CHUNK + _STOP, re.IGNORECASE)
_SE_RE = re.compile(r"\b([a-z]+(?:\s+[a-z]+)?)\s+se\b", re.IGNORECASE)
_TAK_RE = re.compile(r"\b([a-z]+(?:\s+[a-z]+)?)\s+tak\b", re.IGNORECASE)

def _first_place(regex, text, avoid_spans=()):
    for m in regex.finditer(text):
        if any(a <= m.start() < b for a, b in avoid_spans):
            continue
        key, _ = places.find_place_in_text(m.group(1))
        if key:
            return key, m.span()
    return None, None

def extract_tagged_places(text_lower: str):
    pickup, p_span = _first_place(_PICKUP_SPECIFIC_RE, text_lower)
    dest, d_span = _first_place(_DEST_SPECIFIC_RE, text_lower,
                                avoid_spans=[p_span] if p_span else [])
    spans = [s for s in (p_span, d_span) if s]

    m = _FROM_TO_RE.search(text_lower)
    if m and not (pickup and dest):
        pk, _ = places.find_place_in_text(m.group(1).strip())
        dk, _ = places.find_place_in_text(m.group(2).strip())
        if pk and dk and not pickup and not dest:
            pickup, dest = pk, dk
        elif pk or dk:
            if not pickup and pk and pk != dest:
                pickup = pk
            if not dest and dk and dk != pickup:
                dest = dk
            spans.append(m.span())

    if not pickup:
        for rx in (_PICKUP_GENERIC_RE, _SE_RE):
            k, sp = _first_place(rx, text_lower, avoid_spans=spans)
            if k and k != dest:
                pickup = k
                spans.append(sp)
                break
    if not dest:
        for rx in (_DEST_GENERIC_RE, _TAK_RE):
            k, sp = _first_place(rx, text_lower, avoid_spans=spans)
            if k and k != pickup:
                dest = k
                spans.append(sp)
                break
    return pickup, dest, spans

SMALL_MAX_TONS = 9
_SMALL_RE = re.compile(
    r"\b(small|smaller|chota|chhota|chhoti|choti|chote|chhote|chotta|mini|tempo|lcv|tata\s*ace|"
    r"chhota\s*hathi|407|709|1109|dost|bolero|6\s*(?:wheeler|tyre|tyres|wheel))\b",
    re.IGNORECASE)
_BIG_RE = re.compile(
    r"\b(big|bigger|bada|badi|bade|baada|large|heavy|trailer|container|multi\s*-?\s*axle|"
    r"(?:10|12|14|16|18|22)\s*(?:wheeler|tyre|tyres|wheel)|(?:20|22|24|32|40)\s*(?:ft|feet)|"
    r"full\s+truck|ftl|hcv|taurus)\b",
    re.IGNORECASE)
_ANY_SIZE_RE = re.compile(
    r"\b(not\s+sure|dont\s+know|don'?t\s+know|no\s+idea|pata\s+nahi|pata\s+nahin|any(?:\s+size)?|"
    r"koi\s+bhi|anything|either|you\s+decide|aap\s+dekh\s+lo)\b",
    re.IGNORECASE)

def size_from_weight(tons):
    if tons in (None, ""):
        return None
    return "Small" if float(tons) <= SMALL_MAX_TONS else "Big"

def _extract_vehicle_size(text_lower: str, weight=None):
    small = bool(_SMALL_RE.search(text_lower))
    big = bool(_BIG_RE.search(text_lower))
    if small and not big:
        return "Small"
    if big and not small:
        return "Big"
    if weight:
        return size_from_weight(weight)
    if _ANY_SIZE_RE.search(text_lower):
        return "Any"
    return None

def _extract_weight(text_lower: str):
    m = _WEIGHT_RE.search(text_lower)
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("kg") or unit.startswith("kilo"):
        return round(value / 1000, 3)
    return value

def _extract_vehicle_type(text_lower: str):
    for keyword, label in _VEHICLE_KEYWORDS.items():
        if keyword in text_lower:
            return label
    return None

def _extract_date(text_lower: str, today: datetime = None):
    today = today or datetime.now()

    if re.search(r"\b(today|aaj|abhi)\b", text_lower):
        return today.strftime("%d-%b-%Y")
    if re.search(r"\b(day after tomorrow|parso|parson)\b", text_lower):
        return (today + timedelta(days=2)).strftime("%d-%b-%Y")
    if re.search(r"\b(tomorrow|tmrw|tmr|kal)\b", text_lower):
        return (today + timedelta(days=1)).strftime("%d-%b-%Y")

    m = _DATE_TEXT_RE.search(text_lower)
    if m:
        if m.group(1) and m.group(2):
            day, month_word, year_word = int(m.group(1)), m.group(2), m.group(3)
        else:
            month_word, day, year_word = m.group(4), int(m.group(5)), m.group(6)
        month = _MONTHS.get(month_word[:3].lower()) if month_word else None
        if month:
            explicit_year = bool(year_word)
            year = int(year_word) if year_word else today.year
            try:
                candidate = datetime(year, month, day)
            except ValueError:
                return None
            if not explicit_year and candidate.date() < today.date():
                candidate = datetime(year + 1, month, day)
            return candidate.strftime("%d-%b-%Y")

    m = _DATE_NUMERIC_RE.search(text_lower)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year_part = m.group(3)
        if year_part:
            year = int(year_part) if len(year_part) == 4 else 2000 + int(year_part)
        else:
            year = today.year
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            return None
        if not year_part and candidate.date() < today.date():
            candidate = datetime(year + 1, month, day)
        return candidate.strftime("%d-%b-%Y")

    return None

def parse_message(text: str, today: datetime = None) -> dict:
    text_lower = (text or "").strip().lower()

    pickup_key, dest_key, spans = extract_tagged_places(text_lower)

    rest = text_lower
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    other_key, _ = places.find_place_in_text(rest)
    if other_key in (pickup_key, dest_key):
        other_key = None

    ambiguous_place = None
    if other_key:
        if pickup_key and not dest_key:
            dest_key = other_key
        elif dest_key and not pickup_key:
            pickup_key = other_key
        elif not pickup_key and not dest_key:
            ambiguous_place = other_key

    weight = _extract_weight(text_lower)
    vehicle_type = _extract_vehicle_type(text_lower)
    vehicle_size = _extract_vehicle_size(text_lower, weight)
    required_date = _extract_date(text_lower, today=today)

    distance_km = None
    if pickup_key and dest_key:
        distance_km = places.road_distance_km(pickup_key, dest_key)

    def _show(key):
        if not key:
            return None
        if pickup_key != dest_key:
            area = places.area_mention(text_lower, key)
            if area:
                return area
        return places.place_display(key)

    request = {
        "pickup": _show(pickup_key),
        "destination": _show(dest_key),
        "weight_tons": weight,
        "vehicle_size": vehicle_size,
        "distance_km": distance_km,
        "required_date": required_date,
        "vehicle_type": vehicle_type,
        "raw_text": text,
    }
    if ambiguous_place:
        request["_ambiguous_place"] = _show(ambiguous_place)

    request["missing"] = [f for f in ("pickup", "destination", "vehicle_size") if not request[f]]
    return request

def parse_bare_field_answer(field: str, text: str, today: datetime = None):
    text = (text or "").strip()
    if not text:
        return None

    if field == "vehicle_size":
        m = _BARE_NUMBER_RE.match(text)
        if m:
            return size_from_weight(float(m.group(1)))
        return _extract_vehicle_size(text.lower(), _extract_weight(text.lower()))

    if field == "weight_tons":
        m = _BARE_NUMBER_RE.match(text)
        if m:
            return float(m.group(1))
        return _extract_weight(text.lower())

    if field in ("pickup", "destination"):
        return places.resolve_place(text)

    if field == "required_date":
        return _extract_date(text.lower(), today=today)

    return None

def merge_requests(base: dict, update: dict) -> dict:
    merged = dict(base)
    for key in ("pickup", "destination", "weight_tons", "distance_km", "required_date", "vehicle_type"):
        if update.get(key) not in (None, ""):
            merged[key] = update[key]
    merged["raw_text"] = update.get("raw_text", base.get("raw_text"))

    if merged.get("pickup") and merged.get("destination") and not merged.get("distance_km"):
        pickup_key = places.resolve_place(merged["pickup"])
        dest_key = places.resolve_place(merged["destination"])
        if pickup_key and dest_key:
            merged["distance_km"] = places.road_distance_km(pickup_key, dest_key)

    missing = []
    if not merged.get("pickup"):
        missing.append("pickup")
    if not merged.get("destination"):
        missing.append("destination")
    if not merged.get("weight_tons"):
        missing.append("weight_tons")
    merged["missing"] = missing
    return merged