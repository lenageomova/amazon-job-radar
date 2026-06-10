import tempfile
import unittest
from unittest.mock import patch

from checker.config import MonitorConfig, StorageConfig
from checker.models import JobRecord, SourceFetchResult
from checker.monitor import MonitorService


class MonitorFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = MonitorConfig(
            notification_cooldown_minutes=5,
            still_active_reminder_minutes=0,
            close_after_missed_runs=1,
            storage=StorageConfig(
                sqlite_path=f"{self.temp_dir.name}/job_radar.sqlite",
                seen_jobs_json_path=f"{self.temp_dir.name}/seen_jobs.json",
                log_file_path=f"{self.temp_dir.name}/monitor.log",
            ),
        )

    @patch("checker.monitor.send_telegram_event", return_value=True)
    @patch("checker.monitor.send_access_issue_alert", return_value=True)
    def test_new_job_then_closed_without_duplicate_new_alerts(
        self,
        _mock_access_issue,
        _mock_send_telegram_event,
    ):
        service = MonitorService(self.config)
        job = JobRecord(
            job_id="JOB-CA-0000000441",
            title="Warehouse Associate",
            location="Calgary, Alberta, Canada",
            city="Calgary",
            region="AB",
            url="https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000441&locale=en-CA",
            source="amazon_public_search",
        )

        with patch.object(
            MonitorService,
            "_collect_source_results",
            return_value=(
                [SourceFetchResult(status="ok", jobs=[job], message="ok", inventory_complete=True)],
                {job.job_id: job},
                "ok",
                True,
            ),
        ):
            first_summary = service.run()

        with patch.object(
            MonitorService,
            "_collect_source_results",
            return_value=(
                [SourceFetchResult(status="ok", jobs=[job], message="ok", inventory_complete=True)],
                {job.job_id: job},
                "ok",
                True,
            ),
        ):
            second_summary = service.run()

        with patch.object(
            MonitorService,
            "_collect_source_results",
            return_value=(
                [SourceFetchResult(status="ok", jobs=[], message="ok", inventory_complete=True)],
                {},
                "ok",
                True,
            ),
        ):
            third_summary = service.run()

        self.assertEqual(first_summary["sent_events"], 1)
        self.assertEqual(second_summary["sent_events"], 0)
        self.assertEqual(third_summary["sent_events"], 1)

        row = service.store.get_job(job.job_id)
        self.assertEqual(row["last_status"], "closed")

    @patch("checker.monitor.send_telegram_event", return_value=True)
    @patch("checker.monitor.send_access_issue_alert", return_value=True)
    def test_access_issue_alert_sent_when_no_reliable_source(
        self,
        mock_access_issue,
        _mock_send_telegram_event,
    ):
        self.config.seed_job_ids = ["JOB-CA-0000000438"]
        service = MonitorService(self.config)

        with patch.object(
            MonitorService,
            "_collect_source_results",
            return_value=(
                [SourceFetchResult(status="blocked", jobs=[], message="blocked", errors=["CloudFront blocked"], inventory_complete=False)],
                {},
                "blocked",
                False,
            ),
        ):
            summary = service.run()

        self.assertTrue(summary["access_alert_sent"])
        mock_access_issue.assert_called_once()

    @patch("checker.monitor.send_telegram_event", return_value=True)
    @patch("checker.monitor.send_access_issue_alert", return_value=True)
    def test_explicit_unposted_detail_marks_existing_job_closed(
        self,
        _mock_access_issue,
        _mock_send_telegram_event,
    ):
        service = MonitorService(self.config)
        active_job = JobRecord(
            job_id="JOB-CA-0000000552",
            title="Amazon Delivery Station Warehouse Associate",
            location="Calgary, AB",
            city="Calgary",
            region="AB",
            url="https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000552&locale=en-CA",
            source="amazon_graphql",
            raw={"posting_status": "POSTED", "schedule_count": 1},
        )
        inactive_job = JobRecord(
            job_id="JOB-CA-0000000552",
            title="Amazon Delivery Station Warehouse Associate",
            location="Calgary, AB",
            city="Calgary",
            region="AB",
            url="https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000552&locale=en-CA",
            source="amazon_graphql",
            raw={"posting_status": "UNPOSTED", "schedule_count": 0},
        )
        service.store.upsert_job(
            active_job,
            seen_at="2026-06-10T00:00:00+00:00",
            status="active",
        )

        with patch.object(
            MonitorService,
            "_collect_source_results",
            return_value=(
                [
                    SourceFetchResult(
                        status="ok",
                        jobs=[],
                        inactive_jobs=[inactive_job],
                        message="ok",
                        inventory_complete=False,
                    )
                ],
                {},
                "ok",
                False,
            ),
        ):
            summary = service.run()

        self.assertEqual(summary["sent_events"], 1)
        row = service.store.get_job("JOB-CA-0000000552")
        self.assertEqual(row["last_status"], "closed")


if __name__ == "__main__":
    unittest.main()
