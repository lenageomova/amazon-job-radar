import email
import hashlib
import html
import os
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable, List

from ..config import MonitorConfig
from ..models import SOURCE_STATUS_OK, JobRecord, SourceFetchResult
from ..storage import JobStateStore, canonical_job_url


JOB_ID_PATTERN = re.compile(r"(JOB-[A-Z]{2}-\d+)", re.IGNORECASE)
URL_PATTERN = re.compile(
    r"https://hiring\.amazon\.ca/app#/jobDetail\?jobId=(JOB-[A-Z]{2}-\d+)[^\s\"'<]*",
    re.IGNORECASE,
)
TITLE_PATTERN = re.compile(r"(?:job title|title)\s*:\s*(.+)", re.IGNORECASE)
LOCATION_PATTERN = re.compile(r"(?:job location|location)\s*:\s*(.+)", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")


def _iter_candidate_files(paths: Iterable[str], allowed_extensions: List[str]) -> List[Path]:
    results: List[Path] = []
    lowered_extensions = {item.lower() for item in allowed_extensions}

    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_file() and path.suffix.lower() in lowered_extensions:
            results.append(path)
            continue

        if not path.exists() or not path.is_dir():
            continue

        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in lowered_extensions:
                results.append(child)

    return results


def _extract_text_parts(message: email.message.Message) -> List[str]:
    texts = []
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            texts.append(decoded)
    else:
        payload = message.get_payload(decode=True) or b""
        charset = message.get_content_charset() or "utf-8"
        try:
            texts.append(payload.decode(charset, errors="replace"))
        except LookupError:
            texts.append(payload.decode("utf-8", errors="replace"))

    return texts


def _clean_text(parts: List[str]) -> str:
    if not parts:
        return ""

    combined = "\n".join(parts)
    if "<html" in combined.lower() or "<body" in combined.lower():
        combined = TAG_PATTERN.sub(" ", combined)
    combined = html.unescape(combined)
    combined = re.sub(r"[ \t]+", " ", combined)
    combined = re.sub(r"\n{3,}", "\n\n", combined)
    return combined.strip()


def _extract_message_key(path: Path, message: email.message.Message, body_text: str) -> str:
    message_id = str(message.get("Message-ID", "")).strip()
    if message_id:
        return message_id
    payload = "%s|%s|%s" % (path, message.get("Subject", ""), body_text[:200])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_title(subject: str, body_text: str) -> str:
    match = TITLE_PATTERN.search(body_text)
    if match:
        return match.group(1).strip()
    return subject.strip() or "Amazon Hiring Canada opportunity"


def _extract_location(body_text: str) -> str:
    match = LOCATION_PATTERN.search(body_text)
    if match:
        return match.group(1).strip()

    for line in body_text.splitlines():
        lowered = line.lower()
        if "calgary" in lowered or "balzac" in lowered or "airdrie" in lowered:
            return line.strip()

    return "Location not parsed from email"


def _job_records_from_email(subject: str, body_text: str) -> List[JobRecord]:
    job_ids = []
    for match in URL_PATTERN.findall(body_text):
        job_ids.append(match.upper())
    for match in JOB_ID_PATTERN.findall(body_text):
        normalized = match.upper()
        if normalized not in job_ids:
            job_ids.append(normalized)

    title = _extract_title(subject, body_text)
    location = _extract_location(body_text)

    jobs = []
    for job_id in job_ids:
        jobs.append(
            JobRecord(
                job_id=job_id,
                title=title,
                location=location,
                city="",
                region="",
                url=canonical_job_url(job_id),
                source="amazon_email_alert",
                summary=body_text[:280],
                raw={"subject": subject, "body_preview": body_text[:500]},
            )
        )
    return jobs


class EmailAlertSource:
    def __init__(self, config: MonitorConfig, store: JobStateStore):
        self.config = config
        self.store = store

    def fetch(self) -> SourceFetchResult:
        settings = self.config.email_monitor
        if not settings.enabled:
            return SourceFetchResult(
                status=SOURCE_STATUS_OK,
                jobs=[],
                message="Email monitor disabled",
                inventory_complete=False,
            )

        files = _iter_candidate_files(settings.input_paths, settings.allowed_extensions)
        jobs_by_id = {}
        errors = []
        processed_count = 0

        for path in files[: settings.max_files_per_run]:
            try:
                with open(path, "rb") as file:
                    message = BytesParser(policy=policy.default).parse(file)
                subject = str(message.get("Subject", "")).strip()
                body_text = _clean_text(_extract_text_parts(message))
                message_key = _extract_message_key(path, message, body_text)
                if self.store.was_message_processed(message_key):
                    continue

                for job in _job_records_from_email(subject, body_text):
                    jobs_by_id[job.job_id] = job

                self.store.mark_message_processed(
                    message_key=message_key,
                    source_path=str(path),
                    processed_at=_now_utc(),
                )
                processed_count += 1
            except Exception as error:
                errors.append("%s: %s" % (path, error))

        message = "Parsed %s email files and extracted %s job IDs" % (
            processed_count,
            len(jobs_by_id),
        )
        return SourceFetchResult(
            status=SOURCE_STATUS_OK,
            jobs=list(jobs_by_id.values()),
            message=message,
            errors=errors,
            inventory_complete=False,
        )


def _now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
