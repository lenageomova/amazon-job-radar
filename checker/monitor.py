import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .config import MonitorConfig
from .graphql_source import AmazonGraphQLSource
from .jobs_api import matches_monitor_filters
from .models import (
    AccessIssueAlert,
    EVENT_CHANGED,
    EVENT_CLOSED,
    EVENT_MANUAL_CHECK,
    EVENT_NEW,
    EVENT_STILL_ACTIVE,
    JOB_STATUS_ACTIVE,
    JOB_STATUS_CLOSED,
    MODE_EMAIL_MONITOR,
    MODE_MANUAL_ASSIST,
    NotificationEvent,
    SourceFetchResult,
    JobRecord,
)
from .notifier import send_access_issue_alert, send_telegram_event
from .sources.email_alerts import EmailAlertSource
from .storage import JobStateStore, canonical_job_url


logger = logging.getLogger(__name__)

ACCESS_ALERT_JOB_KEY = "__access_issue__"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _minutes_since(older: str, newer: str) -> float:
    return (_parse_timestamp(newer) - _parse_timestamp(older)).total_seconds() / 60.0


def _merge_job(existing: Optional[JobRecord], candidate: JobRecord) -> JobRecord:
    if existing is None:
        return candidate

    current_score = len(existing.title) + len(existing.location) + len(existing.summary)
    candidate_score = len(candidate.title) + len(candidate.location) + len(candidate.summary)
    return candidate if candidate_score >= current_score else existing


def _build_event(
    event_type: str,
    job: JobRecord,
    detected_at: str,
    reason: str,
    changed_fields: Optional[List[str]] = None,
) -> NotificationEvent:
    changed_fields = changed_fields or []
    schedule_available = bool(job.raw.get("schedule_available"))
    if event_type == EVENT_NEW:
        return NotificationEvent(
            event_type=event_type,
            job=job,
            detected_at=detected_at,
            reason=reason,
            urgency="Open immediately" if schedule_available else "Watch closely",
            next_action=(
                "Open the job now and start the application flow."
                if schedule_available
                else "Open the job and watch for schedules; the role is visible but may not have shifts yet."
            ),
            changed_fields=changed_fields,
        )
    if event_type == EVENT_CHANGED:
        return NotificationEvent(
            event_type=event_type,
            job=job,
            detected_at=detected_at,
            reason=reason,
            urgency="High" if schedule_available else "Monitor",
            next_action=(
                "Review the updated job page and apply if schedules are selectable."
                if schedule_available
                else "Review the updated job page; schedules may still be unavailable."
            ),
            changed_fields=changed_fields,
        )
    if event_type == EVENT_CLOSED:
        return NotificationEvent(
            event_type=event_type,
            job=job,
            detected_at=detected_at,
            reason=reason,
            urgency="Informational",
            next_action="Stop prioritizing this job unless it reappears later.",
            changed_fields=changed_fields,
        )
    return NotificationEvent(
        event_type=event_type,
        job=job,
        detected_at=detected_at,
        reason=reason,
        urgency="Monitor",
        next_action="Keep the quick link handy and reopen if it remains available.",
        changed_fields=changed_fields,
    )


def _changed_fields(existing_row, job: JobRecord) -> List[str]:
    fields = []
    if str(existing_row["title"]) != job.title:
        fields.append("title")
    if str(existing_row["location"]) != job.location:
        fields.append("location")
    if str(existing_row["url"]) != job.url:
        fields.append("url")
    if str(existing_row["fingerprint"]) != job.fingerprint and not fields:
        fields.append("details")
    return fields


def _notification_due(
    store: JobStateStore,
    job_id: str,
    event_type: str,
    now_iso: str,
    cooldown_minutes: int,
    fallback_to_any: bool = False,
) -> bool:
    last_sent = store.last_notification_at(job_id, event_type)
    if not last_sent and fallback_to_any:
        last_sent = store.last_notification_at(job_id)
    if not last_sent:
        return True
    return _minutes_since(last_sent, now_iso) >= cooldown_minutes


class MonitorService:
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.store = JobStateStore(
            sqlite_path=config.storage.sqlite_path,
            seen_jobs_json_path=config.storage.seen_jobs_json_path,
        )

    def _build_sources(self):
        sources = []
        if self.config.mode != MODE_EMAIL_MONITOR and self.config.mode != MODE_MANUAL_ASSIST:
            sources.append(AmazonGraphQLSource(self.config))
        if self.config.email_monitor.enabled:
            sources.append(EmailAlertSource(self.config, self.store))
        return sources

    def _collect_source_results(self) -> Tuple[List[SourceFetchResult], Dict[str, JobRecord], str, bool]:
        source_results = []
        merged_jobs: Dict[str, JobRecord] = {}
        inventory_complete = False

        for source in self._build_sources():
            result = source.fetch()
            source_results.append(result)
            inventory_complete = inventory_complete or (
                result.is_ok and result.inventory_complete
            )

            for job in result.jobs:
                if not matches_monitor_filters(job, self.config):
                    continue
                merged_jobs[job.job_id] = _merge_job(merged_jobs.get(job.job_id), job)

        if any(result.status == "ok" for result in source_results):
            source_status = "ok"
        elif any(result.status == "blocked" for result in source_results):
            source_status = "blocked"
        elif source_results:
            source_status = "error"
        else:
            source_status = "ok"

        return source_results, merged_jobs, source_status, inventory_complete

    def _active_events_for_observed_jobs(
        self,
        observed_jobs: Dict[str, JobRecord],
        observed_at: str,
    ) -> List[NotificationEvent]:
        events = []

        for job in observed_jobs.values():
            existing = self.store.get_job(job.job_id)
            if existing is None:
                self.store.upsert_job(job, seen_at=observed_at, status=JOB_STATUS_ACTIVE)
                events.append(
                    _build_event(
                        EVENT_NEW,
                        job,
                        observed_at,
                        "First time the monitor has seen this job ID.",
                    )
                )
                continue

            changed_fields = _changed_fields(existing, job)
            was_closed = existing["last_status"] == JOB_STATUS_CLOSED
            if changed_fields or was_closed:
                reason = (
                    "Job details changed since the previous observation."
                    if changed_fields
                    else "Job is visible again after being closed or manually confirmed."
                )
                self.store.upsert_job(job, seen_at=observed_at, status=JOB_STATUS_ACTIVE)
                events.append(
                    _build_event(
                        EVENT_CHANGED,
                        job,
                        observed_at,
                        reason,
                        changed_fields=changed_fields or ["status"],
                    )
                )
                continue

            self.store.upsert_job(job, seen_at=observed_at, status=JOB_STATUS_ACTIVE)
            if (
                self.config.still_active_reminder_minutes > 0
                and _notification_due(
                    self.store,
                    job.job_id,
                    EVENT_STILL_ACTIVE,
                    observed_at,
                    self.config.still_active_reminder_minutes,
                    fallback_to_any=True,
                )
            ):
                events.append(
                    _build_event(
                        EVENT_STILL_ACTIVE,
                        job,
                        observed_at,
                        "Job remains visible and unchanged after the reminder cooldown.",
                    )
                )

        return events

    def _closure_events_for_explicit_inactive_jobs(
        self,
        source_results: List[SourceFetchResult],
        observed_at: str,
    ) -> List[NotificationEvent]:
        events = []
        inactive_by_id: Dict[str, JobRecord] = {}
        seed_ids = set(self.config.seed_job_ids)

        for result in source_results:
            for job in result.inactive_jobs:
                if job.job_id not in seed_ids and not matches_monitor_filters(job, self.config):
                    continue
                inactive_by_id[job.job_id] = _merge_job(inactive_by_id.get(job.job_id), job)

        for job in inactive_by_id.values():
            existing = self.store.get_job(job.job_id)
            was_active = existing is not None and existing["last_status"] != JOB_STATUS_CLOSED
            self.store.upsert_job(
                job,
                seen_at=observed_at,
                status=JOB_STATUS_CLOSED,
                missed_runs=0,
                notes="Amazon GraphQL reports postingStatus=%s"
                % job.raw.get("posting_status", "inactive"),
            )
            if not was_active:
                continue
            events.append(
                _build_event(
                    EVENT_CLOSED,
                    job,
                    observed_at,
                    "Amazon job detail now reports postingStatus=%s."
                    % job.raw.get("posting_status", "inactive"),
                )
            )

        return events

    def _closure_events_for_missing_jobs(
        self,
        observed_jobs: Dict[str, JobRecord],
        observed_at: str,
    ) -> List[NotificationEvent]:
        events = []
        observed_ids = set(observed_jobs.keys())

        for row in self.store.list_non_closed_jobs():
            if row["job_id"] in observed_ids:
                continue

            missed_runs = int(row["missed_runs"]) + 1
            self.store.update_missed_runs(row["job_id"], missed_runs)
            if missed_runs < self.config.close_after_missed_runs:
                continue

            self.store.mark_closed(
                row["job_id"],
                seen_at=observed_at,
                notes="Closed after %s consecutive misses from a complete public source."
                % missed_runs,
            )
            events.append(
                _build_event(
                    EVENT_CLOSED,
                    JobRecord(
                        job_id=row["job_id"],
                        title=row["title"],
                        location=row["location"],
                        city=row["city"],
                        region=row["region"],
                        url=row["url"],
                        source=row["source"],
                        summary="Closed after consecutive misses.",
                    ),
                    observed_at,
                    "Job disappeared from the complete public result set for %s runs."
                    % missed_runs,
                )
            )

        return events

    def _build_access_issue_alert(
        self,
        observed_at: str,
        source_results: List[SourceFetchResult],
    ) -> Optional[AccessIssueAlert]:
        if not self.config.telegram.access_issue_alerts_enabled:
            return None

        issue_messages = []
        for result in source_results:
            if result.status in ("blocked", "error"):
                issue_messages.extend(result.errors or [result.message])

        if not issue_messages:
            return None

        quick_links = []
        for job_id in self.config.seed_job_ids:
            quick_links.append(canonical_job_url(job_id))

        if not quick_links:
            for row in self.store.list_recent_jobs(self.config.manual_assist.tracked_links_limit):
                quick_links.append(str(row["url"]))

        guidance = self.config.manual_assist.guidance
        message = "%s %s" % ("; ".join(issue_messages[:3]), guidance)
        return AccessIssueAlert(
            message=message,
            detected_at=observed_at,
            quick_links=quick_links[: self.config.manual_assist.tracked_links_limit],
        )

    def _send_job_events(self, events: List[NotificationEvent], observed_at: str) -> List[NotificationEvent]:
        sent_events = []
        remaining = self.config.max_notifications_per_run

        for event in events:
            if remaining <= 0:
                break

            if event.event_type == EVENT_NEW:
                due = _notification_due(
                    self.store,
                    event.job.job_id,
                    EVENT_NEW,
                    observed_at,
                    0,
                )
            elif event.event_type == EVENT_STILL_ACTIVE:
                due = _notification_due(
                    self.store,
                    event.job.job_id,
                    EVENT_STILL_ACTIVE,
                    observed_at,
                    self.config.still_active_reminder_minutes,
                    fallback_to_any=True,
                )
            else:
                due = _notification_due(
                    self.store,
                    event.job.job_id,
                    event.event_type,
                    observed_at,
                    self.config.notification_cooldown_minutes,
                )

            if not due:
                continue

            if send_telegram_event(event, self.config):
                payload_hash = hashlib.sha256(
                    ("%s|%s|%s" % (event.job.job_id, event.event_type, event.reason)).encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
                self.store.record_notification(
                    job_id=event.job.job_id,
                    event_type=event.event_type,
                    sent_at=observed_at,
                    payload_hash=payload_hash,
                )
                sent_events.append(event)
                remaining -= 1

        return sent_events

    def _send_access_alert_if_needed(
        self,
        alert: Optional[AccessIssueAlert],
        observed_at: str,
    ) -> bool:
        if alert is None:
            return False
        if not _notification_due(
            self.store,
            ACCESS_ALERT_JOB_KEY,
            EVENT_MANUAL_CHECK,
            observed_at,
            self.config.manual_action_cooldown_minutes,
        ):
            return False
        if send_access_issue_alert(alert, self.config):
            payload_hash = hashlib.sha256(alert.message.encode("utf-8")).hexdigest()[:16]
            self.store.record_notification(
                job_id=ACCESS_ALERT_JOB_KEY,
                event_type=EVENT_MANUAL_CHECK,
                sent_at=observed_at,
                payload_hash=payload_hash,
            )
            return True
        return False

    def run(self, send_notifications: bool = True) -> Dict[str, object]:
        started_at = utc_now_iso()
        source_results, observed_jobs, source_status, inventory_complete = self._collect_source_results()
        errors = []
        for result in source_results:
            errors.extend(result.errors)

        events = self._active_events_for_observed_jobs(observed_jobs, started_at)
        events.extend(
            self._closure_events_for_explicit_inactive_jobs(source_results, started_at)
        )
        if inventory_complete:
            events.extend(self._closure_events_for_missing_jobs(observed_jobs, started_at))

        events.sort(
            key=lambda item: {
                EVENT_NEW: 0,
                EVENT_CHANGED: 1,
                EVENT_STILL_ACTIVE: 2,
                EVENT_CLOSED: 3,
            }.get(item.event_type, 4)
        )
        sent_events = self._send_job_events(events, started_at) if send_notifications else []

        access_alert = None
        if not inventory_complete and source_status in ("blocked", "error"):
            access_alert = self._build_access_issue_alert(started_at, source_results)
        access_alert_sent = (
            self._send_access_alert_if_needed(access_alert, started_at)
            if send_notifications
            else False
        )

        self.store.export_seen_jobs_snapshot()
        finished_at = utc_now_iso()
        note = "; ".join(result.message for result in source_results if result.message)
        self.store.record_run(
            started_at=started_at,
            finished_at=finished_at,
            mode=self.config.mode,
            source_status=source_status,
            observed_count=len(observed_jobs),
            event_count=len(sent_events) + (1 if access_alert_sent else 0),
            errors=errors,
            note=note,
        )

        return {
            "started_at": started_at,
            "finished_at": finished_at,
            "mode": self.config.mode,
            "source_status": source_status,
            "observed_jobs": len(observed_jobs),
            "inventory_complete": inventory_complete,
            "planned_events": len(events),
            "sent_events": len(sent_events),
            "access_alert_sent": access_alert_sent,
            "errors": errors,
        }

    def manual_confirm(
        self,
        job_id: str,
        status: str,
        title: str = "",
        location: str = "",
        notes: str = "",
    ) -> Dict[str, str]:
        confirmed_at = utc_now_iso()
        self.store.record_manual_confirmation(
            job_id=job_id,
            status=status,
            confirmed_at=confirmed_at,
            title=title,
            location=location,
            notes=notes,
        )
        self.store.export_seen_jobs_snapshot()
        return {
            "job_id": job_id,
            "status": status,
            "confirmed_at": confirmed_at,
        }
