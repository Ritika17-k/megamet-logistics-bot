import csv as _csv
import os
import threading
from pathlib import Path as _Path
import uuid
from datetime import datetime, timedelta
import wave_store
import translation
from data_loader import load_transporters_with_contacts, data_completeness_report
from matching import find_matches
from response_log import log_dispatch
from session_store import add_pending_offer
from whatsapp_client import (
    send_text, send_yes_no_buttons, send_template, to_wire_number, window_open,
)

WAVE_PLAN = [
    {"regional": 5, "pan_india": 5},
    {"regional": 25, "pan_india": 25},
]
TOP_N = 25
ESCALATION_MINUTES = 10


def _env_list(key, default):
    return [x.strip() for x in os.environ.get(key, default).split(",") if x.strip()]

OFFER_TEMPLATE = os.environ.get("WA_TEMPLATE_OFFER", "load_offer").strip()
OFFER_TEMPLATE_PARAMS = _env_list("WA_TEMPLATE_OFFER_PARAMS", "pickup,destination,tons,ref")
OFFER_TEMPLATE_BUTTONS = int(os.environ.get("WA_TEMPLATE_OFFER_BUTTONS", "2") or 0)

CONFIRMED_TEMPLATE = os.environ.get("WA_TEMPLATE_CONFIRMED", "load_confirmed").strip()
CONFIRMED_TEMPLATE_PARAMS = _env_list("WA_TEMPLATE_CONFIRMED_PARAMS",
                                      "ref,pickup,destination,transporter,transporter_phone")

OPS_ALERT_TEMPLATE = os.environ.get("WA_TEMPLATE_OPS_ALERT", "").strip()
OPS_ALERT_TEMPLATE_PARAMS = _env_list("WA_TEMPLATE_OPS_ALERT_PARAMS",
                                      "ref,pickup,destination,tons,offered_to")

SIZE_LABEL = {"Small": "Small vehicle", "Big": "Big vehicle", "Any": "Any size"}

def _load_desc(req: dict) -> str:
    req = req or {}
    size = SIZE_LABEL.get(req.get("vehicle_size"), "")
    bits = [b for b in (_fmt_tons(req["weight_tons"]) if req.get("weight_tons") else "",
                        req.get("vehicle_type") or "") if b]
    if size and bits:
        return f"{size} ({', '.join(bits)})"
    return size or ", ".join(bits) or "-"

def _tons_param(req: dict) -> str:
    req = req or {}
    if req.get("weight_tons"):
        return _fmt_tons(req["weight_tons"]).replace(" tons", "")
    return {"Small": "Small vehicle, up to 9",
            "Big": "Big vehicle, 25+",
            "Any": "Any size, approx 9-30"}.get(req.get("vehicle_size"), "-")

def _template_values(fields, ctx):
    return [ctx.get(f, "-") for f in fields]

def _offer_ctx(shipment_id, request):
    return {
        "pickup": request.get("pickup"),
        "destination": request.get("destination"),
        "route": f"{request.get('pickup')} → {request.get('destination')}",
        "weight": _load_desc(request),
        "tons": _tons_param(request),
        "size": _load_desc(request),
        "date": request.get("required_date") or "Flexible",
        "vehicle": request.get("vehicle_type") or "Any",
        "ref": shipment_id,
    }

def _ops_alert_ctx(shipment_id, request, offered_to):
    request = request or {}
    return {
        "ref": shipment_id or "-",
        "pickup": request.get("pickup") or "-",
        "destination": request.get("destination") or "-",
        "route": _route(request),
        "tons": _tons_param(request),
        "weight": _load_desc(request),
        "size": _load_desc(request),
        "offered_to": (offered_to or "-")[:500],
    }

def send_session_or_template(phone, text, phone_number_id=None, alert_ctx=None):
    if window_open(phone):
        send_text(phone, text, phone_number_id=phone_number_id)
        return True
    if OPS_ALERT_TEMPLATE and alert_ctx:
        send_template(phone, OPS_ALERT_TEMPLATE,
                      _template_values(OPS_ALERT_TEMPLATE_PARAMS, alert_ctx),
                      phone_number_id=phone_number_id)
        return True
    print(f"[dispatcher] Skipped message to {to_wire_number(phone)}: outside WhatsApp's 24h "
          f"window and no WA_TEMPLATE_OPS_ALERT set. Text was:\n{text}")
    return False

def new_shipment_id() -> str:
    return f"SHP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

def _offer_message(shipment_id: str, request: dict) -> str:
    lines = [
        "New load available:",
        f"  {request['pickup']} -> {request['destination']}",
        f"  Vehicle: {_load_desc(request)}",
    ]
    if request.get("required_date"):
        lines.append(f"  Required: {request['required_date']}")
    lines.append(f"  Ref: {shipment_id}")
    lines.append("Reply ACCEPT or DECLINE.")
    return "\n".join(lines)

def pretty_name(name) -> str:
    return " ".join(w if len(w) <= 2 else w.title() for w in str(name or "").split())

def _has_phone(phone) -> bool:
    return bool(phone) and str(phone).strip().lower() not in ("", "nan", "none", "<na>")

def _pick_wave_names(ranked_names: dict, wave_plan_entry: dict, already_contacted: set,
                     phone_by_name: dict = None):
    from matching import is_anywhere_transporter
    phone_by_name = phone_by_name or {}
    picked, no_phone = [], []
    for pool, n in wave_plan_entry.items():
        count = 0
        candidates = ranked_names.get(pool, [])
        if pool == "regional":
            candidates = list(dict.fromkeys(
                list(candidates) + [n for n in ranked_names.get("pan_india", [])
                                    if not is_anywhere_transporter(n)]))
        for name in candidates:
            if count >= n:
                break
            if pool == "regional" and is_anywhere_transporter(name):
                continue
            if pool == "pan_india" and not is_anywhere_transporter(name):
                continue
            if name in already_contacted or name in picked or name in no_phone:
                continue
            if _has_phone(phone_by_name.get(name)):
                picked.append(name)
                count += 1
            else:
                no_phone.append(name)
    return picked, no_phone

def get_transporter_language(transporter_name: str, data_path=None) -> str:
    transporters = load_transporters_with_contacts(data_path)
    if "language" not in transporters.columns:
        return "en"
    match = transporters[transporters["transporter_name"] == transporter_name]
    if match.empty:
        return "en"
    lang = match.iloc[0].get("language")
    if not isinstance(lang, str) or lang.strip().lower() not in translation.SUPPORTED_LANGUAGES:
        return "en"
    return lang.strip().lower()

def _message_names(names, shipment_id, request, phone_by_name, lang_by_name=None):
    lang_by_name = lang_by_name or {}
    messaged, no_contact, failed = [], [], []
    accept_id = f"{shipment_id}|ACCEPT"
    decline_id = f"{shipment_id}|DECLINE"
    base_message = _offer_message(shipment_id, request)

    from session_store import open_offers_for_shipment
    phones_done = {p for p, _ in open_offers_for_shipment(shipment_id)}

    for name in names:
        phone = phone_by_name.get(name)
        if not phone or str(phone).strip().lower() in ("", "nan", "none"):
            no_contact.append(name)
            continue

        if to_wire_number(phone) in phones_done:
            continue
        phones_done.add(to_wire_number(phone))

        try:
            if window_open(phone):


                lang = lang_by_name.get(name, "en")
                if not isinstance(lang, str) or lang.strip().lower() not in translation.SUPPORTED_LANGUAGES:
                    lang = "en"
                body = translation.translate_from_english(base_message, lang)
                accept_label = translation.translate_from_english("Accept", lang)
                decline_label = translation.translate_from_english("Decline", lang)
                send_yes_no_buttons(
                    phone, body, accept_id, decline_id,
                    accept_label=accept_label, decline_label=decline_label,
                )
            else:


                send_template(
                    phone, OFFER_TEMPLATE,
                    _template_values(OFFER_TEMPLATE_PARAMS, _offer_ctx(shipment_id, request)),
                    button_payloads=[accept_id, decline_id][:OFFER_TEMPLATE_BUTTONS],
                )
        except Exception as e:
            print(f"[dispatcher] Failed to message {name} ({phone}) for {shipment_id}: {e}")
            failed.append((name, str(e)))
            continue
        add_pending_offer(to_wire_number(phone), shipment_id, name, accept_id, decline_id)
        messaged.append(name)

    return messaged, no_contact, failed

DEDICATED_ROUTES_PATH = _Path(__file__).parent.parent / "data" / "ops_supplied" / "dedicated_routes.csv"

def _dedicated_routes() -> dict:
    from matching import _place_key
    out = {}
    if DEDICATED_ROUTES_PATH.exists():
        with open(DEDICATED_ROUTES_PATH, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                dest = (row.get("destination") or "").strip()
                name = (row.get("transporter_name") or "").strip()
                if dest and name:
                    out[_place_key(dest)] = name
    return out

def dedicated_transporter_for(request: dict, transporters=None):
    from matching import _place_key, _name_key
    name = _dedicated_routes().get(_place_key((request or {}).get("destination")))
    if not name:
        return None
    if transporters is not None and "transporter_name" in transporters.columns:
        for n in transporters["transporter_name"]:
            if _name_key(n) == _name_key(name):
                return n
        print(f"[dispatcher] dedicated_routes.csv names '{name}' but no such transporter "
              f"in the transporter data -- falling back to normal matching")
        return None
    return name

def dedicated_contact(name: str):
    contact, phone, alt = _details([name]).get(name, ("", "", ""))
    return contact, (_pretty_phone(phone) if _has_phone(phone) else "")

def dispatch_shipment(request: dict, top_n: int = TOP_N, data_path=None,
                       requester_phone: str = None, requester_phone_id: str = None) -> dict:
    transporters = load_transporters_with_contacts(data_path)
    completeness = data_completeness_report(transporters)

    dedicated = dedicated_transporter_for(request, transporters)
    if dedicated:
        from matching import MatchResult
        request = dict(request, _dedicated_transporter=dedicated)
        m = MatchResult(dedicated, 1.0, [f"Dedicated transporter for {request.get('destination')}"], [], {})
        m.region = "Dedicated"
        results = {"regional": [m], "pan_india": [m], "eliminated": [], "note": "dedicated route"}
    else:
        results = find_matches(transporters, request, top_n=top_n)

    shipment_id = new_shipment_id()

    log_dispatch(shipment_id, request,
                 dict(results, pan_india=[]) if dedicated else results)

    phone_by_name = dict(
        zip(transporters["transporter_name"], transporters.get("phone", []))
    ) if "phone" in transporters.columns else {}
    lang_by_name = dict(
        zip(transporters["transporter_name"], transporters.get("language", []))
    ) if "language" in transporters.columns else {}

    ranked_names = {
        pool: [m.name for m in results.get(pool, [])]
        for pool in ("regional", "pan_india")
    }

    wave0_names, skipped_no_phone = _pick_wave_names(
        ranked_names, WAVE_PLAN[0], already_contacted=set(), phone_by_name=phone_by_name
    )
    messaged, no_contact, failed = _message_names(wave0_names, shipment_id, request, phone_by_name, lang_by_name)
    no_contact = skipped_no_phone + no_contact

    wave_store.create_wave_state(
        shipment_id,
        wave_index=0,
        contacted_names=wave0_names + skipped_no_phone,
        ranked_names=ranked_names,
        request=request,
        next_escalation_at=datetime.now() + timedelta(minutes=ESCALATION_MINUTES),
        requester_phone=requester_phone,
        requester_phone_id=requester_phone_id,
    )

    wave1_names = set(wave0_names)
    handled_names = wave1_names | set(no_contact) | {n for n, _ in failed}
    queued = []
    for pool in ("regional", "pan_india"):
        for name in ranked_names[pool]:
            if name in handled_names or name in queued:
                continue
            phone = phone_by_name.get(name)
            if phone and str(phone).strip().lower() not in ("", "nan", "none"):
                queued.append(name)

    return {
        "shipment_id": shipment_id,
        "results": results,
        "messaged": messaged,
        "queued": queued,
        "no_contact": no_contact,
        "failed": failed,
        "completeness": completeness,
        "request": request,
        "dedicated": dedicated,
    }


_escalation_lock = threading.Lock()

def escalate_due_shipments(data_path=None):
    with _escalation_lock:
        return _escalate_due_shipments(data_path)

def escalate_now_if_all_declined(shipment_id: str):
    from session_store import open_offers_for_shipment
    state = wave_store.get_wave_state(shipment_id)
    if not state or state.get("resolved"):
        return []
    if open_offers_for_shipment(shipment_id):
        return []
    wave_store.set_due_now(shipment_id)
    return escalate_due_shipments()

def _escalate_due_shipments(data_path=None):
    due = wave_store.due_for_escalation()
    if not due:
        return []

    transporters = load_transporters_with_contacts(data_path)
    phone_by_name = dict(
        zip(transporters["transporter_name"], transporters.get("phone", []))
    ) if "phone" in transporters.columns else {}
    lang_by_name = dict(
        zip(transporters["transporter_name"], transporters.get("language", []))
    ) if "language" in transporters.columns else {}

    escalated = []
    for shipment_id, state in due:
        next_wave_index = state["wave_index"] + 1
        if next_wave_index >= len(WAVE_PLAN):


            wave_store.mark_resolved(shipment_id)
            try:
                notify_ops_no_response(shipment_id, state, phone_by_name)
            except Exception as e:
                print(f"[dispatcher] Could not send no-response alert for {shipment_id}: {e}")
            continue

        request = state.get("request")
        ranked_names = state.get("ranked_names")
        if not request or not ranked_names:
            wave_store.mark_resolved(shipment_id)
            continue

        already_contacted = set(state.get("contacted", []))
        new_names, skipped_no_phone = _pick_wave_names(
            ranked_names, WAVE_PLAN[next_wave_index], already_contacted, phone_by_name
        )
        if not new_names:


            from session_store import open_offers_for_shipment
            if open_offers_for_shipment(shipment_id):
                wave_store.advance_wave(
                    shipment_id, next_wave_index, skipped_no_phone,
                    next_escalation_at=datetime.now() + timedelta(minutes=ESCALATION_MINUTES),
                )
            else:
                wave_store.mark_resolved(shipment_id)
                state = dict(state, contacted=list(state.get("contacted", [])) + skipped_no_phone)
                try:
                    notify_ops_no_response(shipment_id, state, phone_by_name)
                except Exception as e:
                    print(f"[dispatcher] Could not send no-response alert for {shipment_id}: {e}")
                escalated.append({"shipment_id": shipment_id, "wave": next_wave_index,
                                  "messaged": [], "no_contact": skipped_no_phone, "failed": []})
            continue

        messaged, no_contact, failed = _message_names(new_names, shipment_id, request, phone_by_name, lang_by_name)
        no_contact = skipped_no_phone + no_contact
        wave_store.advance_wave(
            shipment_id, next_wave_index, new_names + skipped_no_phone,
            next_escalation_at=datetime.now() + timedelta(minutes=ESCALATION_MINUTES),
        )
        escalated.append({
            "shipment_id": shipment_id, "wave": next_wave_index,
            "messaged": messaged, "no_contact": no_contact, "failed": failed,
        })
    return escalated

def _details(names):
    t = load_transporters_with_contacts()
    out = {}
    for _, r in t[t["transporter_name"].isin(list(names))].iterrows():
        g = lambda c: (str(r.get(c)).strip() if isinstance(r.get(c), str) and r.get(c).strip() else "")
        out[r["transporter_name"]] = (g("contact_person"), g("phone"), g("alt_phone"))
    return out

def _transporter_line(name, request, details, show_fare=False):
    from data_loader import fare_text
    contact, phone, alt = details.get(name, ("", "", ""))
    line = f"• {pretty_name(name)}" + (f" ({contact})" if contact else "")
    nums = [_pretty_phone(p) for p in (phone, alt) if _has_phone(p) and len("".join(c for c in p if c.isdigit())) in (10, 12)]
    if nums:
        line += " – " + " / ".join(nums)
    if show_fare:
        fare = fare_text(name, request)
        if fare:
            line += f" – {fare}"
    return line

def notify_slot_filled(transporter_phone: str, shipment_id: str, transporter_name: str):
    message = (
        f"Update on {shipment_id}: this load has been assigned to another transporter. "
        f"Thanks for responding -- we'll reach out on the next matching load."
    )

    if not window_open(transporter_phone):
        print(f"[dispatcher] Slot-filled note for {shipment_id} not sent to "
              f"{transporter_name}: outside 24h window")
        return
    lang = get_transporter_language(transporter_name)
    send_text(transporter_phone, translation.translate_from_english(message, lang))

def _pretty_phone(phone) -> str:
    wire = to_wire_number(phone)
    if len(wire) == 12 and wire.startswith("91"):
        return f"+91 {wire[2:7]} {wire[7:]}"
    return f"+{wire}"

OPS_TEAM_PATH = _Path(__file__).parent.parent / "data" / "ops_supplied" / "ops_team.csv"

def _ops_team():
    team = []
    if OPS_TEAM_PATH.exists():
        with open(OPS_TEAM_PATH, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                digits = "".join(c for c in row.get("phone", "") if c.isdigit())
                if len(digits) not in (10, 12):
                    if digits:
                        print(f"[dispatcher] ops_team.csv: skipping {row.get('name')} -- "
                              f"phone '{row.get('phone')}' is not a 10-digit number")
                    continue
                team.append((row.get("name") or "", to_wire_number(digits), row.get("regions", "")))
    for n in os.environ.get("OPS_NOTIFY_NUMBERS", "").split(","):
        if "".join(c for c in n if c.isdigit()):
            team.append(("", to_wire_number(n), "all"))
    return team

def _is_all_regions(regions):
    return not regions.strip() or regions.strip().lower() in ("all", "*", "india", "pan-india")

def _covers_region(regions, request):
    from matching import covers_destination
    return covers_destination(regions, request.get("destination"))

def regional_ops_contacts(request: dict):
    if not request:
        return []
    seen, out = set(), []
    for name, wire, regions in _ops_team():
        if not _is_all_regions(regions) and _covers_region(regions, request) and wire not in seen:
            seen.add(wire)
            out.append((name, wire))
    return out

def ops_numbers(request: dict = None):
    team = _ops_team()
    everyone = list(dict.fromkeys(w for _, w, _ in team))
    if not request:
        return everyone
    picked = [w for _, w, r in team if _is_all_regions(r) or _covers_region(r, request)]
    return list(dict.fromkeys(picked)) or everyone

def is_ops(phone) -> bool:
    return bool(phone) and to_wire_number(phone) in ops_numbers()

def notify_ops(text: str, fallback_phone: str = None, fallback_phone_id: str = None,
               exclude_phone: str = None, request: dict = None, alert_ctx: dict = None):
    targets = ops_numbers(request)
    if exclude_phone:
        targets = [n for n in targets if n != to_wire_number(exclude_phone)]
    for n in targets:
        try:
            send_session_or_template(n, text, phone_number_id=fallback_phone_id, alert_ctx=alert_ctx)
        except Exception as e:
            print(f"[dispatcher] Could not message ops number {n}: {e}")

def _route(req: dict) -> str:
    return f"{req.get('pickup')} → {req.get('destination')}" if req.get("pickup") else "this load"

def _fmt_tons(w) -> str:
    try:
        w = float(w)
        return f"{int(w)} tons" if w.is_integer() else f"{w:g} tons"
    except (TypeError, ValueError):
        return f"{w} tons"

def notify_ops_new_booking(summary: dict, requester_phone: str, requester_name: str = None):
    req = summary.get("request") or {}
    who = f"{requester_name} ({_pretty_phone(requester_phone)})" if requester_name else _pretty_phone(requester_phone)
    lines = [
        f"📦 New booking — Ref {summary['shipment_id']}",
        f"From: {who}",
        f"Route: {_route(req)}" + (f" (~{req['distance_km']} km)" if req.get("distance_km") else ""),
        f"Vehicle: {_load_desc(req)}",
    ]
    if req.get("required_date"):
        lines.append(f"Date: {req['required_date']}")
    if req.get("_dedicated_transporter"):
        lines.append(f"Dedicated route: offered only to {pretty_name(req['_dedicated_transporter'])}")
    lines.append("")
    if summary.get("messaged"):
        from data_loader import fare_text
        lines.append("Offered to: " + ", ".join(
            pretty_name(n) + (f" ({fare_text(n, req)})" if fare_text(n, req) else "") for n in summary["messaged"]))
    else:
        lines.append("⚠️ No transporter could be messaged — please arrange manually.")

    offered = (", ".join(pretty_name(n) for n in summary["messaged"]) if summary.get("messaged")
               else "nobody (no transporter could be messaged, please arrange manually)")
    notify_ops("\n".join(lines), exclude_phone=requester_phone, request=req,
               alert_ctx=_ops_alert_ctx(summary["shipment_id"], req, offered))

def notify_ops_of_acceptance(shipment_id: str, transporter_name: str, transporter_phone: str):
    requester_phone, requester_phone_id = wave_store.get_requester(shipment_id)
    state = wave_store.get_wave_state(shipment_id) or {}
    req = state.get("request") or {}
    contact = _details([transporter_name]).get(transporter_name, ("", "", ""))[0]
    t_name = pretty_name(transporter_name) + (f" ({contact})" if contact else "")
    t_phone = _pretty_phone(transporter_phone)

    if requester_phone and not is_ops(requester_phone):
        if window_open(requester_phone):
            send_text(
                requester_phone,
                f"Good news! 🎉 Your truck for {_route(req)} is confirmed (Ref {shipment_id}).\n\n"
                f"Transporter: {t_name}\nContact: {t_phone}\n\n"
                f"They'll be in touch about pickup. Thank you for choosing Megamet!",
                phone_number_id=requester_phone_id,
            )
        else:


            ctx = {
                "ref": shipment_id, "route": _route(req),
                "pickup": req.get("pickup"), "destination": req.get("destination"),
                "transporter": t_name, "transporter_phone": t_phone,
            }
            send_template(
                requester_phone, CONFIRMED_TEMPLATE,
                _template_values(CONFIRMED_TEMPLATE_PARAMS, ctx),
                phone_number_id=requester_phone_id,
            )

    pool = wave_store.pool_for_transporter(shipment_id, transporter_name)
    pool_label = {"regional": " (Regional)", "pan_india": " (Pan-India)"}.get(pool, "")
    customer = _pretty_phone(requester_phone) if requester_phone else "unknown"
    notify_ops(
        f"✅ {shipment_id}: {t_name}{pool_label} ACCEPTED {_route(req)}.\n"
        f"Transporter: {t_phone}\nCustomer: {customer}"
        + (lambda f: f"\nAgreed fare: {f}" if f else "")(__import__("data_loader").fare_text(transporter_name, req))
        + ("\nCustomer has been sent the transporter's details." if requester_phone and not is_ops(requester_phone) else ""),
        fallback_phone=requester_phone, fallback_phone_id=requester_phone_id,
        request=req,
        alert_ctx=_ops_alert_ctx(shipment_id, req, f"{t_name} ({t_phone}), who has ACCEPTED"),
    )

CALL_LIST_SIZE = 5

def _is_big(row) -> bool:
    from matching import is_anywhere_transporter
    return is_anywhere_transporter(row.get("transporter_name"))

def _extra_call_suggestions(req: dict, exclude: set, want_big: int, want_local: int):
    from matching import covers_destination, apply_hard_filters, _within_local_region
    t = load_transporters_with_contacts()
    t = t.sort_values("historical_shipments", ascending=False)
    survivors, _ = apply_hard_filters(t, req)
    matched = set(survivors["transporter_name"]) if not survivors.empty else set()
    big, local_match, local_near = [], [], []
    for _, r in t.iterrows():
        name = r["transporter_name"]
        if name in exclude or not _has_phone(r.get("phone")):
            continue
        routes = r.get("route_coverage") if isinstance(r.get("route_coverage"), str) else ""
        if _is_big(r):
            big.append(name)
        elif name in matched:
            local_match.append(name)
        elif (covers_destination(routes, req.get("destination"))
              or _within_local_region(routes, req.get("destination"))):
            local_near.append(name)
    return big[:max(0, want_big)], (local_match + local_near)[:max(0, want_local)]

def _call_list(req, contacted, show_fare, mark_offered=False):
    if req.get("_dedicated_transporter"):
        name = req["_dedicated_transporter"]
        line = _transporter_line(name, req, _details([name]), show_fare=show_fare)
        if mark_offered and name in contacted:
            line += " (offered on WhatsApp)"
        return [f"🚛 Our {req.get('destination')} transporter:", line]
    t = load_transporters_with_contacts()
    big_names = set(t[[_is_big(r) for _, r in t.iterrows()]]["transporter_name"])
    offered = set(contacted)
    big = [n for n in contacted if n in big_names][:CALL_LIST_SIZE]
    local = [n for n in contacted if n not in big_names][:CALL_LIST_SIZE]
    extra_big, extra_local = _extra_call_suggestions(
        req, offered, CALL_LIST_SIZE - len(big), CALL_LIST_SIZE - len(local))
    big += extra_big
    local += extra_local
    details = _details(set(big) | set(local))
    lines = []
    for title, names in (("🚚 Local transporters:", local), ("🚛 Big transporters:", big)):
        if not names:
            continue
        if lines:
            lines.append("")
        lines.append(title)
        for n in names:
            line = _transporter_line(n, req, details, show_fare=show_fare)
            if mark_offered and n in offered:
                line += " (offered on WhatsApp)"
            lines.append(line)
    return lines

def notify_ops_no_response(shipment_id: str, state: dict, phone_by_name: dict):
    requester_phone, requester_phone_id = wave_store.get_requester(shipment_id)
    req = state.get("request") or {}
    minutes = ESCALATION_MINUTES * len(WAVE_PLAN)

    contacted = [n for n in state.get("contacted", []) if _has_phone(phone_by_name.get(n))]
    ranked = state.get("ranked_names") or {}
    no_phone = list(dict.fromkeys(n for pool in ("regional", "pan_india") for n in ranked.get(pool, [])
                                  if not _has_phone(phone_by_name.get(n))))

    ops_list = _call_list(req, contacted, show_fare=True, mark_offered=True)
    lines = [
        f"⚠️ No transporter has accepted {_route(req)} yet (Ref {shipment_id}).",
        f"Customer: {_pretty_phone(requester_phone) if requester_phone else 'unknown'}",
        (f"Offered to {len(contacted)} transporter{'s' if len(contacted) != 1 else ''} over "
         f"{minutes} minutes." if contacted
         else "No transporter on this route could be reached on WhatsApp."),
    ]
    if ops_list:
        lines += ["Please call:", ""] + ops_list
    if no_phone:
        lines += ["", "Also match this route but have NO number on file: "
                  + ", ".join(pretty_name(n) for n in no_phone)]
    if contacted:
        lines += ["", "Their offers stay open — if anyone accepts later, you'll be told."]

    no_resp = ((", ".join(pretty_name(n) for n in contacted) + ", but nobody has accepted yet")
               if contacted else "nobody (no transporter reachable on WhatsApp)")
    alert_ctx = _ops_alert_ctx(shipment_id, req, no_resp)
    notify_ops("\n".join(lines), fallback_phone=requester_phone, fallback_phone_id=requester_phone_id,
               exclude_phone=requester_phone, request=req, alert_ctx=alert_ctx)

    if requester_phone:
        cust_list = _call_list(req, contacted, show_fare=is_ops(requester_phone))
        if cust_list:
            cust = [
                (f"No transporter has confirmed your {_route(req)} load yet (Ref {shipment_id})."
                 if contacted else
                 f"None of our transporters are on WhatsApp for {_route(req)} yet (Ref {shipment_id})."),
                "",
                ("Please call them directly:" if req.get("_dedicated_transporter")
                 else "Please call these transporters directly:"),
                "",
            ] + cust_list
            if contacted:
                cust += ["", ("If they accept here" if req.get("_dedicated_transporter")
                              else "If any of them accepts here")
                         + ", I'll confirm it to you straight away. 🙏"]
        else:
            cust = [f"Sorry, none of our transporters cover {_route(req)} yet (Ref {shipment_id})."]

        regional = [(n, w) for n, w in regional_ops_contacts(req) if w != to_wire_number(requester_phone)]
        if regional:
            cust += ["", "Megamet transport team for this region:"]
            cust += [f"• {(n or 'Megamet').title()} – {_pretty_phone(w)}" for n, w in regional]
        try:
            send_session_or_template(requester_phone, "\n".join(cust), phone_number_id=requester_phone_id,
                                     alert_ctx=alert_ctx if is_ops(requester_phone) else None)
        except Exception as e:
            print(f"[dispatcher] Could not message requester {requester_phone}: {e}")
