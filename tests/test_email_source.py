import tempfile
import unittest

from checker.config import EmailMonitorSettings, MonitorConfig, StorageConfig
from checker.sources.email_alerts import EmailAlertSource
from checker.storage import JobStateStore


class EmailSourceTests(unittest.TestCase):
    def test_extracts_job_id_from_eml_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            email_path = f"{temp_dir}/alert.eml"
            with open(email_path, "w", encoding="utf-8") as file:
                file.write(
                    "Subject: Amazon Job Alert\r\n"
                    "Message-ID: <abc@example.com>\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    "\r\n"
                    "Title: Warehouse Associate\r\n"
                    "Location: Calgary, Alberta, Canada\r\n"
                    "Apply here: https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000441&locale=en-CA\r\n"
                )

            config = MonitorConfig(
                email_monitor=EmailMonitorSettings(
                    enabled=True,
                    input_paths=[temp_dir],
                    allowed_extensions=[".eml"],
                    max_files_per_run=10,
                ),
                storage=StorageConfig(
                    sqlite_path=f"{temp_dir}/job_radar.sqlite",
                    seen_jobs_json_path=f"{temp_dir}/seen_jobs.json",
                    log_file_path=f"{temp_dir}/monitor.log",
                ),
            )
            store = JobStateStore(
                sqlite_path=config.storage.sqlite_path,
                seen_jobs_json_path=config.storage.seen_jobs_json_path,
            )

            result = EmailAlertSource(config, store).fetch()

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0].job_id, "JOB-CA-0000000441")


if __name__ == "__main__":
    unittest.main()
