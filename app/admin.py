import csv
import json
import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
import places
import transporter_list as tl

SESSIONS_PATH = Path(__file__).parent.parent / "data" / "processed" / "admin_sessions.json"
BACKUP_DIR = tl.LIST_PATH.parent / "backups"
_lock = threading.RLock()

HELP = (
    "🛠 *Transporter list — commands*\n\n"
    "• *add transporter Name, Mobile, City, Routes, Big/Small* — all in one message\n"
    "  (or just *add transporter* to get a form to fill)\n"
    "• *remove transporter 98XXXXXXXX*\n"
    "• *check 98XXXXXXXX* — is this number on the list?\n"
    "• *transporters* — how many we have\n"
    "• *rate check Mumbai to Hyderabad* — ask transporters their small + big truck rates (no booking)\n"
    "• *rates Mumbai to Hyderabad* — rates collected so far on a route\n"
    "• Or send me the updated *TRANSPORTER_LIST.xlsx* file and I'll start using it\n\n"
    "Type *cancel* any time to stop."
)

def _load():
    try:
        return json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    
def _save(d):
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def _set(phone, s):
    with _lock:
        d = _load()
        if s is None:
            d.pop(phone, None)
        else:
            d[phone] = s
        _save(d)

def in_progress(phone) -> bool:
    return phone in _load()

def _mobile(text):
    d = "".join(c for c in text or "" if c.isdigit())
    return tl._mobile(d)

def _find_phone(mobile):
    for t in tl.load():
        if t["phone"] == mobile or mobile in t["alt_phones"]:
            return t
    return None

ADMINS_PATH = tl.LIST_PATH.parent / "admins.txt"

def is_admin(phone) -> bool:
    import dispatcher
    if dispatcher.is_ops(phone):
        return True
    if not ADMINS_PATH.exists():
        return False
    wire = "".join(c for c in str(phone) if c.isdigit())[-10:]
    for line in ADMINS_PATH.read_text(encoding="utf-8").splitlines():
        m = tl._mobile(tl._digits(line.split("#")[0]))
        if m and m == wire:
            return True
    return False

FORM_TIMEOUT_MIN = 10

def _stale(session) -> bool:
    try:
        return datetime.now() - datetime.fromisoformat(session.get("at", "")) > timedelta(minutes=FORM_TIMEOUT_MIN)
    except ValueError:
        return True

def _looks_like_bid(phone, text) -> bool:
    import auction
    if not auction.open_invites_for(phone):
        return False
    if re.search(r"(?i)\b(name|mobile|based|type|routes?|vehicle|contact)\s*:", text):
        return False
    amount, _ = auction.parse_amount(text)
    letters = re.sub(r"(?i)\b(rs|inr|k|lakh|lac|l|thousand|only|small|big|for)\b|[^a-z]", "", text.lower())
    return amount is not None and not letters

def cancel_form(phone):
    if phone in _load():
        _set(phone, None)

def _r(reply, buttons=None):
    return {"reply": reply, "buttons": buttons}

FORM = (
    "Copy this, fill it in and send it back 👇\n\n"
    "Name: \nMobile: \nBased in: \nType: Local / Pan-India / Both\n"
    "Routes: \nVehicle: Big / Small / All\nContact: \n\n"
    "Or in one line, e.g.:\n"
    "*add transporter Sri Balaji Roadways, 9800000001, Hyderabad, Mumbai Pune Chennai, Big*"
)

def handle(phone, text):
    raw = (text or "").strip()
    t = " ".join(raw.split())
    low = t.lower()
    session = _load().get(phone)

    if session and _stale(session):
        _set(phone, None)
        session = None
    if session and _looks_like_bid(phone, raw):
        _set(phone, None)
        return None
    if session:
        if low in ("cancel", "stop", "exit"):
            _set(phone, None)
            return _r("Okay, cancelled — nothing was saved.")
        _set(phone, None)
        return _add(phone, raw, session.get("data") or {})

    if re.fullmatch(r"(transporter|admin)\s*help|help\s*transporters?", low):
        return _r(HELP)
    m = re.match(r"(?:add|new|save)\s+(?:a\s+|new\s+)*(?:transporters?|numbers?|no\.?|contacts?|vendors?)\b[\s:,-]*",
                 raw, re.I)
    if m:
        rest = raw[m.end():].strip()
        if not rest:
            _set(phone, {"step": "form", "data": {}, "at": datetime.now().isoformat(timespec="seconds")})
            return _r(FORM)
        return _add(phone, rest, {})
    if re.search(r"^\s*name\s*:", raw, re.I | re.M) and re.search(r"^\s*(mobile|phone)\s*:", raw, re.I | re.M):
        return _add(phone, raw, {})
    m = re.fullmatch(r"(?:remove|delete)\s+(?:a\s+|the\s+)*(?:transporters?|numbers?|no\.?|contacts?)?\s*(.*)", low)
    if m and not _mobile(m.group(1)) and not m.group(1).strip():
        m = None
    if m:
        return _remove(phone, m.group(1))
    m = re.fullmatch(r"check\s+(.+)", low)
    if m and _mobile(m.group(1)):
        tr = _find_phone(_mobile(m.group(1)))
        return _r(f"✅ On the list: *{tr['name']}* — {tl.describe(tr)}" if tr
                  else "❌ That number is not on the transporter list. Send *add transporter* to add it.")
    if re.fullmatch(r"(list\s+)?transporters?(\s+list)?", low):
        return _summary()
    m = re.fullmatch(r"(?:rate\s*(?:check|enquiry|inquiry)|check\s*rates?)\s*(.*)", low)
    if m:
        return _rate_check(phone, m.group(1))
    m = re.fullmatch(r"rates?\s+(?:for\s+)?(.+)", low)
    if m and " to " in f" {m.group(1)} ":
        return _rates(m.group(1))
    return None

def _route_from(text, default_size="Both"):
    from requirement_parser import parse_message
    import locations
    r = parse_message(text)
    if not (r.get("pickup") and r.get("destination")):
        parts = re.split(r"\s+to\s+", text, maxsplit=1)
        if len(parts) == 2:
            p, d = tl.hub(parts[0]), tl.hub(re.sub(r"\b(big|small|any|all|vehicle|truck|trailer)\b", "", parts[1]))
            if p and d:
                r["pickup"], r["destination"] = places.place_display(p), places.place_display(d)
    if not (r.get("pickup") and r.get("destination")):
        return None
    low = text.lower()
    has_small = bool(re.search(r"small|tempo|lcv", low))
    has_big = bool(re.search(r"big|trailer|truck", low))
    size = "Both" if has_small == has_big else ("Small" if has_small else "Big")
    if not (has_small or has_big):
        size = default_size
    return {"pickup": r["pickup"], "destination": r["destination"], "vehicle_size": size,
            "weight_tons": None, "vehicle_type": None, "required_date": None,
            "distance_km": locations.resolve_distance(r["destination"], r["pickup"])}

def _rate_check(phone, text):
    import auction
    req = _route_from(text)
    if not req:
        return _r("Send it like this:\n*rate check Mumbai to Hyderabad*\n"
                  "(add *small* or *big* to ask for only one size)\n\n"
                  "I'll ask our transporters for their rate on that route (it's clearly marked as an "
                  "enquiry, not a booking) and send you all the rates.")
    s = auction.start_enquiry(req, requester_phone=phone)
    asked = s["invited"]
    what = "small and big vehicle" if req["vehicle_size"] == "Both" else f"{req['vehicle_size'].lower()} vehicle"
    lines = [f"📋 Rate check started: *{req['pickup']} → {req['destination']}* ({what})",
             f"Asked {len(asked)} transporter{'s' if len(asked) != 1 else ''}. I'll send you all the rates in "
             f"{auction.dur(auction.ENQUIRY_MINUTES)}, or sooner if everyone replies."]
    if s["failed"]:
        lines += ["", f"⚠️ Couldn't message {len(s['failed'])} (no chat with the bot in the last 24 h"
                  + ("" if auction.ENQ_TEMPLATE else "; set up the rate_enquiry template to reach them")
                  + "): " + ", ".join(s["failed"])]
    return _r("\n".join(lines))

def _rates(text):
    import auction, statistics
    req = _route_from(text)
    if not req:
        return _r("Send it like this: *rates Mumbai to Hyderabad*")
    lines = [f"📊 *{req['pickup']} → {req['destination']}*"]
    sizes = ["Small", "Big"] if req["vehicle_size"] == "Both" else [req["vehicle_size"]]
    anything = False
    for size in sizes:
        paid, quoted = auction.route_history(req["pickup"], req["destination"], size)
        label = {"Small": "🛻 *Small vehicle*", "Big": "🚛 *Big vehicle*"}.get(size, size)
        lines += ["", label]
        if not paid and not quoted:
            lines.append("No rates collected yet.")
            continue
        anything = True
        if paid:
            lines.append(f"Loads awarded: {len(paid)} · last {auction.inr(paid[-1])} · "
                         f"median {auction.inr(statistics.median(paid))}")
        if quoted:
            lines.append(f"Rates collected: {len(quoted)} · lowest {auction.inr(min(quoted))} · "
                         f"middle {auction.inr(statistics.median(quoted))} · highest {auction.inr(max(quoted))}")
        base = statistics.median(paid[-5:]) if paid else (
            statistics.median(quoted[-20:]) if len(quoted) >= 3 else None)
        lines.append(f"Base price: *{auction.inr(base)}*" if base else "Base price: needs at least 3 rates")
    if not anything:
        lines += ["", f"Send *rate check {req['pickup']} to {req['destination']}* to ask transporters."]
    return _r("\n".join(lines))

def _summary():
    rows = tl.load()
    pan = sum(1 for t in rows if t["pan_india"])
    no_phone = [t["name"] for t in rows if not t["phone"]]
    added = tl.read_added()
    lines = [f"📋 *{len(rows)} transporters* on the list ({pan} pan-India, {len(rows) - pan} local/route)."]
    if added:
        lines += ["", "Added on WhatsApp:"] + [f"• {r['name']} – {r['phone']}" for r in added[-10:]]
    if no_phone:
        lines += ["", "⚠️ No valid mobile (can't get bid alerts): " + ", ".join(no_phone)]
    lines += ["", "Send *add transporter* to add one, or *transporter help*."]
    return _r("\n".join(lines))

_KEYS = {
    "name": "name", "transporter": "name", "company": "name",
    "mobile": "phone", "phone": "phone", "number": "phone", "mobile no": "phone", "whatsapp": "phone",
    "contact": "contact", "contact person": "contact", "person": "contact",
    "based in": "base", "base": "base", "city": "base", "from": "base", "location": "base",
    "type": "type", "routes": "dests", "route": "dests", "runs to": "dests", "destinations": "dests",
    "destination": "dests", "vehicle": "size", "size": "size", "vehicle size": "size",
}
_PLACEHOLDER = re.compile(r"^(local\s*/\s*pan-india\s*/\s*both|big\s*/\s*small\s*/\s*all)$", re.I)

def _kind(v):
    v = v.lower()
    if "both" in v or ("local" in v and ("pan" in v or "all india" in v)):
        return "Local + Pan-India"
    if "pan" in v or "all india" in v or "anywhere" in v:
        return "Pan-India"
    if "local" in v or "route" in v:
        return "Local"
    return None

def _size(v):
    v = v.lower()
    small = bool(re.search(r"\b(small|tempo|lcv|pickup)\b", v))
    big = bool(re.search(r"\b(big|trailer|truck|lorry)\b", v))
    if re.search(r"\b(all|any|both)\b", v) or (small and big):
        return "Any"
    if big:
        return "Big"
    if small:
        return "Small"
    return None

def _places(v):
    keys, bad = [], []
    for p in (x.strip() for x in re.split(r"[,/;\n]|\band\b|\s{2,}", v) if x.strip()):
        k = tl.hub(p)
        if k:
            keys.append(places.place_display(k))
        else:
            words = p.split()
            ks = [tl.hub(w) for w in words]
            if len(words) > 1 and all(ks):
                keys += [places.place_display(k) for k in ks]
            else:
                bad.append(p)
    return list(dict.fromkeys(keys)), bad

def _parse(text, data):
    lines = [l for l in text.splitlines() if l.strip()]
    form = [l for l in lines if re.match(r"\s*[A-Za-z ]{3,20}\s*[:=-]", l)
            and _KEYS.get(re.split(r"[:=-]", l, 1)[0].strip().lower())]
    bad = []
    if form:
        for l in form:
            k, v = re.split(r"[:=-]", l, 1)
            field, v = _KEYS[k.strip().lower()], v.strip()
            if not v or _PLACEHOLDER.match(v):
                continue
            if field == "phone":
                data["phone"] = _mobile(v) or data.get("phone")
                if not _mobile(v):
                    bad.append(f"mobile '{v}'")
            elif field == "base":
                ks = tl._added_bases(v)
                data["base"] = ", ".join(places.place_display(k) for k in ks) if ks else data.get("base")
                if not ks:
                    bad.append(f"city '{v}'")
            elif field == "type":
                data["type"] = _kind(v) or data.get("type")
            elif field == "size":
                data["size"] = _size(v) or data.get("size")
            elif field == "dests":
                ks, b = _places(v)
                if _kind(v) and not ks:
                    data["type"] = data.get("type") or _kind(v)
                data["destinations"] = ", ".join(ks)
                bad += [f"route '{x}'" for x in b if not _kind(x)]
            else:
                data[field] = v.title() if v.isupper() or v.islower() else v
        return bad

    text = " ".join(text.split())
    m = re.search(r"(?:\+?91[\s-]?)?[6-9]\d(?:[\s-]?\d){8}", text)
    if m:
        data["phone"] = _mobile(m.group(0))
        text = (text[:m.start()] + "," + text[m.end():])
    chunks = [c.strip() for c in re.split(r"[,;\n]", text) if c.strip()]
    rest_places, unknown = [], []
    for c in chunks:
        low = c.lower()
        fm = re.match(r"(?:based\s+(?:in|at)|from)\s+(.+)", low)
        if fm and tl.hub(fm.group(1)):
            data["base"] = places.place_display(tl.hub(fm.group(1)))
        elif _kind(c) and not tl.hub(c):
            data["type"] = _kind(c)
        elif _size(c) and not tl.hub(c) and len(c.split()) <= 3:
            data["size"] = _size(c)
        else:
            ks, b = _places(c)
            if ks and not b:
                rest_places += ks
            else:
                unknown.append(c)
    if unknown and not data.get("name"):
        n = unknown.pop(0)
        data["name"] = n.title() if n.isupper() or n.islower() else n
    if unknown and not data.get("contact"):
        data["contact"] = unknown.pop(0).title()
    if rest_places and not data.get("base"):
        data["base"] = rest_places.pop(0)
    if rest_places:
        data["destinations"] = ", ".join(dict.fromkeys(rest_places))
    return [f"'{u}'" for u in unknown]

def _add(phone, text, data):
    data = dict(data)
    bad = _parse(text, data)
    missing = [label for key, label in (("name", "Name"), ("phone", "Mobile"), ("base", "Based in"))
               if not data.get(key)]
    if data.get("phone"):
        dup = _find_phone(data["phone"])
        if dup:
            return _r(f"{data['phone']} is already on the list as *{dup['name']}*. Nothing was added.")
    if missing:
        _set(phone, {"step": "form", "data": data, "at": datetime.now().isoformat(timespec="seconds")})
        got = _card(data) if data else ""
        return _r((f"Got it so far:\n{got}\n\n" if got else "")
                  + "Still need: *" + ", ".join(missing) + "*\n"
                  + "Send just those, e.g.\n" + "\n".join(f"{m}: " for m in missing)
                  + (f"\n\n(I couldn't use: {', '.join(bad)})" if bad else ""))
    data.setdefault("contact", "")
    data.setdefault("destinations", "")
    data["type"] = data.get("type") or ("Local" if data["destinations"] else "Pan-India")
    data["size"] = data.get("size") or "Any"
    _append(data, phone)
    return _r("✅ Added — gets bid alerts from the next load:\n\n" + _card(data)
              + (f"\n\n(I couldn't use: {', '.join(bad)})" if bad else "")
              + f"\n\nWrong? Send *remove transporter {data['phone']}* and add again.")

def _card(d):
    lines = []
    if d.get("name"):
        lines.append(f"🚛 *{d['name']}*")
    if d.get("phone"):
        lines.append(f"📱 {d['phone']}")
    if d.get("contact"):
        lines.append(f"👤 {d['contact']}")
    if d.get("base"):
        lines.append(f"📍 Based in {d['base']}" + (f" — {d['type']}" if d.get("type") else ""))
    if d.get("destinations"):
        lines.append(f"🛣 Runs to: {d['destinations']}")
    if d.get("size"):
        lines.append(f"🚚 Vehicle: {d['size']}")
    return "\n".join(lines)

def _append(d, by):
    new = not tl.ADDED_PATH.exists()
    tl.ADDED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(tl.ADDED_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=tl.ADDED_COLS)
        if new:
            w.writeheader()
        w.writerow({**{k: d.get(k, "") for k in tl.ADDED_COLS}, "added_by": by,
                    "added_at": datetime.now().isoformat(timespec="seconds")})
    removed = tl.read_removed()
    if d["phone"] in removed:
        _write_removed(removed - {d["phone"]})

def _write_removed(phones):
    with open(tl.REMOVED_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["phone"])
        for p in sorted(phones):
            w.writerow([p])

def _remove(phone, arg):
    mob = _mobile(arg)
    if not mob:
        return _r("Send it like this: *remove transporter 98XXXXXXXX*")
    t = _find_phone(mob)
    if not t:
        return _r(f"{mob} is not on the transporter list.")
    added = tl.read_added()
    keep = [r for r in added if tl._mobile(tl._digits(r.get("phone"))) != mob]
    if len(keep) != len(added):
        with open(tl.ADDED_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=tl.ADDED_COLS)
            w.writeheader()
            w.writerows(keep)
    if _find_phone(mob):
        _write_removed(tl.read_removed() | {t["phone"]})
    return _r(f"🗑 *{t['name']}* ({mob}) removed — they won't get bid alerts any more.\n"
              f"(Changed your mind? Send *add transporter* again.)")

def handle_document(phone, filename, content: bytes):
    if not (filename or "").lower().endswith(".xlsx"):
        return _r("Please send the transporter list as an Excel file (*.xlsx*).")
    tmp = tl.LIST_PATH.with_suffix(".upload.xlsx")
    tmp.write_bytes(content)
    try:
        rows = tl._parse(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return _r(f"❌ I couldn't read that file ({e}). The old list is still in use.\n"
                  f"Keep the same columns: SR NO, TRANSPORTER, NAME, MOBILE NO, VEHICLE, DESTINATION.")
    usable = [t for t in rows if t["phone"]]
    if len(usable) < 3:
        tmp.unlink(missing_ok=True)
        return _r(f"❌ That file only has {len(usable)} transporters with a mobile number, so I did "
                  f"NOT use it (to be safe). The old list is still in use.")
    old = tl.load()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if tl.LIST_PATH.exists():
        shutil.copy2(tl.LIST_PATH, BACKUP_DIR / f"TRANSPORTER_LIST_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    tmp.replace(tl.LIST_PATH)
    tl.REMOVED_PATH.unlink(missing_ok=True)
    new = tl.load()
    old_ph, new_ph = {t["phone"] for t in old}, {t["phone"] for t in new}
    lines = [f"✅ New transporter list saved: *{len(new)} transporters* (was {len(old)}).",
             f"New numbers: {len(new_ph - old_ph)} · no longer on list: {len(old_ph - new_ph)}"]
    problems = [f"• {t['name']}: {p}" for t in rows for p in t["problems"]]
    if problems:
        lines += ["", "⚠️ Please fix in the sheet:"] + problems[:10]
    lines += ["", "The old file is kept as a backup."]
    return _r("\n".join(lines))
