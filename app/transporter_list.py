import re
from pathlib import Path
import places

LIST_PATH = Path(__file__).parent.parent / "data" / "ops_supplied" / "TRANSPORTER_LIST.xlsx"
ADDED_PATH = LIST_PATH.parent / "added_transporters.csv"
REMOVED_PATH = LIST_PATH.parent / "removed_transporters.csv"
ADDED_COLS = ["name", "contact", "phone", "base", "type", "destinations", "size", "added_by", "added_at"]
_METRO = {"navi mumbai": "mumbai", "thane": "mumbai"}
LOCAL_RADIUS_KM = 100

_cache = {"mtime": None, "rows": []}

def hub(place):
    if not place:
        return None
    text = re.sub(r"\(.*?\)", " ", str(place))
    text = re.sub(r"[-,]?\s*\d{6}\b", " ", text)
    key = places.resolve_place(text) or places.resolve_place(str(place))
    return _METRO.get(key, key) if key else None

def near(a, b, radius_km=LOCAL_RADIUS_KM) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    d = places.road_distance_km(a, b)
    return d is not None and d <= radius_km

def distance(a, b):
    if not a or not b:
        return None
    return 0 if a == b else places.road_distance_km(a, b)

def _clean(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""

def _digits(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return "".join(c for c in str(v) if c.isdigit())

def _mobile(d: str):
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 and d[0] in "6789" else None

_FROM_RE = re.compile(r"\bfrom\s+([a-z .,&/]+)", re.I)
_CELLREF_RE = re.compile(r"\b[A-Z]{1,2}\d+(?::[A-Z]{1,2}\d+)?\b")

def _bases_from(text):
    m = _FROM_RE.search(text or "")
    if not m:
        return []
    out = []
    for part in re.split(r"[,&/]|\band\b", m.group(1)):
        k = hub(part)
        if k and k not in out:
            out.append(k)
    return out

def _base_from(text):
    b = _bases_from(text)
    return b[0] if b else None

def _size(vehicle_text: str) -> str:
    t = vehicle_text.lower()
    if "small" in t:
        return "Small"
    if "big" in t:
        return "Big"
    if "all type" in t or "any" in t:
        return "Any"
    if any(w in t for w in ("trailer", "trailor", "lorry", "container")):
        return "Big"
    if any(w in t for w in ("tempo", "pickup", "lcv")):
        return "Small"
    return "Any"

def _parse(path: Path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]

    head_i, cols = None, {}
    for i, r in enumerate(rows):
        labels = [_clean(c).upper() for c in r]
        if any("MOBILE" in c for c in labels):
            head_i = i
            for j, c in enumerate(labels):
                if "SR" in c and "NO" in c:
                    cols["sr"] = j
                elif "TRANSPORTER" in c:
                    cols["name"] = j
                elif "MOBILE" in c or "PHONE" in c:
                    cols["phone"] = j
                elif c.startswith("NAME") or "CONTACT" in c:
                    cols["contact"] = j
                elif "VEHICLE" in c:
                    cols["vehicle"] = j
                elif "DESTINATION" in c or "ROUTE" in c:
                    cols["dest"] = j
                elif c.startswith("FROM") or c in ("BASE", "BASED IN"):
                    cols["from"] = j
                elif "WAREHOUSE" in c or "PORT" in c:
                    cols["kind"] = j
                elif "LANG" in c:
                    cols["lang"] = j
            break
    if head_i is None:
        raise ValueError(f"{path.name}: no header row with 'MOBILE' found")

    def cell(r, k):
        j = cols.get(k)
        return r[j] if j is not None and j < len(r) else None

    groups = []
    for r in rows[head_i + 1:]:
        if not any(c is not None and _clean(c) for c in r):
            continue
        if cell(r, "sr") is not None and _clean(cell(r, "sr")) or cell(r, "name"):
            groups.append({"sr": _clean(cell(r, "sr")), "lines": [r]})
        elif groups:
            groups[-1]["lines"].append(r)

    out = []
    for g in groups:
        first = g["lines"][0]
        name = _clean(cell(first, "name"))
        if not name:
            continue
        contacts, phones, vehicle, dests_raw, problems, lang = [], [], [], [], [], ""
        base = None
        bases = []
        kinds = []
        tag = ""
        mt = re.search(r"\((old|new)\)", name, re.I)
        if mt:
            tag = mt.group(1).lower()
            name = _clean(re.sub(r"\((old|new)\)", " ", name, flags=re.I))
        if "contact" not in cols and "/" in name:
            name, _, who = name.partition("/")
            name, who = _clean(name), _clean(who)
            company = r"\b(transport|transports|logistics?|logisitcs|roadlines?|carriers?|cargo|movers|travels)\b"
            if re.search(company, who, re.I) and not re.search(company, name, re.I):
                name, who = who, name
            contacts_from_name = [w.strip().title() for w in who.split("/") if w.strip()]
        else:
            contacts_from_name = []
        contacts += contacts_from_name
        for r in g["lines"]:
            c = _clean(cell(r, "contact"))
            if c:
                if _FROM_RE.search(c):
                    bases += [b for b in _bases_from(c) if b not in bases]
                    base = base or (bases[0] if bases else None)
                else:
                    contacts.append(c.title())
            p = _digits(cell(r, "phone"))
            if p:
                m = _mobile(p)
                if m:
                    phones.append(m)
                elif len(p) == 10 and p[0] in "2345":
                    problems.append(f"{p} looks like a landline - kept for calls, no WhatsApp")
                    phones.append(("landline", p))
                else:
                    problems.append(f"phone '{p}' is not a valid 10-digit mobile - fix in sheet")
            v = _clean(cell(r, "vehicle"))
            if v:
                vehicle.append(v)
            d = _clean(cell(r, "dest"))
            if d:
                dests_raw.append(d)
            f = _clean(cell(r, "from"))
            if f:
                bases += [b for b in _city_list(f) if b not in bases]
                base = base or (bases[0] if bases else None)
            k = _clean(cell(r, "kind"))
            if k:
                kinds.append(k.lower())
            if not lang and _clean(cell(r, "lang")):
                lang = _clean(cell(r, "lang")).lower()[:2]

        dest_text = " ".join(dests_raw)
        low = dest_text.lower()
        pan_india = bool(re.search(r"pan\s*india|all\s*india", low))
        local = "local" in low
        bases += [b for b in _bases_from(dest_text) if b not in bases]
        base = base or (bases[0] if bases else None)
        if base is None and not pan_india:
            base = hub(name)
        if base is None and pan_india and "all india" not in low:
            base = hub(name)
        if base and base not in bases:
            bases.insert(0, base)

        destinations = []
        if not pan_india:
            for d in dests_raw:
                d = _CELLREF_RE.sub(" ", d)
                if _FROM_RE.search(d):
                    continue
                ks = _city_list(d)
                for k in ks:
                    if k not in destinations:
                        destinations.append(k)
                if not ks:
                    problems.append(f"destination '{d}' not recognised")
            if not destinations:
                local = True
        if base is None and local and destinations:
            base = destinations[0]
            bases.insert(0, base)
        kind_text = " ".join(kinds)
        warehouse = "warehouse" in kind_text or not kinds
        port = "port" in kind_text

        mobiles = [p for p in phones if isinstance(p, str)]
        landlines = [p[1] for p in phones if isinstance(p, tuple)]
        if not mobiles:
            problems.append("no WhatsApp-able mobile number - cannot be invited")

        out.append({
            "sr": g["sr"],
            "name": name.title() if name.isupper() else name,
            "contact": " / ".join(contacts),
            "phone": mobiles[0] if mobiles else None,
            "alt_phones": mobiles[1:] + landlines,
            "base": base,
            "bases": bases,
            "size": _size(" ".join(vehicle)),
            "vehicle": " ".join(vehicle),
            "pan_india": pan_india,
            "local": local,
            "destinations": destinations,
            "language": lang or "en",
            "warehouse": warehouse,
            "port": port,
            "tag": tag,
            "problems": problems,
        })

    seen = {}
    for t in out:
        k = t["name"].lower()
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            t["name"] = f"{t['name']} ({seen[k]})"
    by_phone, uniq = {}, []
    for t in out:
        if t["phone"] and t["phone"] in by_phone:
            by_phone[t["phone"]]["problems"].append(f"same number as row {t['sr']} ({t['name']}) - merged")
            continue
        if t["phone"]:
            by_phone[t["phone"]] = t
        uniq.append(t)
    return uniq

def _mt(p):
    return p.stat().st_mtime if p.exists() else None

def read_added():
    import csv
    if not ADDED_PATH.exists():
        return []
    with open(ADDED_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def read_removed():
    import csv
    if not REMOVED_PATH.exists():
        return set()
    with open(REMOVED_PATH, newline="", encoding="utf-8") as f:
        return {r.get("phone", "").strip() for r in csv.DictReader(f)}

def _city_list(text):
    out = []
    t = re.sub(r"\((?:local)\)", " ", text or "", flags=re.I)
    t = re.sub(r"\b(only|local|both)\b|\d+", " ", t, flags=re.I)
    for part in re.split(r"[,&/+;]|\band\b", t, flags=re.I):
        if not part.strip():
            continue
        k = hub(part)
        if k and k not in out:
            out.append(k)
    return out

def _added_bases(text):
    return _city_list(text)

def _from_added(r):
    kind = (r.get("type") or "").lower()
    dests = [k for k in (hub(d) for d in (r.get("destinations") or "").split(",") if d.strip()) if k]
    return {
        "sr": "WA", "name": r["name"], "contact": r.get("contact") or "",
        "phone": _mobile(_digits(r.get("phone"))), "alt_phones": [],
        "base": _added_bases(r.get("base"))[0] if _added_bases(r.get("base")) else None,
        "bases": _added_bases(r.get("base")),
        "size": r.get("size") or "Any", "vehicle": r.get("size") or "",
        "pan_india": "pan" in kind, "local": "local" in kind or (not dests and "pan" not in kind),
        "destinations": dests, "language": "en", "test": "test" in kind,
        "warehouse": "port" not in kind or "warehouse" in kind, "port": "port" in kind, "tag": "",
        "problems": [f"added on WhatsApp by +{r.get('added_by')} on {(r.get('added_at') or '')[:10]}"],
    }

def load(path: Path = None):
    path = Path(path) if path else LIST_PATH
    if not path.exists():
        raise FileNotFoundError(f"Transporter list not found: {path}")
    key = (str(path), _mt(path), _mt(ADDED_PATH), _mt(REMOVED_PATH))
    if _cache["mtime"] != key:
        rows = _parse(path)
        have = {t["phone"] for t in rows if t["phone"]}
        names = {t["name"].lower() for t in rows}
        for r in read_added():
            t = _from_added(r)
            if t["phone"] and t["phone"] not in have:
                if t["name"].lower() in names:
                    t["name"] = f"{t['name']} ({t['phone'][-4:]})"
                rows.append(t)
                have.add(t["phone"])
                names.add(t["name"].lower())
        removed = read_removed()
        _cache["rows"] = [t for t in rows if not (t["phone"] and t["phone"] in removed)]
        _cache["mtime"] = key
    return [dict(t) for t in _cache["rows"]]

def by_name(name, path: Path = None):
    for t in load(path):
        if t["name"] == name:
            return t
    return None

def _bases(t):
    return t.get("bases") or ([t["base"]] if t.get("base") else [])

def based_near(t, place_hub) -> bool:
    return any(near(b, place_hub) for b in _bases(t))

def dist_from(t, place_hub):
    ds = [d for d in (distance(b, place_hub) for b in _bases(t)) if d is not None]
    return min(ds) if ds else None

def covers(t, dest_hub) -> bool:
    return bool(dest_hub) and dest_hub in t["destinations"]

def describe(t) -> str:
    kind = ("Pan-India" + (" + Local" if t["local"] else "")) if t["pan_india"] else (
        "Route" if t["destinations"] else "Local")
    bases = t.get("bases") or ([t["base"]] if t["base"] else [])
    base = "/".join(places.place_display(b) for b in bases) if bases else "anywhere"
    routes = ", ".join(places.place_display(d) for d in t["destinations"])
    use = "+".join(x for x, ok in (("WH", t.get("warehouse", True)), ("PORT", t.get("port"))) if ok)
    return f"{kind:<18} {use:<7} base={base:<12} size={t['size']:<5} {('-> ' + routes) if routes else ''}"

if __name__ == "__main__":
    rows = load()
    print(f"{len(rows)} transporters read from {LIST_PATH.name}\n")
    for t in rows:
        print(f"{t['sr']:>3} {t['name'][:28]:<28} {t['phone'] or '-':<11} {describe(t)}")
        for p in t["problems"]:
            print(f"      ! {p}")
