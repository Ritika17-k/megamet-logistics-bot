import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="megamet_smoke_"))

os.environ.update({"WHATSAPP_TOKEN": "", "OPS_NOTIFY_NUMBERS": "", "AUCTION_TEST_ONLY": "0",
                   "AUCTION_ONLY_NEW": "0", "AUCTION_AUTO_AWARD": "1"})

try:
    import fastapi
except ImportError:
    fa = types.ModuleType("fastapi")

    class _App:
        def __init__(self, *a, **k): pass
        def get(self, *a, **k): return lambda f: f
        def post(self, *a, **k): return lambda f: f
    fa.FastAPI, fa.HTTPException, fa.Request, fa.Response = _App, Exception, object, object
    sys.modules["fastapi"] = fa

sys.path.insert(0, str(ROOT / "app"))
import whatsapp_client as wc
import session_store
import transporter_list as tl
import auction
import admin
import dispatcher
import translation

ex = ROOT / "data" / "ops_supplied"
shutil.copy(ex / "TRANSPORTER_LIST.example.xlsx", TMP / "TRANSPORTER_LIST.xlsx")
shutil.copy(ex / "ops_team.example.csv", TMP / "ops_team.csv")
tl.LIST_PATH = TMP / "TRANSPORTER_LIST.xlsx"
tl.ADDED_PATH, tl.REMOVED_PATH = TMP / "added.csv", TMP / "removed.csv"
dispatcher.OPS_TEAM_PATH = TMP / "ops_team.csv"
auction.STATE_PATH, auction.LOG_PATH, auction._DATA = TMP / "auctions.json", TMP / "auction_log.csv", TMP
auction.TRIAL_PATH = TMP / "trial.txt"
admin.SESSIONS_PATH, admin.ADMINS_PATH = TMP / "admin_sessions.json", TMP / "admins.txt"
session_store._SESSIONS_PATH, session_store._OFFERS_PATH, session_store._DATA_DIR = \
    TMP / "sessions.json", TMP / "offers.json", TMP
wc.OUTBOX_LOG, wc._LAST_INBOUND_PATH = TMP / "outbox.log", TMP / "last_inbound.json"
translation.translate_from_english = lambda t, l="en": t
translation.translate_to_english = lambda t, s=None: t

SENT = []
wc._send = lambda payload, phone_number_id=None: SENT.append(payload) or {"dry_run": True}
import webhook

OPS = "919800000101"

def say(frm, text=None, button=None):
    m = {"from": frm, "id": f"m{len(SENT)}-{frm}-{text}-{button}"}
    if button:
        m.update(type="interactive", interactive={"button_reply": {"id": button}})
    else:
        m.update(type="text", text={"body": text})
    webhook._handle_message(m, None)

def texts_to(phone):
    out = []
    for p in SENT:
        if p.get("to") != phone:
            continue
        if p["type"] == "text":
            out.append(p["text"]["body"])
        elif p["type"] == "interactive":
            out.append(p["interactive"]["body"]["text"])
        else:
            out.append("TEMPLATE " + p["template"]["name"])
    return out

def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        check.failed = True

check.failed = False
rows = tl.load()
check(len(rows) == 8 and not any(t["problems"] for t in rows), "sample transporter list reads cleanly")

for t in ["hi", "Need a vehicle", "Mumbai to Chennai", "CUST|big vehicle", "CUST|yes"]:
    say(OPS, button=t) if t.startswith("CUST|") else say(OPS, t)
check(any("Megamet Logistics here" in m for m in texts_to(OPS)), "greeting")
state = json.loads((TMP / "auctions.json").read_text())
ref = list(state)[0]
invited = set(state[ref]["invitees"])
check(invited == {"ABC Roadlines", "XYZ Carrier", "Twin City Transport"},
      f"right transporters invited ({sorted(invited)})")

abc, xyz, twin = "919800000011", "919800000031", "919800000041"
for p in (abc, xyz, twin):
    wc.record_inbound(p)
say(abc, "72000")
say(xyz, "70k")
say(xyz, "71000")
say(twin, button=f"BID|{ref}|NA")
check(any("Can you do a little lower" in m for m in texts_to(xyz)), "higher re-bid gets 'can you do a little lower?'")

auction.tick_now(ref)
a = auction.get(ref)
check(a["status"] == "awarded" and a["winner"]["name"] == "XYZ Carrier" and a["winner"]["amount"] == 70000,
      "lowest bid (XYZ Carrier, 70,000) awarded")
check(any("Congratulations" in m for m in texts_to(xyz)), "winner told")
check(any("awarded to another transporter" in m for m in texts_to(abc)), "other bidder told")

wc.record_inbound(OPS)
say(OPS, "add transporter Demo Logistics, 9800000999, Pune, Pan India, Big")
check(any(t["name"] == "Demo Logistics" for t in tl.load()), "admin 'add transporter' works")

paid, _ = auction.route_history("Mumbai", "Chennai", "Big")
check(paid == [70000.0], "award saved to auction_log.csv for base price")

shutil.rmtree(TMP, ignore_errors=True)
print("\nALL PASSED" if not check.failed else "\nSOME CHECKS FAILED")
sys.exit(1 if check.failed else 0)
