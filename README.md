# Amazon Job Radar

Monitors Amazon.ca for warehouse jobs in Calgary/Balzac area.  
Sends alerts via Telegram + Pushover (Apple Watch siren).

## Project structure

```text
amazon-job-radar/
├── check-amazon.js          # Main script
├── package.json
├── run-local-check.sh       # Local test runner
├── .env                     # Your secrets (never commit!)
├── logs/
│   └── check-history.json   # Auto-generated run history
├── launchd/
│   └── com.amazon-job-radar.plist   # macOS scheduler
├── tests/
│   └── test-filter.js       # Filter unit tests
└── .github/
    └── workflows/
        └── check.yml        # GitHub Actions (every 10 min)
```

## Setup

### 1. Install dependencies
```bash
npm install
npx playwright install chromium
```

### 2. Create `.env`
```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
PUSHOVER_TOKEN=your_pushover_app_token   # optional
PUSHOVER_USER=your_pushover_user_key     # optional
```

Get Telegram token: [@BotFather](https://t.me/BotFather)  
Get chat ID: send a message to your bot, visit `https://api.telegram.org/bot<TOKEN>/getUpdates`  
Pushover (Apple Watch siren): https://pushover.net

### 3. Run locally
```bash
./run-local-check.sh
```

### 4. Run tests
```bash
npm test
```

## GitHub Actions

1. Push repo to GitHub
2. Go to Settings -> Secrets -> Actions
3. Add secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PUSHOVER_TOKEN`, `PUSHOVER_USER`
4. The workflow runs every 10 minutes automatically

## macOS local scheduler (launchd)

Edit `launchd/com.amazon-job-radar.plist` - update paths and tokens.

```bash
cp launchd/com.amazon-job-radar.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.amazon-job-radar.plist
```

View logs:
```bash
tail -f /tmp/amazon-job-radar.log
```

## How it works

```text
Every 10 min:
  1. Try Amazon JSON API (fast, no browser)
  2. If empty -> Playwright browser fallback
  3. Filter: location ∩ job type - blacklist
  4. Dedup: skip already-seen job IDs
  5. Notify: Telegram HTML + Pushover siren
  6. Save history to logs/check-history.json
```

## Filter logic

Location whitelist (any match required):  
`calgary`, `balzac`, `airdrie`, `alberta`, `t3z`, `t4b`, ...

Job whitelist (any match required):  
`warehouse`, `fulfillment`, `associate`, `picker`, `packer`, `stower`, ...

Job blacklist (any match = excluded):  
`software`, `engineer`, `manager`, `recruiter`, `analyst`, `senior`, ...
