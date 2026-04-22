import hashlib
import json
import os


SEEN_JOBS_FILE = "data/seen_jobs.json"


def load_seen_jobs() -> set[str]:
    """Load previously seen job identifiers from disk."""
    if not os.path.exists(SEEN_JOBS_FILE):
        return set()

    try:
        with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, KeyError):
        return set()


def save_seen_jobs(seen_ids: set[str]) -> None:
    """Persist job identifiers for deduplication."""
    os.makedirs("data", exist_ok=True)
    with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as file:
        json.dump({"seen_ids": sorted(seen_ids)}, file, indent=2)


def resolve_job_id(job: dict) -> str:
    """Return a stable job identifier, falling back to a content fingerprint."""
    for key in ("jobId", "id", "requisitionId"):
        value = job.get(key)
        if value:
            return str(value)

    fingerprint = hashlib.md5(
        (
            f"{job.get('title', '')}|"
            f"{job.get('city', '')}|"
            f"{job.get('postalCode', '')}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"fp_{fingerprint}"
