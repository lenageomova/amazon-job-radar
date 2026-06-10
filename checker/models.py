import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List


MODE_SAFE_MONITOR = "safe_monitor"
MODE_EMAIL_MONITOR = "email_monitor"
MODE_MANUAL_ASSIST = "manual_assist"

SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_BLOCKED = "blocked"
SOURCE_STATUS_ERROR = "error"

JOB_STATUS_ACTIVE = "active"
JOB_STATUS_CLOSED = "closed"
JOB_STATUS_MANUAL = "manual_check_required"

EVENT_NEW = "new"
EVENT_STILL_ACTIVE = "still_active"
EVENT_CHANGED = "changed"
EVENT_CLOSED = "closed"
EVENT_MANUAL_CHECK = "manual_check_required"


def build_job_fingerprint(
    job_id: str,
    title: str,
    location: str,
    url: str,
    summary: str = "",
) -> str:
    payload = "|".join(
        [
            str(job_id).strip().lower(),
            str(title).strip().lower(),
            str(location).strip().lower(),
            str(url).strip().lower(),
            str(summary).strip().lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class JobRecord:
    job_id: str
    title: str
    location: str
    url: str
    source: str
    city: str = ""
    region: str = ""
    summary: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.summary:
            self.summary = self.title
        if not self.fingerprint:
            self.fingerprint = build_job_fingerprint(
                self.job_id,
                self.title,
                self.location,
                self.url,
                self.summary,
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "city": self.city,
            "region": self.region,
            "summary": self.summary,
            "raw": self.raw,
            "fingerprint": self.fingerprint,
        }


@dataclass
class SourceFetchResult:
    status: str
    jobs: List[JobRecord] = field(default_factory=list)
    inactive_jobs: List[JobRecord] = field(default_factory=list)
    message: str = ""
    errors: List[str] = field(default_factory=list)
    request_count: int = 0
    inventory_complete: bool = False

    @property
    def is_ok(self) -> bool:
        return self.status == SOURCE_STATUS_OK


@dataclass
class NotificationEvent:
    event_type: str
    job: JobRecord
    detected_at: str
    reason: str
    urgency: str
    next_action: str
    changed_fields: List[str] = field(default_factory=list)


@dataclass
class AccessIssueAlert:
    message: str
    detected_at: str
    quick_links: List[str] = field(default_factory=list)
