import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import MODE_EMAIL_MONITOR, MODE_MANUAL_ASSIST, MODE_SAFE_MONITOR


logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/monitor.json"
MIN_SAFE_INTERVAL_SECONDS = 300

DEFAULT_LOCATIONS = [
    {"label": "Calgary", "query": "Calgary", "radius_km": 80},
    {"label": "Balzac", "query": "Balzac", "radius_km": 80},
    {"label": "Airdrie", "query": "Airdrie", "radius_km": 80},
    {"label": "Crossfield", "query": "Crossfield", "radius_km": 80},
]
DEFAULT_INCLUDE_KEYWORDS = [
    "warehouse",
    "fulfillment",
    "delivery",
    "sortation",
    "associate",
    "picker",
    "packer",
    "stower",
]
DEFAULT_EXCLUDE_KEYWORDS = [
    "software",
    "engineer",
    "manager",
    "recruiter",
    "analyst",
    "senior",
]
DEFAULT_BASE_QUERIES = [
    "warehouse associate",
    "fulfillment center",
    "sortation associate",
    "delivery station",
    "package handler",
]


def _environment_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    logger.warning("Ignoring invalid boolean value for %s: %s", name, value)
    return default


@dataclass
class LocationConfig:
    label: str
    query: str
    radius_km: int = 80


@dataclass
class KeywordsConfig:
    include: List[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE_KEYWORDS))
    exclude: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_KEYWORDS))

    def normalized_include(self) -> List[str]:
        return [item.strip().lower() for item in self.include if item and item.strip()]

    def normalized_exclude(self) -> List[str]:
        return [item.strip().lower() for item in self.exclude if item and item.strip()]


@dataclass
class TelegramConfig:
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    disable_web_page_preview: bool = False
    send_access_issue_alerts: bool = True
    send_access_issue_alerts_env: str = "SEND_ACCESS_ISSUE_ALERTS"

    @property
    def bot_token(self) -> str:
        return os.getenv(self.bot_token_env, "")

    @property
    def chat_id(self) -> str:
        return os.getenv(self.chat_id_env, "")

    @property
    def access_issue_alerts_enabled(self) -> bool:
        return _environment_bool(
            self.send_access_issue_alerts_env,
            self.send_access_issue_alerts,
        )


@dataclass
class StorageConfig:
    sqlite_path: str = "data/job_radar.sqlite"
    seen_jobs_json_path: str = "data/seen_jobs.json"
    log_file_path: str = "logs/monitor.log"


@dataclass
class SafeMonitorSettings:
    api_base_url: str = "https://hiring.amazon.ca/api/v1/search"
    graphql_url: str = "https://hiring.amazon.ca/graphql"
    app_search_url: str = (
        "https://hiring.amazon.ca/app#/jobSearch?base_query=&loc_query=Calgary&radius=80"
    )
    search_probe_url: str = "https://hiring.amazon.ca/en/search?base_query=&loc_query=Calgary"
    base_queries: List[str] = field(default_factory=lambda: list(DEFAULT_BASE_QUERIES))
    page_size: int = 100
    schedule_page_size: int = 100
    request_timeout_seconds: int = 15
    retry_attempts: int = 3
    retry_backoff_seconds: int = 5
    request_spacing_seconds: float = 1.5
    max_requests_per_run: int = 12
    enable_direct_graphql: bool = False
    enable_browser_graphql: bool = True
    browser_timeout_seconds: int = 45
    browser_post_load_wait_seconds: float = 4.0
    user_agent: str = "AmazonJobRadar/5.0 (+polite public monitoring)"


@dataclass
class EmailMonitorSettings:
    enabled: bool = False
    input_paths: List[str] = field(default_factory=list)
    allowed_extensions: List[str] = field(default_factory=lambda: [".eml", ".txt"])
    max_files_per_run: int = 50


@dataclass
class ManualAssistSettings:
    tracked_links_limit: int = 5
    guidance: str = (
        "Open the job in a normal browser. If Amazon asks for interactive verification, "
        "complete it manually and then confirm the result in the monitor."
    )


@dataclass
class MonitorConfig:
    mode: str = MODE_SAFE_MONITOR
    poll_interval_seconds: int = 600
    notification_cooldown_minutes: int = 180
    still_active_reminder_minutes: int = 720
    manual_action_cooldown_minutes: int = 240
    close_after_missed_runs: int = 2
    max_notifications_per_run: int = 10
    seed_job_ids: List[str] = field(default_factory=list)
    locations: List[LocationConfig] = field(
        default_factory=lambda: [LocationConfig(**item) for item in DEFAULT_LOCATIONS]
    )
    keywords: KeywordsConfig = field(default_factory=KeywordsConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    safe_monitor: SafeMonitorSettings = field(default_factory=SafeMonitorSettings)
    email_monitor: EmailMonitorSettings = field(default_factory=EmailMonitorSettings)
    manual_assist: ManualAssistSettings = field(default_factory=ManualAssistSettings)


def _load_json_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        logger.warning("Config file %s not found, using built-in defaults", path)
        return {}

    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError("Config root must be a JSON object")
        return payload


def _load_locations(items: Any) -> List[LocationConfig]:
    if not isinstance(items, list) or not items:
        return [LocationConfig(**item) for item in DEFAULT_LOCATIONS]

    locations = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("query") or "").strip()
        query = str(item.get("query") or item.get("label") or "").strip()
        radius_km = int(item.get("radius_km", 80))
        if not query:
            continue
        locations.append(LocationConfig(label=label or query, query=query, radius_km=radius_km))

    return locations or [LocationConfig(**item) for item in DEFAULT_LOCATIONS]


def _as_string_list(value: Any, defaults: Optional[List[str]] = None) -> List[str]:
    defaults = defaults or []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(defaults)


def _clamp_poll_interval(seconds: int) -> int:
    if seconds < MIN_SAFE_INTERVAL_SECONDS:
        logger.warning(
            "Configured poll interval %ss is too aggressive; clamping to %ss",
            seconds,
            MIN_SAFE_INTERVAL_SECONDS,
        )
        return MIN_SAFE_INTERVAL_SECONDS
    return seconds


def load_config(path: Optional[str] = None) -> MonitorConfig:
    config_path = path or os.getenv("MONITOR_CONFIG", DEFAULT_CONFIG_PATH)
    payload = _load_json_file(config_path)

    keywords_payload = payload.get("keywords", {})
    telegram_payload = payload.get("telegram", {})
    storage_payload = payload.get("storage", {})
    safe_payload = payload.get("safe_monitor", {})
    email_payload = payload.get("email_monitor", {})
    manual_payload = payload.get("manual_assist", {})

    config = MonitorConfig(
        mode=str(payload.get("mode", MODE_SAFE_MONITOR)).strip() or MODE_SAFE_MONITOR,
        poll_interval_seconds=_clamp_poll_interval(
            int(payload.get("poll_interval_seconds", 600))
        ),
        notification_cooldown_minutes=int(
            payload.get("notification_cooldown_minutes", 180)
        ),
        still_active_reminder_minutes=int(
            payload.get("still_active_reminder_minutes", 720)
        ),
        manual_action_cooldown_minutes=int(
            payload.get("manual_action_cooldown_minutes", 240)
        ),
        close_after_missed_runs=max(1, int(payload.get("close_after_missed_runs", 2))),
        max_notifications_per_run=max(
            1, int(payload.get("max_notifications_per_run", 10))
        ),
        seed_job_ids=_as_string_list(payload.get("seed_job_ids")),
        locations=_load_locations(payload.get("locations")),
        keywords=KeywordsConfig(
            include=_as_string_list(
                keywords_payload.get("include"), list(DEFAULT_INCLUDE_KEYWORDS)
            ),
            exclude=_as_string_list(
                keywords_payload.get("exclude"), list(DEFAULT_EXCLUDE_KEYWORDS)
            ),
        ),
        telegram=TelegramConfig(
            bot_token_env=str(
                telegram_payload.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
            ),
            chat_id_env=str(telegram_payload.get("chat_id_env", "TELEGRAM_CHAT_ID")),
            disable_web_page_preview=bool(
                telegram_payload.get("disable_web_page_preview", False)
            ),
            send_access_issue_alerts=bool(
                telegram_payload.get("send_access_issue_alerts", True)
            ),
            send_access_issue_alerts_env=str(
                telegram_payload.get(
                    "send_access_issue_alerts_env",
                    "SEND_ACCESS_ISSUE_ALERTS",
                )
            ),
        ),
        storage=StorageConfig(
            sqlite_path=str(storage_payload.get("sqlite_path", "data/job_radar.sqlite")),
            seen_jobs_json_path=str(
                storage_payload.get("seen_jobs_json_path", "data/seen_jobs.json")
            ),
            log_file_path=str(storage_payload.get("log_file_path", "logs/monitor.log")),
        ),
        safe_monitor=SafeMonitorSettings(
            api_base_url=str(
                safe_payload.get(
                    "api_base_url", "https://hiring.amazon.ca/api/v1/search"
                )
            ),
            graphql_url=str(
                safe_payload.get("graphql_url", "https://hiring.amazon.ca/graphql")
            ),
            app_search_url=str(
                safe_payload.get(
                    "app_search_url",
                    "https://hiring.amazon.ca/app#/jobSearch?base_query=&loc_query=Calgary&radius=80",
                )
            ),
            search_probe_url=str(
                safe_payload.get(
                    "search_probe_url",
                    "https://hiring.amazon.ca/en/search?base_query=&loc_query=Calgary",
                )
            ),
            base_queries=_as_string_list(
                safe_payload.get("base_queries"), list(DEFAULT_BASE_QUERIES)
            ),
            page_size=max(1, int(safe_payload.get("page_size", 100))),
            schedule_page_size=max(
                1, int(safe_payload.get("schedule_page_size", 100))
            ),
            request_timeout_seconds=max(
                5, int(safe_payload.get("request_timeout_seconds", 15))
            ),
            retry_attempts=max(1, int(safe_payload.get("retry_attempts", 3))),
            retry_backoff_seconds=max(
                1, int(safe_payload.get("retry_backoff_seconds", 5))
            ),
            request_spacing_seconds=max(
                0.0, float(safe_payload.get("request_spacing_seconds", 1.5))
            ),
            max_requests_per_run=max(
                1, int(safe_payload.get("max_requests_per_run", 12))
            ),
            enable_direct_graphql=bool(
                safe_payload.get("enable_direct_graphql", False)
            ),
            enable_browser_graphql=bool(
                safe_payload.get("enable_browser_graphql", True)
            ),
            browser_timeout_seconds=max(
                10, int(safe_payload.get("browser_timeout_seconds", 45))
            ),
            browser_post_load_wait_seconds=max(
                0.0, float(safe_payload.get("browser_post_load_wait_seconds", 4.0))
            ),
            user_agent=str(
                safe_payload.get(
                    "user_agent", "AmazonJobRadar/5.0 (+polite public monitoring)"
                )
            ),
        ),
        email_monitor=EmailMonitorSettings(
            enabled=bool(email_payload.get("enabled", False)),
            input_paths=_as_string_list(email_payload.get("input_paths")),
            allowed_extensions=_as_string_list(
                email_payload.get("allowed_extensions"), [".eml", ".txt"]
            ),
            max_files_per_run=max(
                1, int(email_payload.get("max_files_per_run", 50))
            ),
        ),
        manual_assist=ManualAssistSettings(
            tracked_links_limit=max(
                1, int(manual_payload.get("tracked_links_limit", 5))
            ),
            guidance=str(
                manual_payload.get(
                    "guidance",
                    "Open the job in a normal browser. If Amazon asks for interactive "
                    "verification, complete it manually and then confirm the result in "
                    "the monitor.",
                )
            ),
        ),
    )

    if config.mode not in {
        MODE_SAFE_MONITOR,
        MODE_EMAIL_MONITOR,
        MODE_MANUAL_ASSIST,
    }:
        raise ValueError(
            "Unsupported mode in config: "
            f"{config.mode}. Expected safe_monitor, email_monitor, or manual_assist."
        )

    return config
