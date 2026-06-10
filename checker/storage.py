import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Set

from .models import JOB_STATUS_ACTIVE, JOB_STATUS_CLOSED, JobRecord


SEEN_JOBS_FILE = "data/seen_jobs.json"
JOB_ID_PATTERN = re.compile(r"(JOB-[A-Z]{2}-\d+)", re.IGNORECASE)


def load_seen_jobs(seen_jobs_file: str = SEEN_JOBS_FILE) -> Set[str]:
    if not os.path.exists(seen_jobs_file):
        return set()

    try:
        with open(seen_jobs_file, "r", encoding="utf-8") as file:
            data = json.load(file)
            return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, KeyError):
        return set()


def save_seen_jobs(seen_ids: Set[str], seen_jobs_file: str = SEEN_JOBS_FILE) -> None:
    directory = os.path.dirname(seen_jobs_file) or "."
    os.makedirs(directory, exist_ok=True)
    with open(seen_jobs_file, "w", encoding="utf-8") as file:
        json.dump({"seen_ids": sorted(seen_ids)}, file, indent=2)


def resolve_job_id(job: Dict) -> str:
    for key in ("jobId", "job_id", "id", "requisitionId", "jobID", "job_id_text"):
        value = job.get(key)
        if value:
            return str(value)

    for key in ("url", "jobUrl", "href", "job_path", "link"):
        value = job.get(key)
        if not value:
            continue
        match = JOB_ID_PATTERN.search(str(value))
        if match:
            return match.group(1).upper()

    fingerprint = hashlib.md5(
        (
            f"{job.get('title', '')}|"
            f"{job.get('location', '')}|"
            f"{job.get('city', '')}|"
            f"{job.get('postalCode', '')}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    return "fp_" + fingerprint


def canonical_job_url(job_id: str) -> str:
    return (
        "https://hiring.amazon.ca/app#/jobDetail"
        f"?jobId={job_id}&locale=en-CA"
    )


class JobStateStore:
    def __init__(self, sqlite_path: str, seen_jobs_json_path: str = SEEN_JOBS_FILE):
        self.sqlite_path = sqlite_path
        self.seen_jobs_json_path = seen_jobs_json_path
        directory = os.path.dirname(sqlite_path) or "."
        os.makedirs(directory, exist_ok=True)
        self.ensure_schema()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    location TEXT NOT NULL,
                    city TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_status TEXT NOT NULL,
                    last_changed_at TEXT NOT NULL,
                    missed_runs INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    payload_hash TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_notifications_job_event
                ON notifications (job_id, event_type, sent_at DESC);

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    source_status TEXT NOT NULL,
                    observed_count INTEGER NOT NULL DEFAULT 0,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    error_json TEXT NOT NULL DEFAULT '[]',
                    note TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );
                """
            )

    def get_job(self, job_id: str) -> Optional[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

    def list_non_closed_jobs(self) -> List[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE last_status != ? ORDER BY last_seen_at DESC",
                (JOB_STATUS_CLOSED,),
            ).fetchall()

    def list_recent_jobs(self, limit: int = 10) -> List[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM jobs ORDER BY last_seen_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def upsert_job(
        self,
        job: JobRecord,
        seen_at: str,
        status: str = JOB_STATUS_ACTIVE,
        missed_runs: int = 0,
        notes: str = "",
    ) -> None:
        existing = self.get_job(job.job_id)
        raw_json = json.dumps(job.raw, sort_keys=True)

        if existing is None:
            with self.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, title, location, city, region, url, source,
                        fingerprint, raw_json, first_seen_at, last_seen_at,
                        last_status, last_changed_at, missed_runs, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.title,
                        job.location,
                        job.city,
                        job.region,
                        job.url,
                        job.source,
                        job.fingerprint,
                        raw_json,
                        seen_at,
                        seen_at,
                        status,
                        seen_at,
                        missed_runs,
                        notes,
                    ),
                )
            return

        last_changed_at = existing["last_changed_at"]
        if (
            existing["fingerprint"] != job.fingerprint
            or existing["last_status"] != status
            or str(existing["title"]) != job.title
            or str(existing["location"]) != job.location
        ):
            last_changed_at = seen_at

        with self.connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET title = ?,
                    location = ?,
                    city = ?,
                    region = ?,
                    url = ?,
                    source = ?,
                    fingerprint = ?,
                    raw_json = ?,
                    last_seen_at = ?,
                    last_status = ?,
                    last_changed_at = ?,
                    missed_runs = ?,
                    notes = ?
                WHERE job_id = ?
                """,
                (
                    job.title,
                    job.location,
                    job.city,
                    job.region,
                    job.url,
                    job.source,
                    job.fingerprint,
                    raw_json,
                    seen_at,
                    status,
                    last_changed_at,
                    missed_runs,
                    notes or existing["notes"],
                    job.job_id,
                ),
            )

    def update_missed_runs(self, job_id: str, missed_runs: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE jobs SET missed_runs = ? WHERE job_id = ?",
                (missed_runs, job_id),
            )

    def mark_closed(self, job_id: str, seen_at: str, notes: str = "") -> None:
        existing = self.get_job(job_id)
        if existing is None:
            return

        with self.connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET last_status = ?,
                    last_changed_at = ?,
                    missed_runs = ?,
                    notes = ?
                WHERE job_id = ?
                """,
                (
                    JOB_STATUS_CLOSED,
                    seen_at,
                    existing["missed_runs"],
                    notes or existing["notes"],
                    job_id,
                ),
            )

    def record_manual_confirmation(
        self,
        job_id: str,
        status: str,
        confirmed_at: str,
        title: str = "",
        location: str = "",
        notes: str = "",
    ) -> None:
        existing = self.get_job(job_id)
        if existing is None:
            placeholder = JobRecord(
                job_id=job_id,
                title=title or "Manual confirmation",
                location=location or "Manual check",
                url=canonical_job_url(job_id),
                source="manual_confirmation",
                summary=notes or "Confirmed manually",
                raw={"manual": True, "notes": notes},
            )
            self.upsert_job(
                placeholder,
                seen_at=confirmed_at,
                status=status,
                missed_runs=0,
                notes=notes,
            )
            return

        with self.connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET last_status = ?,
                    last_seen_at = ?,
                    last_changed_at = ?,
                    missed_runs = 0,
                    notes = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    confirmed_at,
                    confirmed_at,
                    notes or existing["notes"],
                    job_id,
                ),
            )

    def last_notification_at(
        self,
        job_id: str,
        event_type: Optional[str] = None,
    ) -> Optional[str]:
        query = "SELECT sent_at FROM notifications WHERE job_id = ?"
        params: List[str] = [job_id]
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY sent_at DESC LIMIT 1"

        with self.connection() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
            return row["sent_at"] if row else None

    def record_notification(
        self,
        job_id: str,
        event_type: str,
        sent_at: str,
        payload_hash: str = "",
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO notifications (job_id, event_type, sent_at, payload_hash)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, event_type, sent_at, payload_hash),
            )

    def was_message_processed(self, message_key: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_key = ?",
                (message_key,),
            ).fetchone()
            return row is not None

    def mark_message_processed(
        self,
        message_key: str,
        source_path: str,
        processed_at: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_messages (
                    message_key,
                    source_path,
                    processed_at
                ) VALUES (?, ?, ?)
                """,
                (message_key, source_path, processed_at),
            )

    def record_run(
        self,
        started_at: str,
        finished_at: str,
        mode: str,
        source_status: str,
        observed_count: int,
        event_count: int,
        errors: Iterable[str],
        note: str = "",
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    started_at,
                    finished_at,
                    mode,
                    source_status,
                    observed_count,
                    event_count,
                    error_json,
                    note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at,
                    finished_at,
                    mode,
                    source_status,
                    observed_count,
                    event_count,
                    json.dumps(list(errors)),
                    note,
                ),
            )

    def export_seen_jobs_snapshot(self) -> None:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT job_id, title, location, url, last_status, first_seen_at,
                       last_seen_at, raw_json, notes
                FROM jobs
                ORDER BY first_seen_at ASC
                """
            ).fetchall()

        seen_ids = [row["job_id"] for row in rows]
        jobs = []
        for row in rows:
            try:
                raw = json.loads(row["raw_json"] or "{}")
            except json.JSONDecodeError:
                raw = {}
            jobs.append(
                {
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "location": row["location"],
                    "url": row["url"],
                    "status": row["last_status"],
                    "posting_status": raw.get("posting_status"),
                    "schedule_count": raw.get("schedule_count"),
                    "schedule_available": raw.get("schedule_available"),
                    "pay_range": raw.get("pay_range"),
                    "site_ids": raw.get("site_ids"),
                    "most_recent_posted_date": raw.get("most_recent_posted_date"),
                    "most_recent_unposted_date": raw.get("most_recent_unposted_date"),
                    "first_seen_at": row["first_seen_at"],
                    "last_seen_at": row["last_seen_at"],
                    "notes": row["notes"],
                }
            )
        payload = {
            "seen_ids": seen_ids,
            "jobs": jobs,
        }

        directory = os.path.dirname(self.seen_jobs_json_path) or "."
        os.makedirs(directory, exist_ok=True)
        with open(self.seen_jobs_json_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
