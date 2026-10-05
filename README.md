# Megamet Logistics — WhatsApp truck booking with reverse auction

A WhatsApp bot for Megamet India. Someone asks for a vehicle on WhatsApp, the
bot collects the route and vehicle size, sends a **bid alert** to the right
transporters, collects their rates for 30 minutes and awards the load to the
**lowest fair bid**. Rates are kept, so every route builds up a **base price**.

## Features

- **Booking chat** in plain English/Hinglish, with tap buttons. Understands "Mumbai se Pune",
  "pickup from Bhiwandi", small/big vehicle, dates; defaults the loading date to tomorrow.
- **Who gets a load** (Mumbai → Chennai, for example):
  1. transporters based in/near Mumbai (within ~100 km) whose route list includes Chennai;
  2. Pan-India transporters based in/near Mumbai.
- **Reverse auction**: one 30-minute round (optional second round), rates can only go down,
  "Share my rate" / "Vehicle unavailable" buttons, rates like `72000`, `72k`, `1.2 lakh`.
- **Fair-price checks** before awarding: a single bid, a bid far below the others (likely a typo),
  or a bid >25% above the route's base price is sent to ops for approval (Award L1 / L2 / Cancel).
- **Base price per route and vehicle size**: `rates Mumbai to Chennai big`.
- **No-code admin on WhatsApp** (ops and admins): `add transporter Name, 98XXXXXXXX, City, Routes, Big`,
  `remove transporter 98XXXXXXXX`, `check 98XXXXXXXX`, `transporters`, or send the Excel file.
- **Rate check** without a real load: `rate check Mumbai to Hyderabad` (small + big rates).
- **Rollout modes**: test mode, "new transporters only", or everyone (see Settings).
- **Dry-run mode** without a WhatsApp token: messages go to `data/processed/outbox.log`.

## Project layout
app/
  webhook.py            FastAPI app Meta calls; routes every message; runs the auction timer
  conversation.py       booking chat (pickup, destination, size, date, confirm)
  requirement_parser.py free text -> pickup / destination / size / date
  places.py             Indian cities, aliases (Bhiwandi -> Thane, JNPT -> Navi Mumbai), distances
  transporter_list.py   reads data/ops_supplied/TRANSPORTER_LIST.xlsx (+ WhatsApp additions)
  auction.py            who gets a load, bid alerts, bids, rounds, fair-price checks, award, logs
  admin.py              WhatsApp admin commands and Excel upload
  whatsapp_client.py    the only file that calls Meta's API (dry run without a token)
  translation.py        optional Hindi/Marathi/Gujarati/Tamil/Telugu/Kannada for transporters
  session_store.py      chat state (JSON files)
  dispatcher.py         ops-team lookups and ops alerts (ops_team.csv); older accept/decline code
  app.py                command line checks (see below)
  data_loader.py, matching.py, schema.py, response_log.py, wave_store.py, locations.py
                        older matching pipeline (Logistics_Master.xlsx), kept because
                        dispatcher.py imports it; not used for booking any more
data/ops_supplied/      real data lives here on the server (NOT in git) -- see *.example.* files
data/processed/         bot state and logs (NOT in git)
deploy/                 systemd service, optional ngrok service, VM setup script
tests/smoke_test.py     end-to-end test with sample data, no WhatsApp needed


