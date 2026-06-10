# Amazon Hiring Canada Monitor

Safe, stateful monitoring for Amazon Hiring Canada jobs around Calgary, Alberta.

The system is designed to be careful and legal:
- no CAPTCHA bypass
- no anti-bot evasion
- no proxy rotation
- no hidden automation or fingerprint spoofing
- no aggressive scraping

If Amazon blocks or rate-limits direct public monitoring, the monitor degrades gracefully into email parsing and manual-assist flows instead of trying to break through protections.

## Goals

- Monitor Calgary and nearby locations
- Detect new `jobId` values
- Track `new / still_active / changed / closed`
- Avoid duplicate alerts
- Store history in SQLite
- Export a human-readable `data/seen_jobs.json`
- Send useful Telegram alerts with urgency and next action
- Retry safely on transient network failures
- Support `safe_monitor`, `email_monitor`, and `manual_assist`

## Architecture

```text
config/monitor.json
        |
        v
  source adapters
  - Amazon Hiring GraphQL via browser context (safe_monitor)
  - local email parser (.eml/.txt) (email_monitor)
        |
        v
   normalization
   - job_id
   - title
   - location
   - canonical URL
   - fingerprint
        |
        v
   SQLite state store
   - jobs
   - notifications
   - runs
   - processed_messages
        |
        v
 decision engine
 - new
 - changed
 - still_active (cooldown-based reminder)
 - closed (explicit UNPOSTED or after missed runs from a complete source)
 - manual_check_required
        |
        v
 Telegram notifier
 - urgency
 - next step
 - quick open button
```

### Monitoring logic

1. Read the configured source.
2. Extract `jobId`, `title`, `location`, `postingStatus`, schedules, and `url`.
3. Compare current observations with SQLite history.
4. If job is new, send an urgent Telegram alert.
5. If job already exists, do not spam unless status changed or reminder cooldown expired.
6. If Amazon detail explicitly says `UNPOSTED`, mark it closed.
7. If a previously active job disappears from a reliable full source for N runs, mark it closed.
8. If source access fails or becomes interactive, log the issue and send a manual-check alert instead of breaking.

## Why Python

Python is the better primary stack here.

- `sqlite3` is built in, which makes stateful deduplication and history simple and stable.
- Email parsing is much easier with the standard library.
- The monitor is mostly I/O + state transitions, not frontend or heavy browser automation.
- The repository already had Python coverage, so consolidating on Python reduces moving parts.

Node.js is still fine for browser-heavy flows, but for this system the safest and most maintainable path is a Python-first monitor with optional manual browser assistance.

## Modes

### `safe_monitor`

Primary mode.

- Uses Amazon Hiring GraphQL carefully through a normal browser context
- Uses the same GraphQL operations the Amazon Hiring app uses:
  `searchJobCardsByLocation`, `getJobDetail`, and `searchScheduleCards`
- Tracks seeded job IDs even when they are not in search results
- Separates visible jobs from explicit `UNPOSTED` jobs
- Sequential requests with configurable spacing
- Respects safe intervals and retries
- If visibility is partial, it can still find new jobs but will avoid false closure decisions

### `email_monitor`

Fallback mode when public access is unstable.

- Parses local `.eml` or `.txt` alert files
- Good for reliable new-job detection
- Not a complete inventory source, so it does not infer closure from absence

### `manual_assist`

Human-in-the-loop mode.

- Used when Amazon requires interaction or direct monitoring is unreliable
- Sends quick links and guidance
- Lets you confirm a job as `active` or `closed` manually via CLI

## State and Deduplication

Primary storage:
- `data/job_radar.sqlite`

Compatibility/export:
- `data/seen_jobs.json`

Dedup rules:
- A `new` job is alerted once
- `changed` is alerted only when content fingerprint changes
- `still_active` is reminder-based and cooldown-controlled
- `closed` is alerted only when status really transitions
- access/manual alerts have their own cooldown

Closure logic:
- A job is marked closed immediately when Amazon GraphQL detail reports `postingStatus=UNPOSTED`
- A job is marked closed only after it disappears from a complete public source for `close_after_missed_runs`
- If the source is partial, blocked, or interactive, closure is not inferred automatically

## Telegram Alert Format

Each alert includes:

- title
- location
- `jobId`
- direct link
- status
- detection time
- urgency
- next action
- posting status
- schedule count / availability
- pay range
- site IDs
- most recent unposted date
- quick-open button

Examples of status:
- `NEW`
- `STILL ACTIVE`
- `CHANGED`
- `CLOSED`
- `MANUAL CHECK`

## Project Structure

```text
amazon-job-radar/
├── checker/
│   ├── config.py
│   ├── graphql_source.py
│   ├── jobs_api.py
│   ├── main.py
│   ├── models.py
│   ├── monitor.py
│   ├── notifier.py
│   ├── storage.py
│   └── sources/
│       ├── __init__.py
│       └── email_alerts.py
├── config/
│   ├── monitor.json
│   └── monitor.example.json
├── data/
│   └── seen_jobs.json
├── logs/
│   └── check-history.json
├── tests/
│   ├── test_config.py
│   ├── test_email_source.py
│   ├── test_graphql_source.py
│   ├── test_jobs_api.py
│   ├── test_main.py
│   └── test_storage.py
├── requirements.txt
└── README.md
```

## Configuration

Default config file:

```bash
config/monitor.json
```

What you can change there:

- `mode`
- `locations`
- `keywords.include`
- `keywords.exclude`
- `poll_interval_seconds`
- `seed_job_ids`
- `safe_monitor.*`
- `email_monitor.*`
- storage paths

Secrets stay in environment variables:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Setup

### 1. Install Python dependencies

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

### 2. Set Telegram credentials

Use `.env.local` or shell env vars:

```bash
export TELEGRAM_BOT_TOKEN=your_bot_token
export TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Review config

Edit:

```bash
config/monitor.json
```

The seeded Amazon job IDs already include:

- `JOB-CA-0000000552`
- `JOB-CA-0000000441`
- `JOB-CA-0000000438`
- `JOB-CA-0000000443`

### 4. Health check

```bash
python3 -m checker.main --health-check --dry-run
```

### 5. Run once

```bash
python3 -m checker.main run --dry-run
```

Dry-run checks Amazon and writes state, but does not require or send Telegram.

### 6. Run once with Telegram

```bash
python3 -m checker.main run
```

### 7. Run continuously with safe interval

```bash
python3 -m checker.main run --loop
```

### 8. Manual confirmation after opening a page yourself

If Amazon requires interaction in a normal browser, open the page manually and then confirm:

```bash
python3 -m checker.main confirm JOB-CA-0000000441 --status active --notes "Opened manually and job is still visible"
```

Or mark it closed:

```bash
python3 -m checker.main confirm JOB-CA-0000000441 --status closed --notes "Page showed no longer available"
```

## Email Monitor Workflow

If official Amazon Job Alerts are more reliable than direct public monitoring:

1. Switch mode in `config/monitor.json` to `email_monitor`
2. Drop `.eml` files into `data/email-alerts`
3. Run:

```bash
python3 -m checker.main run
```

This mode is intentionally conservative:
- it detects new jobs from alerts
- it does not assume a missing email means a job is closed

## Logging

Primary runtime log:

```bash
logs/monitor.log
```

Structured state:

```bash
data/job_radar.sqlite
data/seen_jobs.json
```

## Risks and Mitigations

### 1. Amazon changes its public response shape

Risk:
- API fields or endpoints may change

Mitigation:
- tolerant JSON parsing
- source probe in health check
- email monitor fallback
- manual-assist fallback

### 2. Amazon blocks public requests

Risk:
- CloudFront or bot protection may block direct access

Mitigation:
- no bypass attempts
- manual-check Telegram alert
- seeded quick links
- manual confirmation CLI
- email-driven monitoring mode

### 3. False closure detection

Risk:
- a job might temporarily disappear from one query

Mitigation:
- closure requires `close_after_missed_runs`
- closure only happens when source inventory is considered complete
- no closure inference from email-only mode

### 4. Notification spam

Risk:
- repeated alerts every few minutes

Mitigation:
- SQLite notification history
- event-type cooldowns
- fingerprint-based change detection
- max notifications per run

### 5. Email parsing is incomplete

Risk:
- some emails may not expose full metadata cleanly

Mitigation:
- parser extracts job ID and URL first
- title/location are best effort
- manual open flow remains available

## Testing

Run all Python tests:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

### Test checklist

- `safe_monitor` finds a new Calgary job and sends one urgent alert
- the same job is not re-alerted on the next run
- a changed title or location produces `CHANGED`
- a missing job becomes `CLOSED` only after the configured missed-run threshold
- blocked source produces `MANUAL CHECK` instead of crashing
- `email_monitor` extracts `jobId` from `.eml`
- `data/job_radar.sqlite` keeps job history
- `data/seen_jobs.json` exports current known IDs
- `confirm ... --status active|closed` updates state after manual verification

## Notes

- This monitor is intentionally conservative. Stability and legality are more important than maximum crawl speed.
- If Amazon tightens access further, the recommended next step is to lean on official Job Alerts plus manual-assist confirmation, not to escalate scraping tactics.
