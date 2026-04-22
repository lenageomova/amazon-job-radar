# Amazon Job Radar

This repository includes a Python checker for Amazon warehouse and fulfillment job postings around Calgary, Balzac, and nearby Alberta locations. It now distinguishes between a healthy empty result, an Amazon block, and other upstream errors so the workflow does not silently look "successful" when the source is unavailable.

## Structure

```text
amazon-job-radar/
├── .github/workflows/check_jobs.yml
├── checker/
│   ├── jobs_api.py
│   ├── main.py
│   ├── notifier.py
│   └── storage.py
├── data/
│   └── seen_jobs.json
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

1. Create a Python 3.11 virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Run a health check with `python -m checker.main --health-check`.
5. Run the checker with `python -m checker.main`.

## How it works

- Fetches jobs from the public Amazon hiring API.
- Cross-checks the public search page during health checks to distinguish an API problem from a site-wide block.
- Filters jobs by configurable location and title keywords.
- Stores seen job IDs in `data/seen_jobs.json`.
- Sends Telegram alerts only for new matches.
- Falls back to a fingerprint if an API record has no explicit job ID.
- Fails loudly when Amazon returns a CloudFront block or another upstream error.
- Only marks a job as seen after the Telegram alert succeeds, so failed notifications can retry later.

## Verification

Use these commands to verify behaviour locally:

```bash
python -m unittest discover -s tests
python -m checker.main --health-check
python -m checker.main
```

Expected outcomes:

- Health check returns success only when Amazon is reachable and Telegram credentials are valid.
- A CloudFront block produces a non-zero exit code and a log message that explicitly says Amazon blocked the request.
- If the API fails but the search page still loads, the checker tells you that the site is reachable and the API contract is the likely problem.
- A healthy but empty Amazon response logs that no jobs were returned, without pretending the source failed.
- A failed Telegram send leaves the job out of `data/seen_jobs.json`, so it can be retried later.

## GitHub Actions

The workflow at `.github/workflows/check_jobs.yml` runs every 30 minutes, executes unit tests, then runs the checker. It updates `data/seen_jobs.json` in the repository after each successful run.
