# Megamet Logistics — WhatsApp Truck Booking & Reverse Auction

A WhatsApp-based truck booking and reverse-auction system for **Megamet India Pvt Ltd**.

Customers can request a vehicle through WhatsApp in plain English/Hinglish. The bot collects the pickup location, destination, vehicle requirements and loading date, identifies suitable transporters, runs a time-limited reverse auction, checks the winning rate, and awards the load to the lowest suitable bid.

Every completed auction is stored so Megamet can build **route-wise historical pricing and base rates** over time.

---

# Flow

```text
                         CUSTOMER
                            │
                            │ WhatsApp message
                            ▼
                  ┌────────────────────┐
                  │    BOOKING CHAT    │
                  │  English / Hinglish│
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │ REQUIREMENT PARSER │
                  │                    │
                  │ Pickup             │
                  │ Destination        │
                  │ Vehicle size       │
                  │ Loading date       │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │    CONFIRM LOAD    │
                  │ [Confirm] [Change]│
                  └─────────┬──────────┘
                            │
                            ▼
              ┌──────────────────────────────┐
              │    FIND TRANSPORTERS         │
              │                              │
              │ Local + Route Match          │
              │            +                 │
              │ Pan-India + Pickup Area      │
              └──────────────┬───────────────┘
                             │
                             ▼
                  ┌────────────────────┐
                  │    BID ALERT       │
                  │ SENT TO SELECTED   │
                  │ TRANSPORTERS       │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   30-MIN AUCTION   │
                  │                    │
                  │ Transporters submit│
                  │ their rates        │
                  │                    │
                  │ Rates can only     │
                  │ go DOWN            │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   AUCTION CLOSES   │
                  │                    │
                  │ Find lowest bids   │
                  │ L1 / L2            │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   PRICE CHECK      │
                  │                    │
                  │ Compare current    │
                  │ bids with route    │
                  │ base price         │
                  └─────────┬──────────┘
                            │
                  ┌─────────┴──────────┐
                  │                    │
                  ▼                    ▼
            NORMAL RESULT        OPS REVIEW
                  │                    │
                  │             Award L1 / L2
                  │                  / Cancel
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   LOAD AWARDED     │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │ SAVE AUCTION DATA  │
                  │                    │
                  │ Final rate         │
                  │ All bids           │
                  │ Route              │
                  │ Vehicle size       │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │ UPDATE BASE PRICE  │
                  │ FOR FUTURE LOADS   │
                  └────────────────────┘
```

---

# Features

* **Booking chat** in plain English/Hinglish, with tap buttons. Understands "Mumbai se Pune", "pickup from Bhiwandi", small/big vehicle, dates; defaults the loading date to tomorrow.

* **Who gets a load** (Mumbai → Chennai, for example):

  1. transporters based in/near Mumbai (within ~100 km) whose route list includes Chennai;
  2. Pan-India transporters based in/near Mumbai.

* **Reverse auction**: one 30-minute round (optional second round), rates can only go down, "Share my rate" / "Vehicle unavailable" buttons, rates like `72000`, `72k`, `1.2 lakh`.

* **Price checks** before awarding: a single bid, a bid far below the others (likely a typo), or a bid >25% above the route's base price is sent to ops for approval (Award L1 / L2 / Cancel).

* **Base price per route and vehicle size**: `rates Mumbai to Chennai big`.

* **No-code admin on WhatsApp** (ops and admins): `add transporter Name, 98XXXXXXXX, City, Routes, Big`, `remove transporter 98XXXXXXXX`, `check 98XXXXXXXX`, `transporters`, or send the Excel file.

* **Rate check** without a real load: `rate check Mumbai to Hyderabad` (small + big rates).

* **Rollout modes**: test mode, "new transporters only", or everyone (see Settings).

* **Dry-run mode** without a WhatsApp token: messages go to `data/processed/outbox.log`.

---

# Project Layout

```text
app/
│
├── webhook.py
│   FastAPI app Meta calls; routes every message;
│   runs the auction timer
│
├── conversation.py
│   Booking chat
│   pickup, destination, size, date, confirm
│
├── requirement_parser.py
│   Free text → pickup / destination / size / date
│
├── places.py
│   Indian cities, aliases
│   Bhiwandi → Thane
│   JNPT → Navi Mumbai
│   Distance calculations
│
├── transporter_list.py
│   Reads TRANSPORTER_LIST.xlsx
│   + WhatsApp transporter additions
│
├── auction.py
│   Who gets a load
│   Bid alerts
│   Bids
│   Auction rounds
│   Price checks
│   Award
│   Auction logs
│
├── admin.py
│   WhatsApp admin commands
│   Excel upload
│
├── whatsapp_client.py
│   The only file that calls Meta's API
│   Dry-run support without a token
│
├── translation.py
│   Optional Hindi / Marathi / Gujarati /
│   Tamil / Telugu / Kannada
│
├── session_store.py
│   Chat state stored as JSON files
│
├── dispatcher.py
│   Ops-team lookups and alerts
│   Older accept/decline code
│
├── app.py
│   Command-line checks
│
├── data_loader.py
├── matching.py
├── schema.py
├── response_log.py
├── wave_store.py
└── locations.py
    Older matching pipeline
    Kept because dispatcher.py imports it
    Not used for booking anymore


data/
│
├── ops_supplied/
│   Real data lives here on the server
│   NOT in git
│
└── processed/
    Bot state and logs
    NOT in git


deploy/
│
├── systemd service
├── optional ngrok service
└── VM setup script


tests/
└── smoke_test.py
    End-to-end test with sample data
    No WhatsApp needed
```

---

# 1. How It Works

A customer sends a WhatsApp message such as:

```text
Mumbai se Chennai big truck chahiye tomorrow
```

The bot identifies:

```text
Pickup: Mumbai
Destination: Chennai
Vehicle: Big
Loading date: Tomorrow
```

The customer receives:

```text
🚛 Load Request

Pickup: Mumbai
Destination: Chennai
Vehicle: Big
Loading: Tomorrow

Is this correct?

[Confirm] [Change]
```

Once confirmed, the system starts finding suitable transporters.

---

# 2. Transporter Selection

For a load such as:

```text
Mumbai → Chennai
Vehicle: Big
```

the system looks for:

### Priority 1

Transporters based in/near Mumbai whose route list includes Chennai.

### Priority 2

Pan-India transporters based in/near Mumbai.

This keeps the auction focused on transporters who are relevant to the load.

---

# 3. Reverse Auction

Each load normally has a **30-minute auction round**.

Transporters receive:

```text
🚛 New Load Available

Mumbai → Chennai
Vehicle: Big
Loading: Tomorrow

Auction closes in 30 minutes.

[Share My Rate]
[Vehicle Unavailable]
```

Transporters submit their rates through WhatsApp.

---

# 4. Bidding Rules

Rates can be entered in different formats:

```text
72000
72k
1.2 lakh
₹72,000
```

The system converts them into a standard value.

Example:

```text
72k       → ₹72,000
1.2 lakh  → ₹120,000
72000     → ₹72,000
```

A transporter can only reduce their own previous bid.

Example:

```text
First bid:  ₹75,000  ✓
Second bid: ₹72,000  ✓
Third bid:  ₹70,000  ✓
Fourth bid: ₹73,000  ✗
```

---

# 5. Optional Second Auction Round

The system can optionally run a second round.

```text
Round 1
30 minutes
     ↓
No acceptable result
     ↓
Round 2
Additional bidding
     ↓
Final result
```

The second round can be enabled or disabled through Settings.

---

# 6. Price Check

Before awarding the load, the system checks the bids against the route's historical base price.

Examples of cases that may require operations review:

```text
Only one bid

Bid is significantly lower than
the other bids

Bid is more than 25% above
the route's base price
```

Operations can then choose:

```text
[Award L1]
[Award L2]
[Cancel]
```

---

# 7. Base Price / Historical Pricing

Every completed auction contributes to the route's pricing history.

Base price is maintained by:

```text
Pickup
+
Destination
+
Vehicle size
```

Example:

```text
Mumbai → Chennai → Big

Historical Base Price:
₹70,000
```

Future auctions can compare their bids against this historical rate.

---

# 8. Rate Check

Operations can check a route's rate without creating an actual load.

Example:

```text
rate check Mumbai to Hyderabad
```

The system returns:

```text
Mumbai → Hyderabad

Small: ₹42,000
Big:   ₹58,000
```

This gives the operations team quick pricing information.

---

# 9. Pricing Data

For each completed auction, the system keeps:

```text
Load ID
Pickup
Destination
Vehicle size
Loading date

Auction start time
Auction end time

Transporters notified
Number of bids

All transporter bids
Bid timestamps

Lowest bid
Second-lowest bid

Historical base price
Final awarded rate

Winner
Operations decision

Cancellation / rejection reason
```

This builds Megamet's historical transportation pricing database.

---

# 10. WhatsApp Booking Conversation

The bot understands messages such as:

```text
Mumbai se Pune truck chahiye
```

```text
Pickup Bhiwandi, delivery Chennai
```

```text
Need big vehicle from Mumbai to Hyderabad
```

```text
Tomorrow Mumbai to Pune small truck
```

The requirement parser identifies:

```text
Pickup
Destination
Vehicle size
Loading date
```

If something is missing, the bot asks for the missing information.

---

# 11. Date Handling

If the customer does not provide a loading date:

```text
Default = Tomorrow
```

The bot confirms the interpreted date before starting the auction.

---

# 12. Location Normalization

The system supports Indian cities and common aliases.

Examples:

```text
Bhiwandi → Thane
JNPT → Navi Mumbai
```

The location module also supports distance calculations for transporter selection.

---

# 13. Supported Languages

The primary booking languages are:

* English
* Hinglish

Optional transporter communication:

* Hindi
* Marathi
* Gujarati
* Tamil
* Telugu
* Kannada

Translation is handled separately from the main auction logic.

---

# 14. Admin Commands

Operations and administrators can manage transporter data directly through WhatsApp.

### Add transporter

```text
add transporter Name, 98XXXXXXXX, City, Routes, Big
```

Example:

```text
add transporter ABC Transport, 9876543210, Mumbai, Chennai Pune Hyderabad, Big
```

### Remove transporter

```text
remove transporter 9876543210
```

### Check transporter

```text
check 9876543210
```

### List transporters

```text
transporters
```

### Excel

The administrator can send the transporter Excel file through WhatsApp for processing.

---

# 15. Rollout Modes

The system supports:

### Test Mode

Used for development and testing.

### New Transporters Only

Only newly added transporters receive qualifying loads.

### Everyone

All selected transporters can participate.

These modes are controlled through Settings.

---

# 16. Dry-Run Mode

The application can run without a WhatsApp/Meta API token.

Instead of sending real WhatsApp messages, outgoing messages are written to:

```text
data/processed/outbox.log
```

This allows the complete auction flow to be tested safely.

---

# 17. Webhook Architecture

Meta WhatsApp sends incoming messages to:

```text
app/webhook.py
```

The webhook:

1. Receives the WhatsApp event
2. Validates the event
3. Identifies the conversation
4. Routes the message
5. Updates the booking state
6. Starts or updates the auction when required

---

# 18. Idempotency / Duplicate Protection

WhatsApp/webhook providers may retry events.

The same event should not be processed twice.

Example:

```text
WhatsApp Event ID:
ABC123
```

If `ABC123` was already processed:

```text
Do not create another booking
Do not create another bid
Do not send duplicate messages
```

---

# 19. Audit Trail

Important actions are logged.

Example:

```text
LOAD-2026-00125

10:00  Load created
10:01  Customer confirmed
10:02  Auction started
10:03  T1 bid ₹75,000
10:07  T2 bid ₹72,000
10:15  T1 bid ₹70,000
10:27  T2 bid ₹68,000
10:32  Auction closed
10:33  Price check completed
10:34  T2 awarded
```

This helps with debugging, reporting and dispute handling.

---

# 20. Testing

The project includes:

```text
tests/smoke_test.py
```

The smoke test verifies the main workflow without WhatsApp:

```text
Create booking
      ↓
Parse requirement
      ↓
Select transporters
      ↓
Start auction
      ↓
Receive bids
      ↓
Validate bids
      ↓
Close auction
      ↓
Price check
      ↓
Award load
      ↓
Store historical rate
```

---

# 21. Deployment

Deployment files:

```text
deploy/
├── systemd service
├── optional ngrok service
└── VM setup script
```

The production application runs the FastAPI webhook as a managed service.

---

# 22. Example End-to-End Flow

### Customer

```text
Mumbai se Chennai big truck chahiye kal
```

### Bot

```text
🚛 Load Request

Pickup: Mumbai
Destination: Chennai
Vehicle: Big
Loading: Tomorrow
```

### Customer

```text
Confirm
```

### System

```text
Finding eligible transporters...
```

### Transporters

```text
🚛 LOAD ALERT

Mumbai → Chennai
Big Vehicle
Loading: Tomorrow

Auction closes in 30 minutes.

[Share My Rate]
[Vehicle Unavailable]
```

### Bidding

```text
Transporter A → ₹75,000
Transporter B → ₹72,000
Transporter C → ₹73,000
Transporter A → ₹70,000
Transporter B → ₹68,000
```

### Auction closes

```text
L1 = ₹68,000
L2 = ₹70,000
Base = ₹70,000
```

### Award

```text
🎉 Load Awarded

Mumbai → Chennai
Big
Rate: ₹68,000
```

The final rate and auction information are saved to the pricing database.

---

# 23. Business Benefits

The system is designed to help Megamet:

### Reduce transportation cost

Competitive bidding can reduce unnecessary freight cost.

### Reduce manual work

Operations does not need to call transporters one by one for every load.

### Increase transporter competition

Multiple suitable transporters can compete for the same load.

### Improve pricing visibility

Historical rates create route-wise pricing intelligence.

### Reduce pricing mistakes

Price checks help identify unusual rates before awarding.

---

## Summary

Megamet Logistics is a **WhatsApp-first automated transportation procurement platform**.

The core workflow is:

```text
Customer Request
       ↓
Requirement Parsing
       ↓
Confirmation
       ↓
Transporter Matching
       ↓
30-Minute Reverse Auction
       ↓
Bid Validation
       ↓
Price Check
       ↓
L1/L2 Award
       ↓
Load Execution
       ↓
Historical Rate Storage
       ↓
Better Future Pricing
```

The system combines:

**WhatsApp + Transporter Matching + Reverse Auction + Price Checking + Historical Pricing + Operations Automation**

to create a digital freight-booking workflow for Megamet India.
