import csv
import json
import os
import re
import statistics
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import places
import transporter_list as tl
import translation
from whatsapp_client import send_text, send_buttons, send_template, window_open, to_wire_number

PER_POOL = int(os.environ.get("AUCTION_PER_POOL", "5"))
TARGET_INVITES = int(os.environ.get("AUCTION_TARGET_INVITES", str(PER_POOL * 3)))
ROUND1_MINUTES = int(os.environ.get("AUCTION_ROUND1_MINUTES", "30"))
ROUND2_MINUTES = int(os.environ.get("AUCTION_ROUND2_MINUTES", "10"))
TWO_ROUNDS = os.environ.get("AUCTION_TWO_ROUNDS", "0") == "1"
REVEAL_LOWEST = os.environ.get("AUCTION_REVEAL_LOWEST", "1") != "0"
AUTO_AWARD = os.environ.get("AUCTION_AUTO_AWARD", "1") != "0"
TEST_ONLY = os.environ.get("AUCTION_TEST_ONLY", "0") == "1"
ONLY_NEW = os.environ.get("AUCTION_ONLY_NEW", "0") == "1"
MATERIAL = os.environ.get("AUCTION_MATERIAL", "Timber (sawn wood)")
ENQUIRY_MINUTES = int(os.environ.get("AUCTION_ENQUIRY_MINUTES", "120"))
ENQ_TEMPLATE = os.environ.get("WA_TEMPLATE_ENQUIRY", "").strip()
ENQ_TEMPLATE_PARAMS = [x.strip() for x in os.environ.get(
    "WA_TEMPLATE_ENQUIRY_PARAMS", "pickup,destination,size").split(",") if x.strip()]
ENQ_TEMPLATE_BUTTONS = int(os.environ.get("WA_TEMPLATE_ENQUIRY_BUTTONS", "2") or 0)
MIN_BID = int(os.environ.get("AUCTION_MIN_BID", "1000"))
MAX_BID = int(os.environ.get("AUCTION_MAX_BID", "1000000"))
LOW_OUTLIER = 0.60
ABOVE_PAST = 1.25

BID_TEMPLATE = os.environ.get("WA_TEMPLATE_BID", os.environ.get("WA_TEMPLATE_OFFER", "load_offer")).strip()
BID_TEMPLATE_PARAMS = [x.strip() for x in os.environ.get(
    "WA_TEMPLATE_BID_PARAMS", os.environ.get("WA_TEMPLATE_OFFER_PARAMS", "pickup,destination,tons,ref")
).split(",") if x.strip()]
BID_TEMPLATE_BUTTONS = int(os.environ.get("WA_TEMPLATE_BID_BUTTONS",
                                          os.environ.get("WA_TEMPLATE_OFFER_BUTTONS", "2")) or 0)

_DATA = Path(__file__).parent.parent / "data" / "processed"
STATE_PATH = _DATA / "auctions.json"
LOG_PATH = _DATA / "auction_log.csv"
_lock = threading.RLock()

OPEN = ("round1", "round2")


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    _DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_PATH)


def get(ref):
    with _lock:
        return _load().get(ref)


def _now():
    return datetime.now()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def inr(n) -> str:
    s = str(int(round(n)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + parts) + "," + tail
    return "₹" + s


def dur(minutes) -> str:
    m = max(1, int(minutes))
    h, m = divmod(m, 60)
    if not h:
        return f"{m} min"
    return (f"{h} hour" if h == 1 else f"{h} hours") if not m else f"{h} hr {m} min"


def _tomorrow() -> str:
    return (_now() + timedelta(days=1)).strftime("%d-%b-%Y")


def short(ref) -> str:
    return ref.split("-")[-1]


def _route(req):
    return f"{req.get('pickup')} → {req.get('destination')}"


def _size_text(req):
    return {"Small": "Small vehicle (tempo / LCV, up to ~9 t)",
            "Big": "Big vehicle (truck / trailer, 25 t+)",
            "Both": "Small (tempo / LCV, up to ~9 t) and Big (truck / trailer, 25 t+)",
            "Any": "Any size"}.get(req.get("vehicle_size"), "Any size") + (
        f", {req['weight_tons']:g} tons" if isinstance(req.get("weight_tons"), (int, float)) else "")


def _pretty_phone(p) -> str:
    w = to_wire_number(p)
    return f"+91 {w[2:7]} {w[7:]}" if len(w) == 12 and w.startswith("91") else f"+{w}"


def _tr(text, lang):
    if not lang or lang == "en":
        return text
    return translation.translate_from_english(text, lang)


def _far(t, place_hub):
    d = tl.dist_from(t, place_hub)
    return d if d is not None else 99999


def _size_ok(t, req):
    want = req.get("vehicle_size")
    return want in (None, "", "Any") or t["size"] in ("Any", want)


def select_invitees(request: dict, exclude=(), per_pool=PER_POOL, target=TARGET_INVITES, real=False):
    P, D = tl.hub(request.get("pickup")), tl.hub(request.get("destination"))
    exclude = set(exclude)
    pool_of = {}
    everyone = [t for t in tl.load() if t["phone"] and t["name"] not in exclude]
    cands = [t for t in everyone if not t.get("test")]
    tests = [t for t in everyone if t.get("test")]
    if TEST_ONLY:
        trial = trial_phones()
        if trial or any(t.get("tag") == "new" for t in cands):
            cands = [t for t in cands if t["phone"] in trial or t.get("sr") == "WA" or t.get("tag") == "new"]
        elif not real:
            return [(t, "test") for t in tests]
    elif ONLY_NEW:
        cands = [t for t in cands if t.get("tag") == "new" or t.get("sr") == "WA"]

    def far(a, b):
        d = tl.distance(a, b)
        return d if d is not None else 99999

    route = [t for t in cands if not t["pan_india"] and D and tl.covers(t, D)
             and tl.based_near(t, P)]
    route.sort(key=lambda t: (not _size_ok(t, request), _far(t, P)))
    pan = [t for t in cands if t["pan_india"] and (tl.based_near(t, P) or t["base"] is None)]
    pan.sort(key=lambda t: (not _size_ok(t, request), t["base"] is None, _far(t, P)))
    chosen = [(t, "route") for t in route] + [(t, "pan_india") for t in pan]
    chosen += [(t, "test") for t in tests]
    return chosen


TRIAL_PATH = tl.LIST_PATH.parent / "trial_group.txt"


def trial_phones():
    if not TRIAL_PATH.exists():
        return set()
    out = set()
    for line in TRIAL_PATH.read_text(encoding="utf-8").splitlines():
        m = tl._mobile(tl._digits(line.split("#")[0]))
        if m:
            out.add(m)
    return out


def no_phone_matches(request):
    D = tl.hub(request.get("destination"))
    P = tl.hub(request.get("pickup"))
    return [t for t in tl.load() if not t["phone"]
            and (tl.covers(t, D) or tl.based_near(t, P) or tl.based_near(t, D))]


def _enquiry_text(a):
    req = a["request"]
    lines = [
        "📋 *Rate enquiry from Megamet*",
        "",
        f"From: {req.get('pickup')}",
        f"To: {req.get('destination')}",
        f"Material: {MATERIAL}",
        f"Vehicle: {_size_text(req)}",
    ]
    if req.get("distance_km"):
        lines.append(f"Distance: ~{req['distance_km']} km")
    if req.get("vehicle_size") == "Both":
        lines += ["", "💰 *What are your rates for the full truck on this route?*",
                  "Please send both:", "Small: ", "Big: ",
                  "(If you have only one type, send just that one.)",
                  "", "This is a rate enquiry, not a booking yet."]
    else:
        lines += ["", "💰 *What is your rate for the full truck on this route?*",
                  "This is a rate enquiry, not a booking yet."]
    return "\n".join(lines)


def _invite_text(a, secs_left=None):
    if a.get("kind") == "enquiry":
        return _enquiry_text(a)
    req = a["request"]
    mins = max(1, int((secs_left if secs_left is not None else ROUND1_MINUTES * 60) // 60))
    lines = [
        "🚛 *Bid Alert from Megamet*",
        "",
        f"Order Id: {a['ref']}",
        f"From: {req.get('pickup')}",
        f"To: {req.get('destination')}",
        f"Loading date: {req.get('required_date') or _tomorrow()}",
        f"Material: {MATERIAL}",
        f"Vehicle: {_size_text(req)}",
    ]
    if req.get("distance_km"):
        lines.append(f"Distance: ~{req['distance_km']} km")
    lines += [
        "",
        "💰 *Send your rate for the full truck*",
        f"⏱ Closes in {dur(mins)}." + (" This order has two bidding rounds." if TWO_ROUNDS else ""),
    ]
    return "\n".join(lines)


def _tons_param(req):
    if isinstance(req.get("weight_tons"), (int, float)):
        return f"{req['weight_tons']:g}"
    return {"Small": "Small vehicle, up to 9", "Big": "Big vehicle, 25+"}.get(
        req.get("vehicle_size"), "Any size, approx 9-30")


def _send_invite(a, name, inv):
    ref = a["ref"]
    enquiry = a.get("kind") == "enquiry"
    na_label = "Don't run this route" if enquiry else "Vehicle unavailable"
    if window_open(inv["phone"]):
        body = _tr(_invite_text(a, _secs_left(a)), inv.get("language"))
        send_buttons(inv["phone"], body, [(f"BID|{ref}|YES", _tr("Share my rate", inv.get("language"))),
                                          (f"BID|{ref}|NA", _tr(na_label, inv.get("language")))])
        return "session"
    req = a["request"]
    if enquiry:
        if not ENQ_TEMPLATE:
            raise RuntimeError("no chat in last 24h and no WA_TEMPLATE_ENQUIRY set")
        ctx = {"pickup": req.get("pickup"), "destination": req.get("destination"),
               "route": _route(req), "size": _size_text(req), "material": MATERIAL}
        send_template(inv["phone"], ENQ_TEMPLATE, [ctx.get(p, "-") for p in ENQ_TEMPLATE_PARAMS],
                      button_payloads=[f"BID|{ref}|YES", f"BID|{ref}|NA"][:ENQ_TEMPLATE_BUTTONS])
        return "template"
    ctx = {"pickup": req.get("pickup"), "destination": req.get("destination"),
           "route": _route(req), "tons": _tons_param(req), "weight": _size_text(req),
           "size": _size_text(req), "date": req.get("required_date") or _tomorrow(),
           "ref": ref, "material": MATERIAL}
    send_template(inv["phone"], BID_TEMPLATE, [ctx.get(p, "-") for p in BID_TEMPLATE_PARAMS],
                  button_payloads=[f"BID|{ref}|YES", f"BID|{ref}|NA"][:BID_TEMPLATE_BUTTONS])
    return "template"


def _say(inv, text, buttons=None):
    if not window_open(inv["phone"]):
        return False
    text = _tr(text, inv.get("language"))
    try:
        if buttons:
            send_buttons(inv["phone"], text, buttons)
        else:
            send_text(inv["phone"], text)
        return True
    except Exception as e:
        print(f"[auction] could not message {inv['phone']}: {e}")
        return False


def new_ref():
    return f"SHP-{_now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def start_enquiry(request: dict, requester_phone=None, requester_phone_id=None, requester_name=None):
    return start_auction(request, requester_phone, requester_phone_id, requester_name, kind="enquiry")


def start_auction(request: dict, requester_phone=None, requester_phone_id=None, requester_name=None,
                  kind="load"):
    ref = new_ref() if kind == "load" else f"RC-{_now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    chosen = select_invitees(request, real=(kind == "enquiry"))
    now = _now()
    minutes = ENQUIRY_MINUTES if kind == "enquiry" else ROUND1_MINUTES
    a = {
        "ref": ref,
        "kind": kind,
        "request": dict(request),
        "requester_phone": to_wire_number(requester_phone) if requester_phone else None,
        "requester_phone_id": requester_phone_id,
        "requester_name": requester_name,
        "created_at": _iso(now),
        "status": "round1",
        "round": 1,
        "round_ends_at": _iso(now + timedelta(minutes=minutes)),
        "extended": False,
        "invitees": {},
        "winner": None,
        "flags": [],
    }
    failed = []
    with _lock:
        for t, pool in chosen:
            inv = {"phone": to_wire_number(t["phone"]), "pool": pool, "language": t.get("language", "en"),
                   "status": "invited", "bids": [], "r2": None, "via": None}
            try:
                inv["via"] = _send_invite(a, t["name"], inv)
            except Exception as e:
                print(f"[auction] invite to {t['name']} failed: {e}")
                inv["status"] = "failed"
                inv["error"] = str(e)[:200]
                failed.append(t["name"])
            a["invitees"][t["name"]] = inv
        data = _load()
        data[ref] = a
        _save(data)
    return {
        "ref": ref,
        "auction": a,
        "invited": [n for n, i in a["invitees"].items() if i["status"] != "failed"],
        "pools": {p: [n for n, i in a["invitees"].items() if i["pool"] == p]
                  for p in ("route", "pan_india", "pickup_local", "destination_local", "extra", "test")},
        "failed": failed,
        "no_phone": [t["name"] for t in no_phone_matches(request)],
    }


def _secs_left(a):
    try:
        return max(0, (datetime.fromisoformat(a["round_ends_at"]) - _now()).total_seconds())
    except (KeyError, ValueError):
        return 0


_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
_NA_WORDS = ("vehicle unavailable", "not available", "unavailable", "no vehicle", "no gadi",
             "gadi nahi", "gaadi nahi", "withdraw", "cancel", "don t run", "dont run", "do not run",
             "not running", "don t go", "dont go", "nahi", "nahin", "nai", "decline", "no", "na", "pass",
             "नहीं", "नही", "ना", "गाडी नाही", "नाही")
_SAME_WORDS = ("same", "hold", "keep", "final", "no change", "wahi", "vahi", "same rate")
_PER_UNIT = re.compile(r"\b(per\s*(ton|tonne|mt|kg)|pmt|/\s*(mt|ton))\b", re.I)


def parse_amount(text: str):
    t = (text or "").translate(_DEVANAGARI).lower()
    t = re.sub(r"shp-\d{8}-[0-9a-f]{6}", " ", t)
    if _PER_UNIT.search(t):
        return None, "per_unit"
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|l|lac|lakh|lakhs|lacs)?\b", t)
    if not m:
        return None, None
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2) or ""
    if unit in ("k", "thousand"):
        num *= 1000
    elif unit:
        num *= 100000
    amount = int(round(num))
    if amount < MIN_BID:
        return amount, "too_small"
    if amount > MAX_BID:
        return amount, "too_big"
    return amount, None


_AMOUNT_RE = r"(\d[\d,]*(?:\.\d+)?\s*(?:k|thousand|l|lac|lakh|lakhs|lacs)?)\b"
_SMALL_RE = r"(?:small|chota|chhota|chhoti|choti|tempo|lcv|pickup|pick-up)"
_BIG_RE = r"(?:big|bada|badi|trailer|trailor|truck|lorry|container)"


def _clean_text(text):
    t = (text or "").translate(_DEVANAGARI).lower()
    return re.sub(r"(?:shp|rc)-\d{8}-[0-9a-f]{6}", " ", t)


def parse_two_sizes(text):
    t = _clean_text(text)
    if _PER_UNIT.search(t):
        return {}, None, "per_unit"
    found = {}
    for size, word in (("Small", _SMALL_RE), ("Big", _BIG_RE)):
        m = re.search(word + r"[^\d\n]{0,20}?" + _AMOUNT_RE, t)
        if m:
            amt, prob = parse_amount(m.group(1))
            if prob:
                return {}, amt, prob
            found[size] = amt
    if found:
        return found, None, None
    amts = []
    for m in re.finditer(_AMOUNT_RE, t):
        amt, prob = parse_amount(m.group(1))
        if amt is not None:
            if prob:
                return {}, amt, prob
            amts.append(amt)
    if len(amts) >= 2:
        lo, hi = sorted(amts[:2])
        return {"Small": lo, "Big": hi}, None, None
    if len(amts) == 1:
        return {}, amts[0], None
    return {}, None, None


def _is_word(text, words):
    t = " ".join(re.sub(r"[^\w\sऀ-ॿ]", " ", (text or "").lower()).split())
    return any(t == w or t.startswith(w + " ") for w in words)


def open_invites_for(phone):
    wire = to_wire_number(phone)
    with _lock:
        data = _load()
    out = []
    for ref, a in data.items():
        if a.get("status") not in OPEN:
            continue
        for name, inv in a["invitees"].items():
            if inv["phone"] == wire and inv["status"] != "failed":
                out.append((a["created_at"], ref, name))
    return [(r, n) for _, r, n in sorted(out, reverse=True)]


def handle_button(phone, payload) -> bool:
    parts = payload.split("|")
    if len(parts) != 3 or parts[0] != "BID":
        return False
    _, ref, what = parts
    wire = to_wire_number(phone)
    with _lock:
        data = _load()
        a = data.get(ref)
        hit = [(n, i) for n, i in (a or {}).get("invitees", {}).items() if i["phone"] == wire]
        if not a or not hit:
            send_text(phone, "This load is no longer open for bidding. Thank you!")
            return True
        name, inv = hit[0]
        if a["status"] not in OPEN:
            _say(inv, f"Bidding for {ref} is closed. Thank you — we'll send you the next load.")
            return True
        if what == "NA":
            _mark_na(a, name, inv)
        elif what[:2] in ("S:", "B:") and what[2:].isdigit():
            _record_enquiry(a, inv, {"Small" if what[0] == "S" else "Big": int(what[2:])})
        else:
            inv["status"] = "interested" if inv["status"] == "invited" else inv["status"]
            na_label = "Don't run this route" if a.get("kind") == "enquiry" else "Vehicle unavailable"
            ask = ("✍️ Please type your rates for the full truck (in ₹) and send them here:\nSmall: \nBig: "
                   if a["request"].get("vehicle_size") == "Both" else
                   "✍️ Please type your rate for the full truck (in ₹) and send it here.")
            if inv.get("via") == "template":
                _say(inv, _invite_text(a, _secs_left(a)) + "\n\n" + ask, [(f"BID|{ref}|NA", na_label)])
            else:
                _say(inv, ask)
        data[ref] = a
        _save(data)
    _close_round_if_everyone_answered(ref)
    return True


def _mark_na(a, name, inv):
    if a.get("kind") == "enquiry":
        if inv["status"] != "not_available":
            inv["status"] = "not_available"
            _say(inv, "Okay, noted. Thank you!")
        return
    if a["status"] == "round2" and inv["bids"]:
        inv["r2"] = "withdrawn"
        inv["status"] = "withdrawn"
        _say(inv, f"Noted — your bid for {a['ref']} is withdrawn. Thanks!")
    elif inv["status"] == "not_available":
        return
    else:
        inv["status"] = "not_available"
        _say(inv, f"No problem, thanks for letting us know about {a['ref']}! "
                  f"We'll send you the next load. 🙏")


def handle_text(phone, text, customer_busy=False):
    invites = open_invites_for(phone)
    if not invites:
        return _late_reply(phone, text)

    upper = (text or "").upper()
    picked = [(r, n) for r, n in invites if short(r) in upper or r in upper]
    if not picked:
        if len(invites) > 1:
            amount, _ = parse_amount(text)
            if amount is not None or _is_word(text, _NA_WORDS + _SAME_WORDS):
                lines = ["You have more than one load open for bidding. Please add the Order code:", ""]
                for r, _ in invites[:5]:
                    a = get(r)
                    lines.append(f"• {short(r)} – {_route(a['request'])}")
                lines += ["", f"Send your rate followed by the code, like: *<rate> {short(invites[0][0])}*"]
                send_text(phone, "\n".join(lines))
                return True
            return False
        picked = invites[:1]
    ref, name = picked[0]

    english = text
    amount, problem = parse_amount(text)
    if amount is None and problem is None:
        with _lock:
            lang = _load()[ref]["invitees"][name].get("language")
        if lang and lang != "en":
            english = translation.translate_to_english(text)
            amount, problem = parse_amount(english)

    with _lock:
        data = _load()
        a = data[ref]
        inv = a["invitees"][name]
        both = a.get("kind") == "enquiry" and a["request"].get("vehicle_size") == "Both"
        if both:
            found, single, prob2 = parse_two_sizes(english)
            if prob2 == "per_unit":
                _say(inv, "Please send the *total freight for the full truck* in ₹ (not per ton).")
            elif prob2 == "too_small":
                _say(inv, f"Is {inr(single)} the full freight for the whole truck? Please send the full amount in ₹.")
            elif prob2 == "too_big":
                _say(inv, f"{inr(single)} looks too high — please check and send your rate again.")
            elif found:
                _record_enquiry(a, inv, found)
            elif single is not None:
                _say(inv, f"Is {inr(single)} for the small or the big vehicle?",
                     [(f"BID|{ref}|S:{single}", "Small vehicle"), (f"BID|{ref}|B:{single}", "Big vehicle")])
            elif _is_word(english, _NA_WORDS):
                _mark_na(a, name, inv)
            elif customer_busy or re.search(r"\b(need|want|book|require|chahiye)\b", english.lower()):
                return False
            else:
                _say(inv, f"For {_route(a['request'])} please send your rates for the full truck:\n"
                          f"Small: \nBig: ", [(f"BID|{ref}|NA", "Don't run this route")])
            data[ref] = a
            _save(data)
            _close_round_if_everyone_answered(ref)
            return True

        if problem == "per_unit":
            _say(inv, "Please send the *total freight for the full truck* in ₹ (not per ton).")
        elif problem == "too_small":
            _say(inv, f"Is {inr(amount)} the full freight for the whole vehicle? "
                      f"Please send the full amount in ₹.")
        elif problem == "too_big":
            _say(inv, f"{inr(amount)} looks too high — please check and send your rate again.")
        elif amount is not None:
            _record_bid(a, name, inv, amount)
        elif _is_word(english, _SAME_WORDS) and a["status"] == "round2" and inv["bids"]:
            inv["r2"] = "same"
            _say(inv, f"Okay — your bid of {inr(_final_bid(inv))} for {ref} stays. "
                      f"We'll share the result soon. 👍")
        elif _is_word(english, _NA_WORDS):
            _mark_na(a, name, inv)
        elif re.fullmatch(r"(hi+|hey+|hello+|hlo|helo|namaste|namaskar|ram ram|good\s*(morning|afternoon|evening)|gm)[\s!.,🙏👋]*",
                          english.strip().lower()):
            if inv["bids"]:
                _say(inv, f"Hello! 👋 Your rate for {_route(a['request'])} (Order {ref}) is "
                          f"{inr(_final_bid(inv))}. You can send a lower rate until bidding closes.")
            else:
                _say(inv, f"Hello! 👋 We have a load waiting for your rate:\n"
                          f"{_route(a['request'])} · {_size_text(a['request'])} (Order {ref})\n\n"
                          f"Please send your rate for the full truck, or tap *Vehicle unavailable*.",
                     [(f"BID|{ref}|YES", "Share my rate"), (f"BID|{ref}|NA", "Vehicle unavailable")])
        else:
            if customer_busy or re.search(r"\b(need|want|book|require|chahiye)\b", english.lower()):
                return False
            if a["status"] == "round2" and inv["bids"]:
                _say(inv, f"For {ref} ({_route(a['request'])}) your bid is {inr(_final_bid(inv))}. "
                          f"Send a *lower* rate, reply *SAME* to keep it, or tap *Withdraw bid*.",
                     [(f"BID|{ref}|NA", "Withdraw bid")])
            else:
                _say(inv, f"For {ref} ({_route(a['request'])}) please reply with your rate in ₹ "
                          f"for the full truck — or tap *Vehicle unavailable*.",
                     [(f"BID|{ref}|NA", "Vehicle unavailable")])
        data[ref] = a
        _save(data)
    _close_round_if_everyone_answered(ref)
    return True


def _late_reply(phone, text):
    amount, problem = parse_amount(text)
    if amount is None or problem == "per_unit":
        return False
    if _recent_win(phone):
        return False
    wire = to_wire_number(phone)
    cutoff = _now() - timedelta(hours=6)
    with _lock:
        data = _load()
    recent = []
    for ref, a in data.items():
        if a.get("status") in OPEN:
            continue
        for name, inv in a["invitees"].items():
            if inv["phone"] == wire and datetime.fromisoformat(a["created_at"]) >= cutoff:
                recent.append((a["created_at"], ref, a))
    if not recent:
        return False
    _, ref, a = max(recent)
    if a.get("kind") == "enquiry":
        send_text(phone, f"Thank you! This rate enquiry ({_route(a['request'])}) has closed. "
                         f"We'll contact you when we have a load on this route.")
    else:
        send_text(phone, f"Thank you! Bidding for Order {ref} ({_route(a['request'])}) has closed. "
                         f"We'll send you the next load. 🙏")
    return True


def _final_bid(inv):
    return min(b["amount"] for b in inv["bids"]) if inv["bids"] else None


def _record_enquiry(a, inv, found):
    sizes = {b.get("size"): b for b in inv["bids"]}
    for size, amt in found.items():
        sizes[size] = {"amount": amt, "at": _iso(_now()), "round": 1, "size": size}
    inv["bids"] = list(sizes.values())
    inv["status"] = "bid"
    req = a["request"]
    if req.get("vehicle_size") == "Both":
        got = {b["size"]: b["amount"] for b in inv["bids"]}
        lines = [f"✅ Thank you! Noted for {_route(req)}:"]
        lines += [f"{s}: *{inr(got[s])}*" for s in ("Small", "Big") if s in got]
        missing = [s for s in ("Small", "Big") if s not in got]
        if missing:
            lines.append(f"If you also have a {missing[0].lower()} vehicle, send that rate too.")
        lines.append("If your rates change, just send the new amount.")
        _say(inv, "\n".join(lines))
    else:
        amt = list(found.values())[0]
        _say(inv, f"✅ Thank you! Noted *{inr(amt)}* for {_route(req)}.\n"
                  f"If your rate changes, just send the new amount.")


def _record_bid(a, name, inv, amount):
    ref = a["ref"]
    if a.get("kind") == "enquiry":
        _record_enquiry(a, inv, {a["request"].get("vehicle_size") or "Any": amount})
        return
    rnd = 2 if a["status"] == "round2" else 1
    prev = _final_bid(inv)
    if rnd == 2 and prev is None:
        _say(inv, f"Round 2 of {ref} is only for transporters who bid in round 1. "
                  f"We'll send you the next load. 🙏")
        return
    if prev is not None and amount >= prev:
        _say(inv, f"Your current rate is {inr(prev)}. Can you do a little lower? "
                  f"If yes, just send the new rate — otherwise {inr(prev)} stays.")
        if rnd == 2:
            inv["r2"] = "same"
        return
    inv["bids"].append({"amount": amount, "at": _iso(_now()), "round": rnd})
    inv["status"] = "bid"
    if rnd == 2:
        inv["r2"] = "lowered"
    mins = max(1, int(_secs_left(a) // 60))
    if prev is None:
        _say(inv, f"✅ Bid received: *{inr(amount)}* for {_route(a['request'])} (Order {ref}).\n\n"
                  f"You can send a lower rate any time in the next {dur(mins)}. "
                  f"We'll tell you the result.")
    else:
        _say(inv, f"✅ Bid updated: *{inr(amount)}* (was {inr(prev)}) for Order {ref}.")


def tick_now(ref):
    with _lock:
        data = _load()
        a = data.get(ref)
        if not a or a["status"] not in OPEN:
            return
        a["round_ends_at"] = _iso(_now())
        data[ref] = a
        _save(data)
    return tick()


def _close_round_if_everyone_answered(ref):
    with _lock:
        data = _load()
        a = data.get(ref)
        if not a or a["status"] not in OPEN:
            return
        if a["status"] == "round1":
            waiting = ("invited", "interested") if a.get("kind") == "enquiry" else \
                ("invited", "interested", "bid")
            pending = [i for i in a["invitees"].values() if i["status"] in waiting]
        else:
            pending = [i for i in a["invitees"].values() if i["bids"] and i["r2"] is None]
        if pending:
            return
        a["round_ends_at"] = _iso(_now())
        data[ref] = a
        _save(data)
    tick()


def tick():
    done = []
    with _lock:
        data = _load()
        for ref, a in list(data.items()):
            if a.get("status") not in OPEN or _secs_left(a) > 0:
                continue
            try:
                if a.get("kind") == "enquiry":
                    _finish_enquiry(a)
                elif a["status"] == "round1":
                    _end_round1(a)
                else:
                    _finalize(a)
            except Exception as e:
                print(f"[auction] tick failed for {ref}: {e}")
                continue
            data[ref] = a
            done.append((ref, a["status"]))
        if done:
            _save(data)
    return done


def _bidders(a):
    b = [(n, i, _final_bid(i)) for n, i in a["invitees"].items() if i["bids"] and i["status"] != "withdrawn"]
    b.sort(key=lambda x: (x[2], min(bb["at"] for bb in x[1]["bids"] if bb["amount"] == x[2])))
    return b


def _end_round1(a):
    bidders = _bidders(a)
    if not bidders:
        if not a["extended"]:
            more = select_invitees(a["request"], exclude=a["invitees"].keys())
            a["extended"] = True
            if more:
                a["round_ends_at"] = _iso(_now() + timedelta(minutes=ROUND1_MINUTES))
                for t, pool in more:
                    inv = {"phone": to_wire_number(t["phone"]), "pool": "second_batch",
                           "language": t.get("language", "en"), "status": "invited",
                           "bids": [], "r2": None, "via": None}
                    try:
                        inv["via"] = _send_invite(a, t["name"], inv)
                    except Exception as e:
                        print(f"[auction] invite to {t['name']} failed: {e}")
                        inv["status"] = "failed"
                    a["invitees"][t["name"]] = inv
                _tell_requester(a, f"No bids yet for {_route(a['request'])} (Order {a['ref']}). "
                                   f"I've invited {len(more)} more transporters and will update you.")
                return
        a["status"] = "no_bids"
        _notify_no_bids(a)
        _log(a)
        return

    if TWO_ROUNDS and len(bidders) >= 2:
        a["status"] = "round2"
        a["round"] = 2
        a["round_ends_at"] = _iso(_now() + timedelta(minutes=ROUND2_MINUTES))
        low = bidders[0][2]
        n = len(bidders)
        for rank, (name, inv, amt) in enumerate(bidders, 1):
            head = f"⏱ *Round 2* — Order {a['ref']} ({_route(a['request'])})\n\n"
            if rank == 1:
                body = (f"Your bid {inr(amt)} is currently the *lowest (L1)* of {n} bids. 👍\n\n"
                        f"Reply *SAME* to hold it, or send a lower rate within {ROUND2_MINUTES} min.")
            else:
                low_txt = f"Lowest bid so far: *{inr(low)}*\n" if REVEAL_LOWEST else ""
                body = (f"{low_txt}Your bid: {inr(amt)} (L{rank} of {n})\n\n"
                        f"Send a *lower* rate within {ROUND2_MINUTES} min to improve your chance, "
                        f"or reply *SAME* to keep {inr(amt)}.")
            _say(inv, head + body, [(f"BID|{a['ref']}|NA", "Withdraw bid")])
        for name, inv in a["invitees"].items():
            if inv["status"] in ("invited", "interested"):
                inv["status"] = "no_response"
        _tell_requester(a, f"Round 1 closed for {_route(a['request'])} (Order {a['ref']}): "
                           f"{n} bids, lowest {inr(low)}. Round 2 is running for {ROUND2_MINUTES} min.",
                        ops_only_detail=True)
        return
    _finalize(a)


def route_history(pickup, destination, size=None):
    if not LOG_PATH.exists():
        return [], []
    P, D = tl.hub(pickup), tl.hub(destination)
    size = size or "Any"
    paid, quoted = [], []
    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("pickup_hub") != P or r.get("dest_hub") != D or not r.get("final_bid"):
                continue
            if (r.get("vehicle_size") or "Any") != size:
                continue
            if r.get("awarded") == "True":
                paid.append(float(r["final_bid"]))
            elif r.get("auction_status") in ("enquiry_done", "cancelled") and r.get("pool") != "test":
                quoted.append(float(r["final_bid"]))
    return paid, quoted


def _past_rate(a):
    paid, quoted = route_history(a["request"].get("pickup"), a["request"].get("destination"),
                                 a["request"].get("vehicle_size"))
    if paid:
        return statistics.median(paid[-5:])
    if len(quoted) >= 3:
        return statistics.median(quoted[-20:])
    return None


def _finalize(a):
    for inv in a["invitees"].values():
        if inv["status"] in ("invited", "interested"):
            inv["status"] = "no_response"
    bidders = _bidders(a)
    if not bidders:
        a["status"] = "no_bids"
        _notify_no_bids(a)
        _log(a)
        return
    l1_name, l1_inv, l1 = bidders[0]
    flags = []
    if l1_inv.get("pool") == "test":
        flags.append("L1 is a TEST number — award a real transporter (L2) or cancel")
    elif TEST_ONLY:
        flags.append("test mode — nothing is awarded automatically. Tap *Cancel load* if this was "
                     "only to collect rates")
    if len(bidders) == 1:
        flags.append("only 1 bid received")
    others = [b[2] for b in bidders[1:]]
    if len(others) >= 2 and l1 < LOW_OUTLIER * statistics.median(others):
        flags.append(f"L1 {inr(l1)} is far below the other bids (median {inr(statistics.median(others))}) — "
                     f"possible typo")
    past = _past_rate(a)
    if past and l1 > ABOVE_PAST * past:
        flags.append(f"L1 {inr(l1)} is well above the base price on this route ({inr(past)})")
    a["flags"] = flags
    a["past_rate"] = past
    if AUTO_AWARD and not flags:
        _award(a, l1_name, by="auto")
    else:
        a["status"] = "awaiting_approval"
        _ask_approval(a, bidders)


def _finish_enquiry(a):
    import dispatcher
    for inv in a["invitees"].values():
        if inv["status"] in ("invited", "interested"):
            inv["status"] = "no_response"
    a["status"] = "enquiry_done"
    req = a["request"]
    asked = [n for n, i in a["invitees"].items() if i["status"] != "failed"]
    unreachable = [n for n, i in a["invitees"].items() if i["status"] == "failed"]
    na = [n for n, i in a["invitees"].items() if i["status"] == "not_available"]
    responders = [n for n, i in a["invitees"].items() if i["bids"]]
    lines = [f"📊 *Rate check* — {_route(req)}"
             + (f" · ~{req['distance_km']} km" if req.get("distance_km") else ""),
             f"Asked {len(asked)} transporters · {len(responders)} replied with rates", ""]
    sizes = ["Small", "Big"] if req.get("vehicle_size") == "Both" else [req.get("vehicle_size") or "Any"]
    real = []
    for size in sizes:
        quotes = [(n, i, b["amount"]) for n, i in a["invitees"].items() for b in i["bids"]
                  if (b.get("size") or size) == size]
        quotes.sort(key=lambda x: x[2])
        real_s = [q for q in quotes if q[1].get("pool") != "test"]
        real += real_s
        label = {"Small": "🛻 *Small vehicle*", "Big": "🚛 *Big vehicle*"}.get(size, f"*{_size_text(req)}*")
        if real_s:
            amts = [q[2] for q in real_s]
            lines += [f"{label} — {len(real_s)} rate{'s' if len(real_s) != 1 else ''}",
                      f"Lowest *{inr(min(amts))}* · Middle *{inr(statistics.median(amts))}* · "
                      f"Highest {inr(max(amts))}"]
        else:
            lines.append(f"{label} — no rates")
        for n, i, amt in quotes:
            t = tl.by_name(n) or {}
            lines.append(f"• {inr(amt)} – {n}" + (f" ({t['contact']})" if t.get("contact") else "")
                         + (" [test]" if i.get("pool") == "test" else ""))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    quotes = [(n, i, None) for n, i in a["invitees"].items() if i["bids"]]
    if na:
        lines += ["", "Don't run this route: " + ", ".join(na)]
    if unreachable:
        lines += ["", f"⚠️ Couldn't be messaged ({len(unreachable)}) — no chat with the bot in the last "
                      f"24 h" + ("" if ENQ_TEMPLATE else " and no rate-enquiry template set up yet")
                  + ": " + ", ".join(unreachable)]
    if real:
        lines += ["", "These rates are now used as the base price for this route when checking bids."]
    text = "\n".join(lines)
    targets = [a["requester_phone"]] if a.get("requester_phone") else _ops_for(req)
    for p in targets:
        try:
            dispatcher.send_session_or_template(p, text, phone_number_id=a.get("requester_phone_id"),
                                                alert_ctx=dispatcher._ops_alert_ctx(
                                                    a["ref"], req, f"rate check: {len(real)} rates"))
        except Exception as e:
            print(f"[auction] rate-check summary to {p} failed: {e}")
    for n, i, amt in quotes:
        _say(i, f"Thank you for your rate for {_route(req)}. We'll contact you when we have a load "
                f"on this route.")
    _log(a)


def _recent_win(phone):
    wire = to_wire_number(phone)
    cutoff = _now() - timedelta(hours=24)
    with _lock:
        data = _load()
    won = []
    for ref, a in data.items():
        w = a.get("winner")
        if a.get("status") != "awarded" or not w:
            continue
        inv = a["invitees"].get(w["name"]) or {}
        try:
            when = datetime.fromisoformat(w["at"])
        except (KeyError, ValueError):
            continue
        if inv.get("phone") == wire and when >= cutoff:
            won.append((when, ref, a))
    return max(won)[1:] if won else None


def forward_from_winner(phone, text) -> bool:
    import dispatcher
    wire = to_wire_number(phone)
    cutoff = _now() - timedelta(hours=24)
    with _lock:
        data = _load()
    won = []
    for ref, a in data.items():
        w = a.get("winner")
        if a.get("status") != "awarded" or not w:
            continue
        inv = a["invitees"].get(w["name"]) or {}
        try:
            when = datetime.fromisoformat(w["at"])
        except (KeyError, ValueError):
            continue
        if inv.get("phone") == wire and when >= cutoff:
            won.append((when, ref, a))
    if not won:
        return False
    _, ref, a = max(won)
    req, name = a["request"], a["winner"]["name"]
    fwd = f"🚛 *{name}* (Order {ref}, {_route(req)}):\n\n{text}"
    targets = set(_ops_for(req))
    if a.get("requester_phone"):
        targets.add(a["requester_phone"])
    for p in targets:
        try:
            dispatcher.send_session_or_template(p, fwd, phone_number_id=a.get("requester_phone_id"),
                                                alert_ctx=dispatcher._ops_alert_ctx(ref, req, f"message from {name}"))
        except Exception as e:
            print(f"[auction] forward to {p} failed: {e}")
    send_text(phone, "Thank you! 👍 Shared with the Megamet team.")
    return True


def _ranking_lines(a, bidders, limit=8):
    lines = []
    for rank, (name, inv, amt) in enumerate(bidders[:limit], 1):
        t = tl.by_name(name) or {}
        who = name + (f" ({t['contact']})" if t.get("contact") else "")
        lines.append(f"L{rank}  {inr(amt)} – {who}")
    return lines


def _ops_for(req):
    import dispatcher
    pickup_side = dict(req, destination=req.get("pickup"))
    return list(dict.fromkeys(dispatcher.ops_numbers(req) + dispatcher.ops_numbers(pickup_side)))


def _approvers(a):
    import dispatcher
    req_phone = a.get("requester_phone")
    if req_phone and dispatcher.is_ops(req_phone):
        return [req_phone]
    return _ops_for(a["request"])


def _ask_approval(a, bidders):
    lines = [f"🧾 *Bids for Order {a['ref']}* — {_route(a['request'])}", f"{_size_text(a['request'])}", ""]
    lines += _ranking_lines(a, bidders)
    if a.get("past_rate"):
        lines += ["", f"Base price on this route: {inr(a['past_rate'])}"]
    lines += ["", "⚠️ Please check before awarding:"] + [f"• {f}" for f in a["flags"]]
    lines += ["", "Tap a button, or reply *L1*, *L2*, *L3* … to award."]
    buttons = [(f"AWARD|{a['ref']}|{i}", f"Award L{i}") for i in range(1, min(2, len(bidders)) + 1)]
    buttons.append((f"AWARD|{a['ref']}|X", "Cancel load"))
    import dispatcher
    for p in _approvers(a):
        try:
            if window_open(p):
                send_buttons(p, "\n".join(lines), buttons, phone_number_id=a.get("requester_phone_id"))
            else:
                dispatcher.send_session_or_template(
                    p, "\n".join(lines), alert_ctx=dispatcher._ops_alert_ctx(
                        a["ref"], a["request"], f"{len(bidders)} bids, L1 {inr(bidders[0][2])} – needs approval"))
        except Exception as e:
            print(f"[auction] approval request to {p} failed: {e}")


def handle_award_button(phone, payload) -> bool:
    parts = payload.split("|")
    if len(parts) != 3 or parts[0] != "AWARD":
        return False
    return _approve(phone, parts[1], parts[2])


def handle_approval_text(phone, text) -> bool:
    m = re.fullmatch(r"\s*(?:award\s*)?l?\s*(\d)\s*", (text or "").lower())
    cancel = (text or "").strip().lower() in ("cancel", "cancel load")
    if not (m or cancel):
        return False
    wire = to_wire_number(phone)
    with _lock:
        waiting = [r for r, a in _load().items() if a.get("status") == "awaiting_approval"
                   and wire in [to_wire_number(p) for p in _approvers(a)]]
    if len(waiting) != 1:
        return False
    return _approve(phone, waiting[0], "X" if cancel else m.group(1))


def _approve(phone, ref, choice):
    with _lock:
        data = _load()
        a = data.get(ref)
        if not a:
            return False
        if a["status"] != "awaiting_approval":
            send_text(phone, f"Order {ref} is already {a['status'].replace('_', ' ')}"
                             + (f" ({a['winner']['name']}, {inr(a['winner']['amount'])})." if a.get("winner") else "."))
            return True
        if choice == "X":
            a["status"] = "cancelled"
            for n, inv in a["invitees"].items():
                if inv["bids"]:
                    _say(inv, f"Order {ref} ({_route(a['request'])}) has been cancelled. Thanks for bidding! 🙏")
            send_text(phone, f"Order {ref} cancelled. Bidders have been told.")
            import dispatcher
            rp = a.get("requester_phone")
            if rp and rp != to_wire_number(phone) and not dispatcher.is_ops(rp):
                _tell_requester(a, f"Sorry, your request for {_route(a['request'])} (Ref {ref}) "
                                   f"has been cancelled by our transport team. They'll contact you, "
                                   f"or you can book again any time.")
            _log(a)
        else:
            bidders = _bidders(a)
            i = int(choice)
            if not 1 <= i <= len(bidders):
                send_text(phone, f"There are only {len(bidders)} bids for {ref}.")
                return True
            _award(a, bidders[i - 1][0], by=to_wire_number(phone))
        data[ref] = a
        _save(data)
    return True


def _award(a, name, by):
    import dispatcher
    inv = a["invitees"][name]
    amount = _final_bid(inv)
    bidders = _bidders(a)
    rank = [b[0] for b in bidders].index(name) + 1
    t = tl.by_name(name) or {}
    a["status"] = "awarded"
    a["winner"] = {"name": name, "amount": amount, "rank": rank, "by": by, "at": _iso(_now())}
    req = a["request"]

    contacts = dispatcher.regional_ops_contacts(req)
    mg = (f"{contacts[0][0]} – {_pretty_phone(contacts[0][1])}" if contacts
          else (_pretty_phone(a["requester_phone"]) if a.get("requester_phone") else "Megamet office"))
    _say(inv, f"🎉 *Congratulations!* Order {a['ref']} is awarded to you.\n\n"
              f"Route: {_route(req)}\nLoading date: {req.get('required_date') or _tomorrow()}\n"
              f"Vehicle: {_size_text(req)}\nFreight: *{inr(amount)}* (your bid)\n\n"
              f"Megamet contact: {mg}\nPlease share the vehicle number and driver's mobile number.")

    for n, other in a["invitees"].items():
        if n != name and other["bids"] and other["status"] != "withdrawn":
            _say(other, f"Order {a['ref']} ({_route(req)}) has been awarded to another transporter. "
                        f"Thank you for bidding — we'll send you the next load. 🙏")

    who = name + (f" ({t['contact']})" if t.get("contact") else "")
    phones = " / ".join(_pretty_phone(p) for p in [t.get("phone")] + t.get("alt_phones", []) if p)
    requester = a.get("requester_phone")
    requester_is_ops = bool(requester) and dispatcher.is_ops(requester)
    if requester and not requester_is_ops:
        text = (f"Good news! 🎉 Your truck for {_route(req)} is confirmed (Ref {a['ref']}).\n\n"
                f"Transporter: {who}\nContact: {phones}\n\nThey'll be in touch about pickup. "
                f"Thank you for choosing Megamet!")
        try:
            if window_open(requester):
                send_text(requester, text, phone_number_id=a.get("requester_phone_id"))
            else:
                ctx = {"ref": a["ref"], "route": _route(req), "pickup": req.get("pickup"),
                       "destination": req.get("destination"), "transporter": who, "transporter_phone": phones}
                send_template(requester, dispatcher.CONFIRMED_TEMPLATE,
                              [ctx.get(f, "-") for f in dispatcher.CONFIRMED_TEMPLATE_PARAMS],
                              phone_number_id=a.get("requester_phone_id"))
        except Exception as e:
            print(f"[auction] could not tell requester {requester}: {e}")

    how = "auto-awarded (lowest bid)" if by == "auto" else f"awarded by {_pretty_phone(by)}"
    lines = [f"✅ *Order {a['ref']} {how}*", f"{_route(req)} · {_size_text(req)}", "",
             f"Winner: {who} – {phones}", f"Freight: *{inr(amount)}* (L{rank} of {len(bidders)})", "",
             "All bids:"] + _ranking_lines(a, bidders)
    invited = len([i for i in a["invitees"].values() if i["status"] != "failed"])
    na = len([i for i in a["invitees"].values() if i["status"] == "not_available"])
    lines += ["", f"Invited {invited} · bid {len(bidders)} · unavailable {na}"]
    if requester and not requester_is_ops:
        lines.append(f"Customer {_pretty_phone(requester)} has been sent the transporter's details.")
    text = "\n".join(lines)
    targets = set(_ops_for(req)) | ({requester} if requester_is_ops else set())
    for p in targets:
        try:
            dispatcher.send_session_or_template(
                p, text, phone_number_id=a.get("requester_phone_id"),
                alert_ctx=dispatcher._ops_alert_ctx(a["ref"], req, f"{who} at {inr(amount)} (awarded)"))
        except Exception as e:
            print(f"[auction] ops award note to {p} failed: {e}")
    _log(a)


def _tell_requester(a, text, ops_only_detail=False):
    import dispatcher
    p = a.get("requester_phone")
    if not p or (ops_only_detail and not dispatcher.is_ops(p)):
        return
    try:
        if window_open(p):
            send_text(p, text, phone_number_id=a.get("requester_phone_id"))
    except Exception as e:
        print(f"[auction] update to requester failed: {e}")


def _notify_no_bids(a):
    import dispatcher
    req = a["request"]
    invited = [n for n, i in a["invitees"].items() if i["status"] != "failed"]
    call = []
    for n in invited[:10]:
        t = tl.by_name(n) or {}
        nums = " / ".join(_pretty_phone(p) for p in [t.get("phone")] + t.get("alt_phones", []) if p)
        call.append(f"• {n}" + (f" ({t['contact']})" if t.get("contact") else "") + (f" – {nums}" if nums else ""))
    ops_text = "\n".join([f"⚠️ No bids for {_route(req)} (Order {a['ref']}).",
                          f"Invited {len(invited)} transporter{'' if len(invited) == 1 else 's'} on WhatsApp, nobody quoted.", "",
                          "Please call:"] + call)
    for p in set(_ops_for(req)) | ({a["requester_phone"]} if a.get("requester_phone") and
                                                 dispatcher.is_ops(a["requester_phone"]) else set()):
        try:
            dispatcher.send_session_or_template(p, ops_text, alert_ctx=dispatcher._ops_alert_ctx(
                a["ref"], req, f"{len(invited)} transporters, no bids"))
        except Exception as e:
            print(f"[auction] no-bid alert to {p} failed: {e}")
    rp = a.get("requester_phone")
    if rp and not dispatcher.is_ops(rp):
        lines = [f"Sorry, none of our transporters have quoted for {_route(req)} yet (Ref {a['ref']}).",
                 "Our transport team has been alerted and will call you."]
        for n, w in dispatcher.regional_ops_contacts(req):
            lines.append(f"• {(n or 'Megamet').title()} – {_pretty_phone(w)}")
        _tell_requester(a, "\n".join(lines))


_LOG_COLS = ["ref", "created_at", "pickup", "destination", "pickup_hub", "dest_hub", "vehicle_size",
             "transporter", "pool", "status", "round1_bid", "final_bid", "rank", "awarded", "auction_status"]


def _log(a):
    _DATA.mkdir(parents=True, exist_ok=True)
    new = not LOG_PATH.exists()
    req = a["request"]
    ranks = {n: r for r, (n, _, _) in enumerate(_bidders(a), 1)}
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_LOG_COLS)
        if new:
            w.writeheader()
        for n, inv in a["invitees"].items():
            if a.get("kind") == "enquiry" and inv["bids"]:
                for b in inv["bids"]:
                    w.writerow({
                        "ref": a["ref"], "created_at": a["created_at"], "pickup": req.get("pickup"),
                        "destination": req.get("destination"), "pickup_hub": tl.hub(req.get("pickup")),
                        "dest_hub": tl.hub(req.get("destination")),
                        "vehicle_size": b.get("size") or req.get("vehicle_size") or "Any",
                        "transporter": n, "pool": inv["pool"], "status": inv["status"],
                        "round1_bid": b["amount"], "final_bid": b["amount"], "rank": "",
                        "awarded": False, "auction_status": a["status"],
                    })
                continue
            r1 = [b["amount"] for b in inv["bids"] if b["round"] == 1]
            w.writerow({
                "ref": a["ref"], "created_at": a["created_at"], "pickup": req.get("pickup"),
                "destination": req.get("destination"), "pickup_hub": tl.hub(req.get("pickup")),
                "dest_hub": tl.hub(req.get("destination")), "vehicle_size": req.get("vehicle_size") or "Any",
                "transporter": n, "pool": inv["pool"], "status": inv["status"],
                "round1_bid": min(r1) if r1 else "", "final_bid": _final_bid(inv) or "",
                "rank": ranks.get(n, ""), "awarded": bool(a.get("winner") and a["winner"]["name"] == n),
                "auction_status": a["status"],
            })


POOL_LABEL = {"route": "Runs this route", "test": "Test number", "pickup_local": "Pickup-area local", "destination_local": "Destination side",
              "pan_india": "Pan-India", "extra": "Extra", "second_batch": "Second batch"}


def preview(pickup, destination, size=None):
    req = {"pickup": pickup, "destination": destination, "vehicle_size": size}
    for t, pool in select_invitees(req):
        print(f"  {POOL_LABEL[pool]:<18} {t['name']:<28} {t['phone']}  {tl.describe(t)}")
    miss = no_phone_matches(req)
    if miss:
        print("  (fit the route but no valid mobile: " + ", ".join(t["name"] for t in miss) + ")")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:] or ["Mumbai", "Hyderabad", "Big"]
    print(f"{args[0]} -> {args[1]} ({args[2] if len(args) > 2 else 'Any'}):")
    preview(*args[:3])
