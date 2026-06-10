import argparse
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

from .config import DEFAULT_CONFIG_PATH, MonitorConfig, load_config
from .graphql_source import STATUS_BLOCKED, AmazonGraphQLSource
from .models import JOB_STATUS_ACTIVE, JOB_STATUS_CLOSED, MODE_SAFE_MONITOR
from .monitor import MonitorService
from .notifier import check_telegram_configuration


EXIT_OK = 0
EXIT_SOURCE_BLOCKED = 2
EXIT_SOURCE_ERROR = 3
EXIT_CONFIGURATION_ERROR = 4
EXIT_ALERT_FAILURE = 5


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Amazon Hiring Canada monitor")
    parser.add_argument(
        "--config",
        default=os.getenv("MONITOR_CONFIG", DEFAULT_CONFIG_PATH),
        help="Path to the JSON config file",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Validate config, source reachability, and Telegram access",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without requiring or sending Telegram notifications",
    )

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run one monitoring cycle")
    run_parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running with the configured poll interval",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without requiring or sending Telegram notifications",
    )

    confirm_parser = subparsers.add_parser(
        "confirm", help="Manually confirm whether a tracked job is active or closed"
    )
    confirm_parser.add_argument("job_id", help="Job ID such as JOB-CA-0000000441")
    confirm_parser.add_argument(
        "--status",
        required=True,
        choices=[JOB_STATUS_ACTIVE, JOB_STATUS_CLOSED],
        help="Status confirmed by manual inspection",
    )
    confirm_parser.add_argument("--title", default="", help="Optional title override")
    confirm_parser.add_argument(
        "--location", default="", help="Optional location override"
    )
    confirm_parser.add_argument("--notes", default="", help="Optional notes")

    return parser.parse_args()


def configure_logging(config: MonitorConfig) -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    log_directory = os.path.dirname(config.storage.log_file_path) or "."
    os.makedirs(log_directory, exist_ok=True)
    file_handler = RotatingFileHandler(
        config.storage.log_file_path,
        maxBytes=1_000_000,
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def _require_runtime_env(config: MonitorConfig) -> None:
    if os.getenv("DRY_RUN") == "1":
        return

    missing = []
    if not config.telegram.bot_token:
        missing.append(config.telegram.bot_token_env)
    if not config.telegram.chat_id:
        missing.append(config.telegram.chat_id_env)
    if missing:
        raise RuntimeError(
            "Missing required environment variables: %s" % ", ".join(missing)
        )


def run_health_check(config_path: str = DEFAULT_CONFIG_PATH) -> int:
    config = load_config(config_path)
    configure_logging(config)
    logger.info("=== Amazon Hiring Canada health check started ===")

    try:
        _require_runtime_env(config)
    except RuntimeError as error:
        logger.error("%s", error)
        return EXIT_CONFIGURATION_ERROR

    telegram_result = check_telegram_configuration(config)
    if not telegram_result.is_ok:
        logger.error("%s", telegram_result.message)
        return EXIT_CONFIGURATION_ERROR
    logger.info("%s", telegram_result.message)

    if config.mode == MODE_SAFE_MONITOR:
        source_result = AmazonGraphQLSource(config).fetch()
        if source_result.status == STATUS_BLOCKED:
            logger.error("%s", source_result.message)
            return EXIT_SOURCE_BLOCKED
        if not source_result.is_ok:
            logger.error("%s", source_result.message)
            return EXIT_SOURCE_ERROR
        logger.info("%s", source_result.message)

    if config.email_monitor.enabled:
        missing_paths = [
            path
            for path in config.email_monitor.input_paths
            if not os.path.exists(os.path.expanduser(path))
        ]
        if missing_paths:
            logger.error("Configured email monitor paths do not exist: %s", missing_paths)
            return EXIT_CONFIGURATION_ERROR
        logger.info(
            "Email monitor paths look valid: %s",
            ", ".join(config.email_monitor.input_paths),
        )

    logger.info("=== Health check complete ===")
    return EXIT_OK


def run_checker(
    config_path: str = DEFAULT_CONFIG_PATH,
    send_notifications: bool = True,
) -> int:
    config = load_config(config_path)
    configure_logging(config)
    _require_runtime_env(config)

    telegram_result = check_telegram_configuration(config)
    if not telegram_result.is_ok:
        logger.error("%s", telegram_result.message)
        return EXIT_CONFIGURATION_ERROR

    service = MonitorService(config)
    summary = service.run(send_notifications=send_notifications)
    logger.info("Run summary: %s", summary)

    if summary["source_status"] == "blocked":
        return EXIT_SOURCE_BLOCKED
    if summary["source_status"] == "error" and summary["observed_jobs"] == 0:
        return EXIT_SOURCE_ERROR
    return EXIT_OK


def run_loop(
    config_path: str = DEFAULT_CONFIG_PATH,
    send_notifications: bool = True,
) -> int:
    config = load_config(config_path)
    configure_logging(config)
    logger.info(
        "Starting long-running monitor loop with a %ss interval",
        config.poll_interval_seconds,
    )

    while True:
        exit_code = run_checker(config_path, send_notifications=send_notifications)
        if exit_code not in (EXIT_OK, EXIT_SOURCE_BLOCKED, EXIT_SOURCE_ERROR):
            return exit_code
        time.sleep(config.poll_interval_seconds)


def confirm_job_status(
    config_path: str,
    job_id: str,
    status: str,
    title: str = "",
    location: str = "",
    notes: str = "",
) -> int:
    config = load_config(config_path)
    configure_logging(config)
    service = MonitorService(config)
    summary = service.manual_confirm(
        job_id=job_id,
        status=status,
        title=title,
        location=location,
        notes=notes,
    )
    logger.info("Manual confirmation saved: %s", summary)
    return EXIT_OK


def main() -> int:
    load_dotenv()
    args = parse_args()
    if args.dry_run:
        os.environ["DRY_RUN"] = "1"

    if args.health_check:
        return run_health_check(args.config)

    if args.command == "confirm":
        return confirm_job_status(
            config_path=args.config,
            job_id=args.job_id,
            status=args.status,
            title=args.title,
            location=args.location,
            notes=args.notes,
        )

    if args.command == "run" and args.loop:
        return run_loop(args.config, send_notifications=not args.dry_run)

    return run_checker(args.config, send_notifications=not args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
