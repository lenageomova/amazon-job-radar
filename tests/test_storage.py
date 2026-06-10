import json
import tempfile
import unittest

from checker.models import JobRecord
from checker.storage import JobStateStore, resolve_job_id


class StorageTests(unittest.TestCase):
    def test_resolve_job_id_uses_explicit_identifier_first(self):
        job = {"jobId": "JOB123", "id": "fallback"}
        self.assertEqual(resolve_job_id(job), "JOB123")

    def test_resolve_job_id_uses_fingerprint_when_identifiers_missing(self):
        job = {
            "title": "Warehouse Associate",
            "city": "Calgary",
            "postalCode": "T1X0L3",
        }
        resolved_id = resolve_job_id(job)
        self.assertTrue(resolved_id.startswith("fp_"))
        self.assertEqual(len(resolved_id), 15)

    def test_store_exports_seen_jobs_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JobStateStore(
                sqlite_path=f"{temp_dir}/job_radar.sqlite",
                seen_jobs_json_path=f"{temp_dir}/seen_jobs.json",
            )
            store.upsert_job(
                JobRecord(
                    job_id="JOB-CA-0000000443",
                    title="Sortation Associate",
                    location="Calgary, Alberta, Canada",
                    city="Calgary",
                    region="AB",
                    url="https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000443&locale=en-CA",
                    source="amazon_public_search",
                ),
                seen_at="2026-05-08T00:00:00+00:00",
            )
            store.export_seen_jobs_snapshot()

            with open(f"{temp_dir}/seen_jobs.json", "r", encoding="utf-8") as file:
                snapshot = json.load(file)

        self.assertEqual(snapshot["seen_ids"], ["JOB-CA-0000000443"])
        self.assertEqual(snapshot["jobs"][0]["status"], "active")


if __name__ == "__main__":
    unittest.main()
