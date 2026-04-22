import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

try:
    from .storage import resolve_job_id
except ImportError:
    from storage import resolve_job_id


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
    return f"https://api.telegram.org/bot{token}/{method}"


def _format_telegram_error(payload: dict) -> str:
    description = payload.get("description", "unknown Telegram error")
    error_code = payload.get("error_code")
    if error_code:
        return f"{description} (error code: {error_code})"
    return description


def check_telegram_configuration() -> TelegramCheckResult:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        return TelegramCheckResult(
            status="missing-config",
            message=f"Missing required Telegram environment variables: {', '.join(missing)}",
        )

    try:
        me_response = requests.get(_telegram_api_url(token, "getMe"), timeout=10)
        me_response.raise_for_status()
        me_payload = me_response.json()
        if not me_payload.get("ok"):
            return TelegramCheckResult(
                status="error",
                message=f"Telegram getMe failed: {_format_telegram_error(me_payload)}",
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
                message=f"Telegram getChat failed: {_format_telegram_error(chat_payload)}",
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
            message=f"Telegram bot @{bot_username} can access chat {chat_label}",
            bot_username=bot_username,
            chat_label=chat_label,
        )
    except requests.exceptions.RequestException as error:
        return TelegramCheckResult(
            status="error",
            message=f"Telegram connectivity check failed: {error}",
        )
    except ValueError as error:
        return TelegramCheckResult(
            status="error",
            message=f"Telegram returned an invalid JSON payload: {error}",
        )


def send_telegram_alert(job: dict) -> bool:
    """Send a Telegram alert with retries."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    title = job.get("title", "Unknown position")
    city = job.get("city", "")
    state = job.get("state", "")
    job_id = job.get("resolvedJobId") or resolve_job_id(job)
    job_url = (
        f"https://hiring.amazon.com/jobs/{job_id}"
        if job_id and not job_id.startswith("fp_")
        else "https://hiring.amazon.com"
    )

    message = (
        "Amazon job alert\n\n"
        f"Title: {title}\n"
        f"Location: {city}, {state}\n"
        f"Job ID: {job_id}\n"
        f"Apply: {job_url}"
    )

    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }

    for attempt in range(3):
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

            logger.info("Telegram alert sent for job: %s", job_id)
            return True
        except Exception as error:
            logger.error("Telegram send failed (attempt %s): %s", attempt + 1, error)
            if attempt < 2:
                time.sleep(5)

    logger.error("Fallback only; job alert not sent: %s | %s | %s", title, city, job_url)
    return False
