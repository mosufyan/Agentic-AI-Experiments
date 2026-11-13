# Agentic-AI-Experiments

US visa appointment tracker for **H1B / H4** across India posts (Mumbai, New Delhi, Chennai, Hyderabad, Kolkata).

## Status: Phases 1–3 implemented

| Phase | What | Status |
|---|---|---|
| 0 | Buy vs build guidance | Documented |
| 1 | Public / demo feeds + alerts + SQLite | Done |
| 2 | Authenticated check-only watcher (Playwright) + `auth_demo` | Done (flagged off by default) |
| 3 | Analytics dashboard (hourly/daily probability, timeline, lifetimes) | Done |

**No portal password needed** for Phase 1 / `auth_demo`. Phase 2 real auth stays **off** until you opt in.

Alert-first only — **never auto-books**.

## Quick start (local testing)

```bash
# synthetic history for dashboard charts
python3 -m visa_tracker seed --days 14 --interval-minutes 60

# one live check (demo collector)
VISA_TRACKER_COLLECTOR=demo python3 -m visa_tracker once

# dual public+auth demo sources
VISA_TRACKER_COLLECTOR=demo,auth_demo python3 -m visa_tracker once

# dashboard
python3 -m visa_tracker serve
# open http://127.0.0.1:8080
```

## Real public feed (Phase 1)

```bash
cp .env.example .env
# set:
# VISA_TRACKER_COLLECTOR=http_json
# VISA_TRACKER_FEED_URL=...
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
python3 -m visa_tracker loop
```

## Phase 2 auth (optional)

1. `pip install -r requirements-auth.txt && playwright install chromium`
2. In `.env`:
   - `VISA_TRACKER_AUTH_ENABLED=1`
   - `VISA_TRACKER_COLLECTOR=http_json,auth` (or `auth` alone)
   - username / password / `VISA_TRACKER_SECURITY_QA`
3. Run from your own machine (not Cloud Agents). Check-only; tune selectors if the portal UI drifts.

Account / ToS risk is yours — keep intervals ≥ 10–15 minutes with jitter.

## Deploy

```bash
cp .env.example .env
docker compose up -d --build
```

Worker + dashboard share a SQLite volume. For Playwright auth, run on a machine with browsers installed (Compose image is Phase 1/3 oriented).

## Docs

- [Architecture plan](docs/us-visa-appointment-tracker-plan.md)

## Cursor cost

Build with included plan usage. Do **not** run the 24/7 loop as Cloud Agents.
