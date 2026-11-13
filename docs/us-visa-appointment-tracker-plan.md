# US Visa Appointment Tracker — Architecture Plan

**Scope:** India posts · visa classes **H-1B** and **H-4** · posts Mumbai, New Delhi, Chennai, Hyderabad, Kolkata  
**Official portal:** [usvisascheduling.com](https://www.usvisascheduling.com/) (India scheduling)  
**Stance:** **Alert-first.** You book manually. Do **not** auto-book.

This document is the implementation plan for a personal monitoring + dashboard system. It is not affiliated with the U.S. Department of State or the scheduling vendor.

---

## 1. Problem statement

You are already registered. The manual loop is:

1. Log in with username / password  
2. Answer security questions  
3. Open **Schedule appointment**  
4. Try each of five India consulates for H1B / H4  
5. Repeat for months — usually zero inventory  

You need automation that:

- Checks on your behalf on a reliable cadence  
- Alerts you the moment a slot appears so you can log in and book  
- Exposes a **dashboard**: how many checks ran, at what interval, over which timeline, and an empirical **probability of availability** by post / visa / time window  

---

## 2. Reality constraints (architect-level)

| Constraint | Implication |
|---|---|
| No public official appointment API | Any “live” view must come from public aggregators and/or authenticated browser sessions |
| Portal ToS / anti-bot | Continuous automated login can lock or flag the account |
| Slots are cancellation-driven | Inventory appears irregularly and disappears in minutes |
| Credentials + security answers | Equivalent to full account takeover if leaked — vault only |
| Auto-booking bots | Higher ToS, ethical, and account risk; **out of scope** for this plan |

**Design decision:** Ship an **observer + alerter + analytics** system. Keep **you** as the booking actor on the official site.

---

## 3. Recommended strategy: phased

### Phase 0 — Buy vs build (1 decision)

Before writing scrapers, evaluate existing India H1B/H4 trackers (e.g. VisaSlots, SlotWatch / AppointVisa, checkvisaslots-style feeds).

- **Buy / subscribe** if Telegram/email alerts for your posts are good enough.  
- **Build** if you want private history, custom probability dashboards, multi-source fusion, or a later authenticated verifier under your control.

### Phase 1 — Public-signal monitor + alerts (**MVP**)

**Goal:** Fast wake-ups without storing portal credentials.

```
cron → public collectors → normalize → Postgres → change detect → Telegram/email
                                                      ↓
                                                 dashboard API
```

- Poll one or more public availability feeds every **2–5 minutes** (with jitter).  
- Normalize to: `{ post, visa_class, earliest_date?, slots_count, observed_at, source }`.  
- Filter to **H1B** and **H4** and your five posts.  
- Alert on **edge**: transition from empty → non-empty, or new earliest date.  
- Deduplicate with cooldown (e.g. same post/visa/date fingerprint within 15–30 min).  
- You open the official site and book.

### Phase 2 — Authenticated personal watcher (**optional, higher risk**)

**Goal:** Confirm what *your* account actually sees.

- Playwright (or equivalent) with secrets from a vault: username, password, security Q&A.  
- Prefer **persisted session cookies** over full login every cycle.  
- Check interval **10–30 minutes** with large jitter — never a tight loop.  
- **Check-only.** No click-to-book automation.  
- Feature-flagged; kill switch if the portal challenges CAPTCHA / locks the session.  
- Log success/failure separately from public collectors so the dashboard can show both.

### Phase 3 — Analytics dashboard (parallel with Phase 1)

Surfaces exactly the questions you asked:

| Metric | Definition |
|---|---|
| `checks_total` | Count of automation runs |
| `check_interval_sec` | Configured target + observed median/p95 between runs |
| `check_success_rate` | Successful fetches / attempts |
| `slots_seen` | Observations with inventory &gt; 0 |
| `p_available` | Empirical rate: checks_with_slots / successful_checks by post × visa × time bucket |
| `time_to_alert_sec` | Observation timestamp → notification send |
| `slot_lifetime_sec` | How long a fingerprint stayed visible across consecutive checks |

**Probability** here is **empirical**, not a predictive ML model. Report Wilson score intervals when N is small so “0% for 5 months” is honest scarcity, not a broken scraper.

---

## 4. Target architecture

### 4.1 Components

| Component | Responsibility |
|---|---|
| **Scheduler** | Cron or long-running loop with jitter |
| **Collectors** | Public feed adapters; optional Playwright auth checker |
| **Normalizer** | Canonical posts + visa classes |
| **Store** | Postgres (checks, observations, alerts, sessions) |
| **Alerter** | Telegram (primary), email/SMS backup; dedupe + cooldown |
| **API** | Read models for the dashboard |
| **Dashboard** | Ops + probability UI |
| **Secrets** | Env / Doppler / cloud secret manager — never in git |

### 4.2 Suggested schema (sketch)

```sql
-- Canonical reference
CREATE TABLE posts (
  id TEXT PRIMARY KEY,          -- e.g. 'mumbai', 'new_delhi', ...
  label TEXT NOT NULL
);

CREATE TABLE visa_classes (
  id TEXT PRIMARY KEY,          -- 'H1B', 'H4'
  label TEXT NOT NULL
);

-- Every automation tick
CREATE TABLE checks (
  id BIGSERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  source TEXT NOT NULL,         -- 'public:<name>' | 'auth:portal'
  status TEXT NOT NULL,         -- 'ok' | 'error' | 'partial'
  error_summary TEXT,
  duration_ms INT
);

-- What was seen
CREATE TABLE observations (
  id BIGSERIAL PRIMARY KEY,
  check_id BIGINT REFERENCES checks(id),
  observed_at TIMESTAMPTZ NOT NULL,
  post_id TEXT REFERENCES posts(id),
  visa_class_id TEXT REFERENCES visa_classes(id),
  slots_count INT NOT NULL DEFAULT 0,
  earliest_date DATE,
  raw JSONB,
  fingerprint TEXT NOT NULL     -- for dedupe / lifetime
);

CREATE TABLE alerts (
  id BIGSERIAL PRIMARY KEY,
  observation_id BIGINT REFERENCES observations(id),
  channel TEXT NOT NULL,        -- 'telegram' | 'email' | 'sms'
  sent_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  payload JSONB
);

CREATE INDEX ON observations (observed_at);
CREATE INDEX ON observations (post_id, visa_class_id, observed_at);
CREATE INDEX ON checks (started_at);
```

### 4.3 Alert rules (MVP)

1. Observation with `slots_count > 0` for configured post × visa.  
2. Fingerprint not alerted within cooldown window.  
3. Send Telegram message with: post, visa, earliest date, source, “open portal now” link.  
4. Optionally require **two sources** (public + auth) before SMS to reduce false positives.

### 4.4 Tech stack (pragmatic personal build)

| Layer | Suggestion |
|---|---|
| Worker | Node (Playwright) **or** Python (Playwright) |
| API + dashboard | Next.js (App Router) or FastAPI + simple React |
| DB | Postgres (Neon / Railway / Supabase) |
| Alerts | Telegram Bot API |
| Config | `.env` + secret manager in prod |
| Packaging | Docker Compose for local; one container + managed DB in cloud |

---

## 5. Deployment: Cursor vs elsewhere

### Can this run “in Cursor”?

| Cursor capability | Fit for this product |
|---|---|
| **Cloud Agents / IDE** | Excellent for **building, testing, PRs** — not an always-on cron |
| **publish-app** | Can host a **dashboard UI** later; still needs a separate worker for 24/7 checks |
| **Always-on slot polling** | **No** — agents are ephemeral task runners |

**Bottom line:** Use Cursor to **design and implement**. Deploy the **checker** somewhere that stays up.

### Where to run the always-on piece

| Option | Fit | Notes |
|---|---|---|
| **Railway / Fly.io / Render** | **Best personal default** | Worker + Postgres + secrets; simple ops |
| **AWS EventBridge + Lambda (+ RDS/Neon)** | Strong reliability | More setup for a single-user app |
| **VPS / always-on home machine** | Lowest cash cost | You own uptime and IP reputation |
| **GitHub Actions cron** | Weak | Interval limits, IP reputation, not ideal for tight loops |

**Recommended topology for you:**

1. Develop in **Cursor** (this repo).  
2. Deploy **worker + Postgres** on **Railway or Fly.io**.  
3. Host **dashboard** on the same service or Vercel.  
4. **Telegram** for wake-ups.  
5. Keep portal password / security answers only in the host secret store (Phase 2).

---

## 6. Security & compliance checklist

- [ ] Never commit credentials, cookies, or security answers  
- [ ] Rotate portal password if ever pasted into chat or logs  
- [ ] Phase 1 first (no portal secrets)  
- [ ] Auth checker behind feature flag + max QPS / min interval  
- [ ] Structured audit log of every login attempt  
- [ ] Alert content should not include full credentials  
- [ ] Read vendor ToS; accept account-risk before enabling Phase 2  
- [ ] No auto-booking in v1 (or ever, unless you explicitly accept the risk later)

---

## 7. Engineering build sequence

1. Data model + migrations (`checks`, `observations`, `alerts`).  
2. One public collector + Telegram dry-run (message yourself).  
3. Dashboard pages: checks over time, interval histogram, `p_available` by post/visa.  
4. Deploy worker + DB; wire secrets; uptime monitor.  
5. Optional Playwright auth checker (feature flag).  
6. Hardening: jitter, exponential backoff, dedupe, multi-source confirm.

---

## 8. What success looks like

- You are woken within ~1–2 minutes of a credible H1B/H4 opening at a watched post.  
- Dashboard proves the bot ran continuously (check counts, intervals, error rate).  
- `p_available` over weeks/months documents scarcity honestly and highlights any day/hour patterns.  
- Your scheduling account remains usable because booking stays human and auth checks stay conservative.

---

## 9. Explicit non-goals (v1)

- Auto-completing the booking flow / CAPTCHA solving for booking  
- Reselling alerts as a commercial multi-tenant SaaS  
- Guaranteeing a slot (impossible — inventory is external)

---

## 10. Implementation status

Phase 1 scaffold lives in `visa_tracker/` (stdlib Python):

- Collectors: `demo` (offline) and `http_json` (pluggable public feed URL)
- SQLite history + Telegram/log alerts
- Dashboard + `/api/metrics`
- Docker Compose for worker + dashboard

**Credentials:** Phase 1 does **not** need your usvisascheduling.com username/password. There is no official government slots API; use a reachable third-party JSON feed via `VISA_TRACKER_FEED_URL`.

**Cursor cost control:** build with included plan usage / cheaper models; do not run the 24/7 loop as Cloud Agents.
