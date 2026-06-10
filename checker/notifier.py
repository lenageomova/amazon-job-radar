import html
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import MonitorConfig, TelegramConfig, load_config
from .models import AccessIssueAlert, NotificationEvent


logger = logging.getLogger(__name__)


@dataclass
class TelegramCheckResult:
    status: str
    message: str
    bot_username: Optional[str] = None
    chat_label: Optional[str] = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


def _telegram_api_url(token: str, method: str) -> str:
    return "https://api.telegram.org/bot%s/%s" % (token, method)


def _format_telegram_error(payload: dict) -> str:
    description = payload.get("description", "unknown Telegram error")
    error_code = payload.get("error_code")
    if error_code:
        return "%s (error code: %s)" % (description, error_code)
    return description


def _resolve_telegram_config(config: Optional[MonitorConfig]) -> TelegramConfig:
    if config is None:
        return load_config().telegram
    return config.telegram


def check_telegram_configuration(config: Optional[MonitorConfig] = None) -> TelegramCheckResult:
    telegram = _resolve_telegram_config(config)
    token = telegram.bot_token
    chat_id = telegram.chat_id

    if os.getenv("DRY_RUN") == "1" and not token and not chat_id:
        return TelegramCheckResult(
            status="ok",
            message="DRY_RUN=1, Telegram credentials are not required for this check",
        )

    missing = []
    if not token:
        missing.append(telegram.bot_token_env)
    if not chat_id:
        missing.append(telegram.chat_id_env)
    if missing:
        return TelegramCheckResult(
            status="missing-config",
            message="Missing required Telegram environment variables: %s"
            % ", ".join(missing),
        )

    try:
        me_response = requests.get(_telegram_api_url(token, "getMe"), timeout=10)
        me_response.raise_for_status()
        me_payload = me_response.json()
        if not me_payload.get("ok"):
            return TelegramCheckResult(
                status="error",
                message="Telegram getMe failed: %s"
                % _format_telegram_error(me_payload),
            )

        chat_response = requests.get(
            _telegram_api_url(token, "getChat"),
            params={"chat_id": chat_id},
            timeout=10,
        )
        chat_response.raise_for_status()
        chat_payload = chat_response.json()
        if not chat_payload.get("ok"):
            return TelegramCheckResult(
                status="error",
                message="Telegram getChat failed: %s"
                % _format_telegram_error(chat_payload),
            )

        bot_username = me_payload.get("result", {}).get("username")
        chat_result = chat_payload.get("result", {})
        chat_label = (
            chat_result.get("title")
            or chat_result.get("username")
            or str(chat_result.get("id", chat_id))
        )

        return TelegramCheckResult(
            status="ok",
            message="Telegram bot @%s can access chat %s" % (bot_username, chat_label),
            bot_username=bot_username,
            chat_label=chat_label,
        )
    except requests.exceptions.RequestException as error:
        return TelegramCheckResult(
            status="error",
            message="Telegram connectivity check failed: %s" % error,
        )
    except ValueError as error:
        return TelegramCheckResult(
            status="error",
            message="Telegram returned an invalid JSON payload: %s" % error,
        )


def _status_label(event_type: str) -> str:
    return {
        "new": "NEW",
        "still_active": "STILL ACTIVE",
        "changed": "CHANGED",
        "closed": "CLOSED",
        "manual_check_required": "MANUAL CHECK",
    }.get(event_type, event_type.upper())


def _format_job_message(event: NotificationEvent) -> str:
    changed = ""
    if event.changed_fields:
        changed = "\nChanged: %s" % ", ".join(event.changed_fields)

    raw = event.job.raw or {}
    detail_lines = []
    if raw.get("posting_status"):
        detail_lines.append("Posting: %s" % raw.get("posting_status"))
    if raw.get("schedule_count") is not None:
        availability = "yes" if raw.get("schedule_available") else "no"
        detail_lines.append(
            "Schedules: %s (available: %s)"
            % (raw.get("schedule_count"), availability)
        )
    if raw.get("pay_range"):
        detail_lines.append("Pay: %s" % raw.get("pay_range"))
    if raw.get("site_ids"):
        detail_lines.append("Site: %s" % ", ".join(raw.get("site_ids") or []))
    if raw.get("most_recent_unposted_date"):
        detail_lines.append("Last unposted: %s" % raw.get("most_recent_unposted_date"))
    details = ""
    if detail_lines:
        details = "\n<b>Details:</b> %s" % html.escape("; ".join(detail_lines))

    return (
        "<b>Amazon Hiring Canada</b>\n"
        "<b>Status:</b> %s\n"
        "<b>Urgency:</b> %s\n"
        "<b>Title:</b> %s\n"
        "<b>Location:</b> %s\n"
        "<b>Job ID:</b> <code>%s</code>\n"
        "<b>Detected:</b> %s\n"
        "<b>Next step:</b> %s\n"
        "<b>Source:</b> %s\n"
        "<b>Reason:</b> %s%s%s"
    ) % (
        html.escape(_status_label(event.event_type)),
        html.escape(event.urgency),
        html.escape(event.job.title),
        html.escape(event.job.location),
        html.escape(event.job.job_id),
        html.escape(event.detected_at),
        html.escape(event.next_action),
        html.escape(event.job.source),
        html.escape(event.reason),
        details,
        html.escape(changed),
    )


def _format_access_issue_message(alert: AccessIssueAlert) -> str:
    links_text = ""
    if alert.quick_links:
        links_text = "\n\nQuick links:\n" + "\n".join(alert.quick_links)

    return (
        "<b>Amazon Hiring Canada</b>\n"
        "<b>Status:</b> MANUAL CHECK\n"
        "<b>Detected:</b> %s\n"
        "<b>Issue:</b> %s%s"
    ) % (
        html.escape(alert.detected_at),
        html.escape(alert.message),
        html.escape(links_text),
    )


def _send_payload(token: str, payload: dict) -> bool:
    if os.getenv("DRY_RUN") == "1":
        logger.info("DRY_RUN=1, not sending Telegram message: %s", payload.get("text"))
        return True

    for attempt in range(1, 4):
        try:
            response = requests.post(
                _telegram_api_url(token, "sendMessage"),
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError(_format_telegram_error(body))
            return True
        except Exception as error:
            logger.error("Telegram send failed (attempt %s): %s", attempt, error)
            if attempt < 3:
                time.sleep(5)
    return False


def send_telegram_event(event: NotificationEvent, config: Optional[MonitorConfig] = None) -> bool:
    monitor_config = config or load_config()
    telegram = monitor_config.telegram
    payload = {
        "chat_id": telegram.chat_id,
        "text": _format_job_message(event),
        "parse_mode": "HTML",
        "disable_web_page_preview": telegram.disable_web_page_preview,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "Open job", "url": event.job.url}],
            ]
        },
    }
    return _send_payload(telegram.bot_token, payload)


def send_access_issue_alert(
    alert: AccessIssueAlert,
    config: Optional[MonitorConfig] = None,
) -> bool:
    monitor_config = config or load_config()
    telegram = monitor_config.telegram
    payload = {
        "chat_id": telegram.chat_id,
        "text": _format_access_issue_message(alert),
        "parse_mode": "HTML",
        "disable_web_page_preview": telegram.disable_web_page_preview,
    }
    return _send_payload(telegram.bot_token, payload)
