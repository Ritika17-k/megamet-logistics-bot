import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
import admin
import auction
from conversation import handle_customer_message
from dispatcher import is_ops, send_session_or_template, _ops_alert_ctx
from session_store import get_customer_session
from whatsapp_client import send_text, send_buttons, record_inbound, download_media

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(name)s %(levelname)s] %(message)s")
logger = logging.getLogger("webhook")

async def _auction_loop():
    while True:
        await asyncio.sleep(60)
        try:
            for ref, status in await asyncio.to_thread(auction.tick):
                logger.info("Auction %s -> %s", ref, status)
        except Exception:
            logger.exception("Auction tick failed")

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_auction_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Megamet Logistics WhatsApp Webhook", lifespan=lifespan)

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "megamet-verify-token")
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET")
ALLOWED_PHONE_ID = (os.environ.get("WHATSAPP_PHONE_ID") or "").strip() or None
_warned_unverified = False

_seen_ids = OrderedDict()
_SEEN_MAX = 5000

def _already_seen(message_id):
    if not message_id:
        return False
    if message_id in _seen_ids:
        return True
    _seen_ids[message_id] = None
    if len(_seen_ids) > _SEEN_MAX:
        _seen_ids.popitem(last=False)
    return False

def _verify_signature(raw_body, signature_header):
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/webhook")
def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)

@app.post("/webhook")
async def receive_webhook(request: Request):
    raw_body = await request.body()

    global _warned_unverified
    if APP_SECRET:
        if not _verify_signature(raw_body, request.headers.get("x-hub-signature-256", "")):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")
    elif not _warned_unverified:
        print("[webhook WARNING] WHATSAPP_APP_SECRET is not set -- incoming webhook requests are NOT being verified as genuinely from Meta.")
        _warned_unverified = True

    body = json.loads(raw_body)
    print("[webhook DEBUG] raw payload:", json.dumps(body, indent=2))
    _process_payload(body)
    return {"status": "received"}

def _log_status(status, phone_id):
    if status.get("status") != "failed":
        return
    for err in status.get("errors") or [{}]:
        logger.warning(
            "WhatsApp delivery FAILED to %s from phone_number_id=%s: code=%s title=%s details=%s (msg id %s)",
            status.get("recipient_id"),
            phone_id,
            err.get("code"),
            err.get("title"),
            (err.get("error_data") or {}).get("details"),
            status.get("id"),
        )

def _process_payload(body):
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_id = value.get("metadata", {}).get("phone_number_id")
            if ALLOWED_PHONE_ID and phone_id and phone_id != ALLOWED_PHONE_ID:
                logger.info("Ignoring event for other phone_number_id=%s", phone_id)
                continue

            for status in value.get("statuses", []):
                _log_status(status, phone_id)

            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts", [])
            }
            for message in value.get("messages", []):
                message["_profile_name"] = names.get(message.get("from"))
                message_id = message.get("id")
                if _already_seen(message_id):
                    logger.info("Skipping already-processed message %s", message_id)
                    continue
                try:
                    _handle_message(message, phone_id)
                except Exception:
                    logger.exception("Failed handling message %s", message_id)

def _handle_message(message, phone_id=None):
    sender = message.get("from")
    if not sender:
        return

    record_inbound(sender)

    msg_type = message.get("type")

    if msg_type == "document":
        if admin.is_admin(sender):
            doc = message.get("document") or {}
            try:
                content = download_media(doc.get("id"))
                _reply(sender, admin.handle_document(sender, doc.get("filename") or "", content), phone_id)
            except Exception as e:
                logger.exception("Transporter list upload failed")
                send_text(sender, f"❌ Couldn't read that file ({e}). The old list is still in use.",
                          phone_number_id=phone_id)
        return

    button_id = None
    if msg_type == "button":
        button_id = (message.get("button") or {}).get("payload") or ""
    elif msg_type == "interactive":
        button_id = message.get("interactive", {}).get("button_reply", {}).get("id")
    if button_id:
        if button_id.startswith("CUST|"):
            _handle_customer_message(sender, button_id[5:], phone_id, message.get("_profile_name"))
            return
        if button_id.startswith("ADM|") and admin.is_admin(sender):
            _reply(sender, admin.handle(sender, button_id), phone_id)
            return
        if button_id.startswith("BID|"):
            admin.cancel_form(sender)
            auction.handle_button(sender, button_id)
            return
        if button_id.startswith("AWARD|"):
            auction.handle_award_button(sender, button_id)
            return
        if "|" in button_id:
            send_text(sender, "Thanks! That load is closed now — we'll send you the next one.",
                      phone_number_id=phone_id)
            return

    text = None
    if msg_type == "text":
        text = message.get("text", {}).get("body", "")
    elif msg_type == "button":
        text = message.get("button", {}).get("text", "")
    if text is None:
        return

    if admin.is_admin(sender):
        admin_reply = admin.handle(sender, text)
        if admin_reply:
            _reply(sender, admin_reply, phone_id)
            return
    if is_ops(sender) and auction.handle_approval_text(sender, text):
        return

    session = get_customer_session(sender) or {}
    busy = session.get("stage") in ("collecting", "disambiguate", "same_city", "confirming")
    if auction.handle_text(sender, text, customer_busy=busy):
        return
    if not busy and auction.forward_from_winner(sender, text):
        return

    _handle_customer_message(sender, text, phone_id, message.get("_profile_name"))

def _handle_customer_message(phone, text, phone_id=None, profile_name=None):
    result = handle_customer_message(phone, text)

    if result["reply"]:
        if result.get("buttons"):
            try:
                send_buttons(phone, result["reply"], result["buttons"], phone_number_id=phone_id)
            except Exception:
                logger.exception("Button message failed, sending plain text instead")
                send_text(phone, result["reply"], phone_number_id=phone_id)
        else:
            send_text(phone, result["reply"], phone_number_id=phone_id)

    if result["dispatch_ready"]:
        summary = auction.start_auction(result["request"], requester_phone=phone,
                                        requester_phone_id=phone_id, requester_name=profile_name)
        requester_is_ops = is_ops(phone)
        send_text(phone, _ops_ack(summary) if requester_is_ops else _customer_ack(summary),
                  phone_number_id=phone_id)
        if not requester_is_ops:
            try:
                who = f"{profile_name} (+{phone})" if profile_name else f"+{phone}"
                text = f"📦 New booking from {who}\n\n" + _ops_ack(summary)
                for p in auction._ops_for(summary["auction"]["request"]):
                    if p != phone:
                        send_session_or_template(p, text, alert_ctx=_ops_alert_ctx(
                            summary["ref"], summary["auction"]["request"],
                            f"{len(summary['invited'])} transporters (bid alert)"))
            except Exception:
                logger.exception("Could not send ops booking copy for %s", summary["ref"])
        if not summary["invited"]:
            auction.tick_now(summary["ref"])

def _reply(phone, r, phone_id=None):
    if not r:
        return
    if r.get("buttons"):
        try:
            send_buttons(phone, r["reply"], r["buttons"], phone_number_id=phone_id)
            return
        except Exception:
            logger.exception("Button message failed, sending plain text instead")
    send_text(phone, r["reply"], phone_number_id=phone_id)

def _customer_ack(summary):
    req = summary["auction"]["request"]
    route = f"{req.get('pickup')} → {req.get('destination')}"
    if not summary["invited"]:
        return (f"Thank you! ✅ Your request for {route} is booked (Ref {summary['ref']}).\n\n"
                f"Our transport team will call you shortly to arrange the truck.")
    total = auction.ROUND1_MINUTES + (auction.ROUND2_MINUTES if auction.TWO_ROUNDS else 0)
    return (f"Thank you! ✅ Your request for {route} is booked (Ref {summary['ref']}).\n\n"
            f"We're collecting rates from {len(summary['invited'])} transporter"
            f"{'' if len(summary['invited']) == 1 else 's'} now and will "
            f"confirm your truck here, usually within {auction.dur(total + 10)}. 🚚")

def _ops_ack(summary):
    a = summary["auction"]
    req = a["request"]
    lines = [f"🚛 Bid alert sent — Order {summary['ref']}",
             f"{req.get('pickup')} → {req.get('destination')} · {auction._size_text(req)}"
             + (f" · ~{req['distance_km']} km" if req.get("distance_km") else ""), ""]
    for pool, names in summary["pools"].items():
        names = [n for n in names if n in summary["invited"]]
        if names:
            lines.append(f"*{auction.POOL_LABEL[pool]}* ({len(names)}): " + ", ".join(names))
    if not summary["invited"]:
        lines.append("⚠️ No transporter could be messaged — please arrange manually.")
    if summary["failed"]:
        lines.append("⚠️ Message failed: " + ", ".join(summary["failed"]))
    if summary["no_phone"]:
        lines.append("ℹ️ Fit this route but no valid mobile in the list: " + ", ".join(summary["no_phone"]))
    if summary["invited"]:
        lines += ["", f"Bidding closes in {auction.dur(auction.ROUND1_MINUTES)}"
                  + (f", then a {auction.ROUND2_MINUTES}-min round 2 for the bidders" if auction.TWO_ROUNDS else "")
                  + ". The lowest fair bid is awarded"
                  + (" automatically" if auction.AUTO_AWARD else " after your approval")
                  + " — you'll get all the bids."]
    return "\n".join(lines)
