import argparse
import logging
import os
import sys

from dotenv import load_dotenv

try:
    from .jobs_api import (
        STATUS_BLOCKED,
        STATUS_OK,
        fetch_calgary_jobs,
        is_relevant_job,
        probe_search_page,
    )
    from .notifier import check_telegram_configuration, send_telegram_alert
    from .storage import load_seen_jobs, resolve_job_id, save_seen_jobs
except ImportError:
    from jobs_api import (
        STATUS_BLOCKED,
        STATUS_OK,
        fetch_calgary_jobs,
        is_relevant_job,
        probe_search_page,
    )
    from notifier import check_telegram_configuration, send_telegram_alert
    from storage import load_seen_jobs, resolve_job_id, save_seen_jobs


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
EXIT_OK = 0
EXIT_SOURCE_BLOCKED = 2
EXIT_SOURCE_ERROR = 3
EXIT_CONFIGURATION_ERROR = 4
EXIT_ALERT_FAILURE = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Amazon jobs checker")
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Validate Amazon source access and Telegram configuration without sending alerts",
    )
    return parser.parse_args()


def _require_runtime_env() -> None:
    missing = [
        name
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


def _describe_fetch_failure(fetch_result) -> str:
    details = fetch_result.message
    if fetch_result.http_status:
        details = f"{details}; HTTP {fetch_result.http_status}"
    return details


def _select_new_jobs(relevant_jobs: list[dict], seen_ids: set[str]) -> list[dict]:
    new_jobs = []
    pending_ids = set()

    for job in relevant_jobs:
        job_id = resolve_job_id(job)
        if not job_id:
            logger.warning("Skipping job without resolvable identifier: %s", job)
            continue
        if job_id in seen_ids or job_id in pending_ids:
            continue

        job["resolvedJobId"] = job_id
        new_jobs.append(job)
        pending_ids.add(job_id)

    return new_jobs


def run_health_check() -> int:
    logger.info("=== Amazon Jobs Health Check started ===")

    fetch_result = fetch_calgary_jobs(retries=1)
    if fetch_result.is_ok:
        relevant_jobs = [job for job in fetch_result.jobs if is_relevant_job(job)]
        logger.info(
            "Amazon source is healthy: %s total jobs, %s relevant jobs",
            len(fetch_result.jobs),
            len(relevant_jobs),
        )
    else:
        logger.error("Amazon jobs API check failed: %s", _describe_fetch_failure(fetch_result))
        search_result = probe_search_page(retries=1)
        if search_result.is_ok:
            logger.info("%s", search_result.message)
            logger.error(
                "The Amazon site is reachable, but the API query used by the checker is not working"
            )
        elif search_result.status == STATUS_BLOCKED:
            logger.error("Amazon search page is blocked: %s", _describe_fetch_failure(search_result))
            return EXIT_SOURCE_BLOCKED
        else:
            logger.error(
                "Amazon search page check also failed: %s",
                _describe_fetch_failure(search_result),
            )
        return EXIT_SOURCE_ERROR

    telegram_result = check_telegram_configuration()
    if telegram_result.is_ok:
        logger.info("%s", telegram_result.message)
        logger.info("=== Health check complete ===")
        return EXIT_OK

    logger.error("%s", telegram_result.message)
    return EXIT_CONFIGURATION_ERROR


def run_checker() -> int:
    logger.info("=== Amazon Jobs Checker started ===")
    _require_runtime_env()

    telegram_result = check_telegram_configuration()
    if not telegram_result.is_ok:
        logger.error("%s", telegram_result.message)
        return EXIT_CONFIGURATION_ERROR
    logger.info("%s", telegram_result.message)

    fetch_result = fetch_calgary_jobs()
    if fetch_result.status == STATUS_BLOCKED:
        logger.error("Amazon blocked the checker: %s", _describe_fetch_failure(fetch_result))
        return EXIT_SOURCE_BLOCKED
    if not fetch_result.is_ok:
        logger.error("Amazon fetch failed: %s", _describe_fetch_failure(fetch_result))
        search_result = probe_search_page(retries=1)
        if search_result.is_ok:
            logger.info("%s", search_result.message)
            logger.error(
                "The search page is reachable, which suggests the API endpoint or query has changed"
            )
        return EXIT_SOURCE_ERROR

    jobs = fetch_result.jobs
    logger.info("Fetched %s total jobs from Amazon", len(jobs))
    if not jobs:
        logger.info("Amazon source is healthy, but there are no jobs for this query right now")

    seen_ids = load_seen_jobs()
    logger.info("Loaded %s previously seen jobs", len(seen_ids))

    relevant_jobs = [job for job in jobs if is_relevant_job(job)]
    logger.info("Found %s relevant jobs for configured filters", len(relevant_jobs))

    new_jobs = _select_new_jobs(relevant_jobs, seen_ids)
    logger.info("New jobs to alert: %s", len(new_jobs))

    successful_ids = set()
    failed_ids = []
    for job in new_jobs:
        if send_telegram_alert(job):
            successful_ids.add(job["resolvedJobId"])
        else:
            failed_ids.append(job["resolvedJobId"])

    seen_ids.update(successful_ids)
    save_seen_jobs(seen_ids)

    if failed_ids:
        logger.error(
            "Failed to send alerts for %s jobs; they will be retried on the next run",
            len(failed_ids),
        )
        return EXIT_ALERT_FAILURE

    logger.info("=== Run complete ===")
    return EXIT_OK


def main() -> int:
    load_dotenv()
    args = parse_args()
    if args.health_check:
        return run_health_check()
    return run_checker()


if __name__ == "__main__":
    sys.exit(main())
