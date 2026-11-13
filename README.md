# Agentic-AI-Experiments

Experiments and architecture notes for agentic systems.

## US Visa Appointment Tracker (Phase 1)

Alert-first monitor for **H1B / H4** availability across India posts (Mumbai, New Delhi, Chennai, Hyderabad, Kolkata).

- **No portal username/password required** for Phase 1
- Stores every check in SQLite
- Alerts via Telegram (optional) when slots appear
- Local dashboard: check counts, interval, empirical availability probability
- You still book manually on [usvisascheduling.com](https://www.usvisascheduling.com/)

### Quick start (offline demo — no secrets)

```bash
# one check
python -m visa_tracker once

# dashboard at http://127.0.0.1:8080
python -m visa_tracker serve

# continuous loop (uses VISA_TRACKER_INTERVAL_SEC, default 300s)
python -m visa_tracker loop
```

Default collector is `demo` (stdlib only, no pip install).

### Real public feed

1. Copy `.env.example` → `.env`
2. Set:

```bash
VISA_TRACKER_COLLECTOR=http_json
VISA_TRACKER_FEED_URL=https://your-accessible-json-feed
# optional:
VISA_TRACKER_FEED_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Expected JSON: a list (or `{ "slots": [...] }`) of objects with post, visa, slots_count, earliest_date. See `fixtures/sample_feed.json`.

Many consumer slot websites block datacenter IPs. Run the worker on your home machine or a host that can reach your chosen feed.

### Deploy

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
```

- Worker: continuous checks  
- Dashboard: port `8080`

### Docs

- [Architecture plan](docs/us-visa-appointment-tracker-plan.md)

### Cost note (Cursor)

Build with included plan usage / cheaper models. Do **not** run the 24/7 checker as Cloud Agents — deploy the worker elsewhere so ongoing monitoring does not burn Cursor tokens.
