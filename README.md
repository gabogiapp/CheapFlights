# 🛫 Serverless EWR/NYC International Flight Deal & Mistake-Fare Tracker

A production-ready, 100% free, serverless Python flight deal tracker that monitors international round-trip flights departing from **Newark Liberty International (EWR)** and the wider NYC metro area (**JFK**, **LGA**). It dispatches instant push alerts via **Telegram**, **Discord**, or **ntfy.sh** whenever prices drop below target thresholds or mistake fares appear.

Runs entirely on **GitHub Actions** on an automated cron schedule, with zero external databases required.

---

## ⚡ Key Capabilities

* **Google Flights Internal RPC Integration**: Powered by [`punitarani/fli`](https://github.com/punitarani/fli) to directly query Google Flights' internal RPC endpoints (`GetExploreDestinations` for open-ended flexible discovery and `search_flights` for pinned date/route watches) without headless browsers, Selenium, or paid API keys.
* **Secondary Mistake-Fare Feed**: Scrapes and parses the **Secret Flying New York** feed so human-spotted error fares and flash sales are captured in real-time.
* **Post-Booking Reprice Credit Alerts**: Pinned watches for trips you've already booked in Main Cabin—alerts you when the price drops below what you paid so you can cancel and rebook for an instant airline eCredit voucher.
* **Zero-Database Git Persistence**: Tracks already-alerted deals in `data/seen_deals.json` and records rolling 90-day price baselines (with 180-day retention) in `data/price_history.json`, automatically committed back to the repository after each run.
* **Multi-Channel Push Notifications**: Formatted alerts for Telegram bots, Discord webhooks (rich embeds), or ntfy.sh with one-tap Google Flights booking deep links.
* **Resilient Architecture**: Includes [`fast-flights`](https://github.com/AWeirdDev/flights) as an automatic fallback if an endpoint schema fluctuates.

---

## 📁 Repository Structure

```
├── .github/
│   └── workflows/
│       └── flight_tracker.yml    # GitHub Actions cron & manual workflow
├── config.yaml                   # User configuration (airports, ceilings, watches)
├── data/
│   ├── price_history.json        # 14-day rolling price baselines per route
│   └── seen_deals.json           # Deduplicated history of alerted deal keys
├── requirements.txt              # Pinned Python dependencies
├── src/
│   ├── __init__.py
│   ├── config.py                 # Configuration parser & region classifier
│   ├── explore.py                # Google Flights Explore RPC scanner
│   ├── models.py                 # Typed data models & deterministic deal hashing
│   ├── notifier.py               # Telegram, Discord, and ntfy.sh dispatcher
│   ├── pinned.py                 # Pinned route & reprice credit watcher
│   ├── secret_flying.py          # Secret Flying NYC scraper & parser
│   ├── state.py                  # Git-backed state manager & price baselines
│   └── tracker.py                # Main orchestrator CLI
└── README.md
```

---

## ⚙️ Configuration (`config.yaml`)

Edit [`config.yaml`](file:///mnt/c/Users/gabog/Downloads/Projects/CheapFlights/config.yaml) directly to customize your airports, destinations, and price thresholds without changing any code:

```yaml
# Origin airports to monitor (EWR prioritized)
origins:
  - "EWR"
  - "JFK"
  - "LGA"

currency: "USD"
country: "US"
language: "en-US"

max_stops: "ONE_STOP_OR_FEWER"
max_layover_hours: 6.0

# Explore target ceilings (USD round-trip)
explore_price_ceilings:
  Europe: 420
  Caribbean / Mexico / Central America: 260
  South America: 450
  Asia / Japan: 680

# Trigger drop alert when fare is 35%+ below the rolling 90-day baseline
drop_threshold_pct: 35.0
baseline_window_days: 90
history_retention_days: 180
min_baseline_observations: 3

# Pinned watches for booked flights (reprice credit monitor)
pinned_watches:
  - origin: "EWR"
    destination: "LIS"
    departure_date: "2027-01-12"
    return_date: "2027-01-19"
    cabin_class: "ECONOMY"
    target_price: 550.0
    booked_price: 650.0
    notes: "Main cabin booked to Lisbon - watch for eCredit drop"
```

---

## 🚀 Setup & Free Serverless Deployment

### 1. Enable GitHub Actions Write Permissions

Since state is saved directly back to git, grant GitHub Actions write permissions:
1. In your GitHub repository, go to **Settings** → **Actions** → **General**.
2. Scroll down to **Workflow permissions**.
3. Select **Read and write permissions**.
4. Click **Save**.

### 2. Add Notification Secrets

Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret** and add any (or all) of the following:

| Secret Name | Description | Example / Format |
| :--- | :--- | :--- |
| `DISCORD_WEBHOOK_URL` | Discord Channel Webhook URL | `https://discord.com/api/webhooks/...` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token from [@BotFather](https://t.me/BotFather) | `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ` |
| `TELEGRAM_CHAT_ID` | Telegram Chat or Channel ID | `987654321` or `-100123456789` |
| `NTFY_TOPIC` | [ntfy.sh](https://ntfy.sh) Topic Name (no sign-up required) | `ewr-deal-alerts-mysecret` |

*(If no secrets are supplied, the tracker runs in dry-run mode and logs deals to GitHub Actions logs).*

### 3. Run Manually or Let it Run on Cron

* **Automatic**: The tracker runs automatically at 15 minutes past every hour (`15 * * * *`).
* **Manual Trigger**: Navigate to the **Actions** tab → **Flight Deal Tracker** → click **Run workflow**.
* **Travel Deal Tuesday Burst**: An optional burst schedule is provided in `.github/workflows/flight_tracker.yml` to poll every 15 minutes during Travel Deal Tuesday (December 1, 2026).

---

## 🧪 Local Dry-Run Testing

You can run the tracker locally without sending external webhook alerts:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the tracker in Dry Run mode
DRY_RUN=1 python -m src.tracker
```

The script will query live Google Flights RPCs, print the **Top 5 Cheapest International Round-Trip Deals Found**, update `data/seen_deals.json` and `data/price_history.json`, and verify everything runs cleanly.

---

## 🔔 Sample Notifications

### 💳 Reprice Credit Alert (Telegram / Discord)
```
💳 POST-BOOKING REPRICE CREDIT ALERT: EWR → LIS
━━━━━━━━━━━━━━━━━━
✈️ Route: EWR → LIS (Lisbon, Portugal)
💰 Current Price: $402 USD round-trip
💳 Booked Price: $650 USD
💵 Airline eCredit: $248 savings!
🛫 Airline: TAP Air Portugal (Nonstop)
📅 Travel Dates: 2027-01-12 to 2027-01-19
⏱️ Flight Duration: 6h 55m
💡 Cancel and rebook in Main Cabin to claim your eCredit voucher.

👉 One-Tap Booking on Google Flights: https://www.google.com/travel/flights?q=...
```

### 🔥 Explore Drop / Mistake Fare Alert (Discord Rich Embed)
```
🔥 EXPLORE DROP ALERT: EWR → CDG ($310)
Round-trip international flight deal detected out of Newark (EWR)!
• Round-Trip Fare: $310 USD (38% below $500 baseline)
• Airline & Stops: United Airlines (Nonstop)
• Travel Dates: 2027-01-12 → 2027-01-19 (7 nights)
• Flight Duration: 7h 20m
• Deep Link: [Open Google Flights Search]
```

---

## 📄 License

MIT License. Free for personal and commercial use.
