import random
import re
import places
from requirement_parser import parse_message, parse_bare_field_answer
from session_store import get_customer_session, set_customer_session, clear_customer_session
from datetime import datetime, timedelta

SESSION_TTL = timedelta(hours=2)

REQUIRED = ("pickup", "destination", "vehicle_size")

_GREETING_RE = re.compile(
    r"^(hi+|hey+|hello+|hlo|helo|hii+|yo|namaste|namaskar|ram ram|jai shree krishna|"
    r"good\s*(morning|afternoon|evening)|gm)[\s!.,🙏👋]*$",
    re.IGNORECASE,
)
_RESET_RE = re.compile(
    r"\b(start over|restart|reset|new request|new load|naya request|cancel this|nevermind|never mind)\b",
    re.IGNORECASE,
)
_YES_RE = re.compile(
    r"^(y|yes|yeah|yep|yup|ok|okay|confirm|confirmed|haan|ha|han|haa|ji|ji haan|theek hai|"
    r"thik hai|sure|go ahead|done|correct|right|perfect|send|proceed|book|book it|yes book it|"
    r"yes,? local|local|yes local move)[\s!.,👍✅]*$",
    re.IGNORECASE,
)
_NO_RE = re.compile(r"^(n|no|nope|nahi|nahin|cancel|don'?t|stop|no,? change( it)?|change)[\s!.,❌]*$",
                    re.IGNORECASE)
_THANKS_RE = re.compile(r"\b(thanks|thank you|thanku|thx|ty|dhanyavad|dhanyawad|shukriya)\b", re.IGNORECASE)
_ACK_RE = re.compile(r"^(ok|okay|k|kk|fine|alright|cool|great|nice|👍|🙏)[\s!.,]*$", re.IGNORECASE)
_HOW_ARE_YOU_RE = re.compile(r"\b(how are you|how r u|kaise ho|kaisa hai|kya haal)\b", re.IGNORECASE)
_WHO_RE = re.compile(
    r"\b(who is this|who are you|what is this|what can you do|what do you do|kaun|help|menu|start)\b",
    re.IGNORECASE,
)
_TRANSPORT_RE = re.compile(
    r"\b(truck|trucks|lorry|gaadi|gadi|gaddi|vehicle|transport|transporter|load|loads|"
    r"shipment|consignment|dispatch|despatch|send|bhejna|bhejni|pickup|pick up|deliver|"
    r"delivery|container|trailer|tempo|ftl|ptl|maal)\b",
    re.IGNORECASE,
)
_ROLE_DEST_RE = re.compile(r"\b(dest\w*|drop\w*|deliver\w*|unload\w*|going|jaa?na|jaayega|jayega|"
                           r"reach|to|tak|there|wahan)\b", re.IGNORECASE)
_ROLE_PICKUP_RE = re.compile(r"\b(pick\s*-?\s*up\w*|load(ing)?|origin|source|from|se|here|yahan|"
                             r"uthana|lena)\b", re.IGNORECASE)

_GREETINGS = [
    "Hello! 👋 Megamet Logistics here. What can I do for you?",
]
_HOW_ARE_YOU = [
    "I'm doing well, thanks for asking! 😊 How can I help you today?",
    "All good here, thank you! What can I do for you?",
]
_WHO = (
    "I'm the Megamet Logistics assistant. I arrange trucks for your loads — "
    "anywhere in India. 🚚\n\n"
    "Just tell me what you need in your own words, for example:\n"
    "\"Need a big truck from Mumbai to Hyderabad on 30 Sept\""
)
_NOT_UNDERSTOOD = [
    "Sorry, I didn't quite get that. 🙂 I can arrange a truck for you — just tell me "
    "where to pick the load up and where it's going.",
    "Hmm, I'm not sure I understood. If you need a truck, just tell me the pickup "
    "and delivery place, and I'll take it from there.",
]
_THANKS_REPLY = [
    "You're welcome! 😊 Message me anytime you need a truck.",
    "Happy to help! 🙏 Just message here whenever you have the next load.",
]
_ACK_REPLY = ["👍", "Great! Message me whenever you need a truck. 🙂"]
_SURE = ["Sure!", "Of course!", "Happy to help!", "Sure, I can help with that."]
_GOT_IT = ["Got it", "Okay", "Noted", "Alright"]

SIZE_BUTTONS = [("CUST|small vehicle", "Small vehicle"),
                ("CUST|big vehicle", "Big vehicle"),
                ("CUST|not sure", "Not sure")]
CONFIRM_BUTTONS = [("CUST|yes", "Yes, book it"), ("CUST|no", "Cancel")]
SAME_CITY_BUTTONS = [("CUST|yes local", "Yes, local move"), ("CUST|change", "No, change it")]

SIZE_LABEL = {"Small": "Small vehicle", "Big": "Big vehicle", "Any": "Any size (not sure)"}


def _role_buttons(place):
    return [("CUST|pickup", f"Pickup: {place}"[:20]),
            ("CUST|destination", f"Delivery: {place}"[:20])]

def _pick(options):
    return random.choice(options) if isinstance(options, list) else options

def _now(today=None):
    return today or datetime.now()

def _is_stale(session: dict, today) -> bool:
    ts = session.get("_updated_at")
    if not ts:
        return True
    try:
        updated = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return (today - updated) > SESSION_TTL

def _save(phone, session, today):
    session["_updated_at"] = today.isoformat(timespec="seconds")
    set_customer_session(phone, session)

def _blank_session():
    return {
        "stage": "open",
        "awaiting_field": None,
        "pickup": None,
        "destination": None,
        "vehicle_size": None,
        "weight_tons": None,
        "distance_km": None,
        "required_date": None,
        "vehicle_type": None,
    }

def _missing(session: dict):
    return [f for f in REQUIRED if not session.get(f)]

def _fill_distance(session: dict):
    session["distance_km"] = None
    if session.get("pickup") and session.get("destination"):
        pickup_key = places.resolve_place(session["pickup"])
        dest_key = places.resolve_place(session["destination"])
        if pickup_key and dest_key:
            session["distance_km"] = places.road_distance_km(pickup_key, dest_key)

def _same(a, b):
    if not a or not b:
        return False
    return (places.resolve_place(a) or a.lower()) == (places.resolve_place(b) or b.lower())

def _set_place(session: dict, field: str, value: str):
    session[field] = value
    session.pop("_same_city_ok", None)

def _apply_parse(session: dict, text: str, today, ignore_place=None):
    parsed = parse_message(text, today=today)
    found = False

    for field in ("pickup", "destination"):
        value = parsed.get(field)
        if value and not _same(value, ignore_place):
            if not _same(session.get(field), value):
                _set_place(session, field, value)
            found = True

    for field in ("vehicle_size", "weight_tons", "required_date", "vehicle_type"):
        value = parsed.get(field)
        if value not in (None, ""):
            session[field] = value
            found = True
    if parsed.get("weight_tons") and parsed.get("vehicle_size"):
        session["vehicle_size"] = parsed["vehicle_size"]

    ambiguous = parsed.get("_ambiguous_place")
    if ambiguous and _same(ambiguous, ignore_place):
        ambiguous = None
    if ambiguous:
        found = True
        if _same(ambiguous, session.get("pickup")) or _same(ambiguous, session.get("destination")):
            ambiguous = None
        elif session.get("pickup") and not session.get("destination"):
            _set_place(session, "destination", ambiguous)
            ambiguous = None
        elif session.get("destination") and not session.get("pickup"):
            _set_place(session, "pickup", ambiguous)
            ambiguous = None

    _fill_distance(session)
    return found, ambiguous

def _fmt_weight(w):
    try:
        w = float(w)
        return f"{int(w)} tons" if w.is_integer() else f"{w:g} tons"
    except (TypeError, ValueError):
        return f"{w} tons"

def vehicle_text(req: dict) -> str:
    size = SIZE_LABEL.get(req.get("vehicle_size"), "")
    extra = [x for x in (_fmt_weight(req["weight_tons"]) if req.get("weight_tons") else "",
                         req.get("vehicle_type") or "") if x]
    if size and extra:
        return f"{size} ({', '.join(extra)})"
    return size or ", ".join(extra)

def _route_text(session: dict) -> str:
    if _same(session.get("pickup"), session.get("destination")):
        return f"Local move within {session['pickup']}"
    return f"{session['pickup']} → {session['destination']}"

def _known_summary(session: dict) -> str:
    parts = []
    if session.get("pickup") and session.get("destination"):
        parts.append(_route_text(session))
    elif session.get("pickup"):
        parts.append(f"pickup from {session['pickup']}")
    elif session.get("destination"):
        parts.append(f"going to {session['destination']}")
    if session.get("vehicle_size") or session.get("weight_tons"):
        parts.append(vehicle_text(session))
    if session.get("required_date"):
        parts.append(f"on {session['required_date']}")
    return ", ".join(parts)

def _missing_question(session: dict):
    m = set(_missing(session))
    if {"pickup", "destination"} <= m:
        return "Where should we pick up the load, and where is it going?", None
    if "pickup" in m:
        return "And where should we pick it up from?", None
    if "destination" in m:
        return "And where is it going?", None
    return ("What size of vehicle do you need?\n\n"
            "🛻 *Small* — tempo / LCV (up to about 9 tons)\n"
            "🚛 *Big* — truck / trailer (25 tons and above)", SIZE_BUTTONS)

def _confirmation_text(session: dict) -> str:
    lines = ["Here's what I have:", ""]
    lines.append(f"🚚 {_route_text(session)}")
    if session.get("distance_km") and not _same(session["pickup"], session["destination"]):
        lines.append(f"📏 About {session['distance_km']} km")
    lines.append(f"🚛 {vehicle_text(session)}")
    if session.get("required_date"):
        lines.append(f"📅 Loading: {session['required_date']}")
    else:
        lines.append(f"📅 Loading: {default_loading_date()} (tomorrow)")
    lines.append("")
    tail = "Shall I book a transporter for this? Tap *Yes* to go ahead"
    if not session.get("required_date"):
        tail += ", or tell me another date"
    lines.append(tail + " — or tell me anything to change.")
    return "\n".join(lines)

def _clean_request(session: dict) -> dict:
    return {
        "pickup": session["pickup"],
        "destination": session["destination"],
        "vehicle_size": session.get("vehicle_size"),
        "weight_tons": session.get("weight_tons"),
        "distance_km": session.get("distance_km"),
        "required_date": session.get("required_date") or default_loading_date(),
        "vehicle_type": session.get("vehicle_type"),
    }

def default_loading_date(today=None) -> str:
    return ((today or datetime.now()) + timedelta(days=1)).strftime("%d-%b-%Y")

def _result(reply="", dispatch_ready=False, request=None, buttons=None):
    return {"reply": reply, "dispatch_ready": dispatch_ready, "request": request, "buttons": buttons}

def _ask_role(phone, session, place, today, prefix=""):
    session["stage"] = "disambiguate"
    session["_pending_place"] = place
    session["awaiting_field"] = None
    _save(phone, session, today)
    known = _known_summary(session)
    lead = prefix or (f"{_pick(_GOT_IT)} — {known}. " if known else f"{_pick(_SURE)} 🚚 ")
    return _result(
        reply=f"{lead}Is *{place}* where we pick up the load, or where it's going?",
        buttons=_role_buttons(place),
    )

def handle_customer_message(phone: str, text: str, today=None) -> dict:
    today = _now(today)
    text = (text or "").strip()
    low = text.lower()

    if _GREETING_RE.match(low):
        clear_customer_session(phone)
        _save(phone, _blank_session(), today)
        return _result(reply=_pick(_GREETINGS))

    if _RESET_RE.search(low):
        clear_customer_session(phone)
        _save(phone, _blank_session(), today)
        return _result(reply="No problem, let's start fresh. 🙂 What do you need?")

    session = get_customer_session(phone)
    if session and _is_stale(session, today):
        clear_customer_session(phone)
        session = None
    if session is None:
        session = _blank_session()
    session.setdefault("vehicle_size", None)

    stage = session.get("stage", "open")
    in_booking = stage in ("collecting", "disambiguate", "same_city", "confirming")

    if stage == "confirming":
        if _YES_RE.match(low):
            request = _clean_request(session)
            clear_customer_session(phone)
            return _result(dispatch_ready=True, request=request)
        if _NO_RE.match(low) and low.strip(" !.,") not in ("change", "no change", "no, change", "no change it"):
            clear_customer_session(phone)
            return _result(reply="Okay, I've cancelled that. Just message me whenever you're ready. 🙂")
        found, ambiguous = _apply_parse(session, text, today)
        if ambiguous:
            session["_back_to_confirm"] = True
            return _ask_role(phone, session, ambiguous, today, prefix=f"{_pick(_GOT_IT)}. ")
        if not found:
            _save(phone, session, today)
            return _result(
                reply="Sorry, I didn't catch that change. 🙂 Tap *Yes* to go ahead, or tell me what to "
                      "change (e.g. \"pickup from Thane\", \"big vehicle\" or \"date 30 Sept\").",
                buttons=CONFIRM_BUTTONS,
            )
        return _continue(phone, session, today, updated=True)

    if stage == "same_city":
        if _YES_RE.match(low):
            session["_same_city_ok"] = True
            return _continue(phone, session, today, acknowledge=True)
        city = session.get("destination")
        found, ambiguous = _apply_parse(session, text, today)
        if not _same(session.get("pickup"), session.get("destination")):
            return _continue(phone, session, today, acknowledge=True)
        if ambiguous and not _same(ambiguous, city):
            _set_place(session, "pickup", ambiguous)
            _set_place(session, "destination", city)
            _fill_distance(session)
            return _continue(phone, session, today, acknowledge=True)
        session["pickup"] = None
        session["stage"] = "collecting"
        session["awaiting_field"] = "pickup"
        _save(phone, session, today)
        return _result(reply=f"No problem. Where should we pick up the load for {city}?")

    if stage == "disambiguate":
        pending = session.get("_pending_place")
        found, ambiguous = _apply_parse(session, text, today, ignore_place=None)
        placed = _same(pending, session.get("pickup")) or _same(pending, session.get("destination"))
        if not placed and (session.get("pickup") or session.get("destination")) and not ambiguous:
            if session.get("pickup") and not session.get("destination"):
                _set_place(session, "destination", pending)
                placed = True
            elif session.get("destination") and not session.get("pickup"):
                _set_place(session, "pickup", pending)
                placed = True
            elif _parsed_places(text, today):
                placed = True
        if not placed and not ambiguous:
            target = None
            if _ROLE_DEST_RE.search(low) and not re.search(r"\bpick\s*-?\s*up|\bloading\b", low):
                target = "destination"
            elif _ROLE_PICKUP_RE.search(low):
                target = "pickup"
            if target:
                _set_place(session, target, pending)
                placed = True
        if placed:
            session.pop("_pending_place", None)
            _fill_distance(session)
            back = session.pop("_back_to_confirm", False)
            return _continue(phone, session, today, acknowledge=not back, updated=back)
        if ambiguous and not _same(ambiguous, pending):
            return _ask_role(phone, session, ambiguous, today,
                             prefix=f"{_pick(_GOT_IT)}. First, about {pending}: ")
        _save(phone, session, today)
        return _result(
            reply=f"Just to be sure — is *{pending}* where we pick up the load, or where it's going?",
            buttons=_role_buttons(pending),
        )

    if not in_booking:
        if _THANKS_RE.search(low) and not _TRANSPORT_RE.search(low):
            return _result(reply=_pick(_THANKS_REPLY))
        if _ACK_RE.match(low):
            return _result(reply=_pick(_ACK_REPLY))
        if _HOW_ARE_YOU_RE.search(low):
            _save(phone, session, today)
            return _result(reply=_pick(_HOW_ARE_YOU))
        if _WHO_RE.search(low) and not _TRANSPORT_RE.search(low):
            _save(phone, session, today)
            return _result(reply=_WHO)

    awaiting = session.get("awaiting_field")
    if stage == "collecting" and awaiting == "vehicle_size" and len(text.split()) <= 4:
        value = parse_bare_field_answer("vehicle_size", text, today=today)
        if value:
            session["vehicle_size"] = value
            m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(t|ton|tons|tonne|tonnes)?\s*$", low)
            if m:
                session["weight_tons"] = float(m.group(1))
            _apply_parse(session, text, today)
            return _continue(phone, session, today, acknowledge=True)

    found, ambiguous = _apply_parse(session, text, today)

    if ambiguous:
        return _ask_role(phone, session, ambiguous, today)

    if not found:
        if _TRANSPORT_RE.search(low):
            session["stage"] = "collecting"
            q, buttons = _missing_question(session)
            session["awaiting_field"] = (_missing(session) or [None])[0]
            _save(phone, session, today)
            lead = "" if in_booking else f"{_pick(_SURE)} 🚚 "
            return _result(reply=lead + q, buttons=buttons)
        if in_booking:
            q, buttons = _missing_question(session)
            _save(phone, session, today)
            return _result(reply="Sorry, I didn't catch that. 🙂 " + q, buttons=buttons)
        _save(phone, session, today)
        return _result(reply=_pick(_NOT_UNDERSTOOD))

    return _continue(phone, session, today, acknowledge=True)


def _parsed_places(text, today):
    p = parse_message(text, today=today)
    return bool(p.get("pickup") and p.get("destination"))


def _continue(phone: str, session: dict, today, acknowledge=False, updated=False) -> dict:
    if (_same(session.get("pickup"), session.get("destination"))
            and not session.get("_same_city_ok")):
        session["stage"] = "same_city"
        session["awaiting_field"] = None
        _save(phone, session, today)
        city = session["pickup"]
        return _result(
            reply=f"Pickup and delivery are both *{city}*. Is this a local move within {city}?",
            buttons=SAME_CITY_BUTTONS,
        )

    missing = _missing(session)
    if missing:
        session["stage"] = "collecting"
        session["awaiting_field"] = missing[0]
        _save(phone, session, today)
        summary = _known_summary(session)
        prefix = f"{_pick(_GOT_IT)} — {summary}. " if (acknowledge and summary) else ""
        q, buttons = _missing_question(session)
        return _result(reply=prefix + q, buttons=buttons)

    session["stage"] = "confirming"
    session["awaiting_field"] = None
    _save(phone, session, today)
    lead = f"{_pick(_GOT_IT)}, updated. 👍\n\n" if updated else ""
    return _result(reply=lead + _confirmation_text(session), buttons=CONFIRM_BUTTONS)
