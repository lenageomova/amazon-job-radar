import unittest
from unittest.mock import patch

from checker.jobs_api import FetchResult, STATUS_OK
from checker.main import EXIT_ALERT_FAILURE, EXIT_OK, run_checker


class MainTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "123"},
        clear=False,
    )
    @patch("checker.main.check_telegram_configuration")
    @patch("checker.main.fetch_calgary_jobs")
    @patch("checker.main.send_telegram_alert")
    @patch("checker.main.save_seen_jobs")
    @patch("checker.main.load_seen_jobs")
    def test_failed_alerts_are_not_marked_seen(
        self,
        mock_load_seen_jobs,
        mock_save_seen_jobs,
        mock_send_telegram_alert,
        mock_fetch_calgary_jobs,
        mock_check_telegram_configuration,
    ):
        mock_load_seen_jobs.return_value = set()
        mock_check_telegram_configuration.return_value = type(
            "TelegramStatus",
            (),
            {"is_ok": True, "message": "ok"},
        )()
        mock_fetch_calgary_jobs.return_value = FetchResult(
            status=STATUS_OK,
            jobs=[{"jobId": "JOB-1", "title": "Warehouse Associate", "city": "Calgary", "state": "AB"}],
        )
        mock_send_telegram_alert.return_value = False

        exit_code = run_checker()

        self.assertEqual(exit_code, EXIT_ALERT_FAILURE)
        mock_save_seen_jobs.assert_called_once_with(set())

    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "123"},
        clear=False,
    )
    @patch("checker.main.check_telegram_configuration")
    @patch("checker.main.fetch_calgary_jobs")
    @patch("checker.main.send_telegram_alert")
    @patch("checker.main.save_seen_jobs")
    @patch("checker.main.load_seen_jobs")
    def test_successful_alerts_are_marked_seen(
        self,
        mock_load_seen_jobs,
        mock_save_seen_jobs,
        mock_send_telegram_alert,
        mock_fetch_calgary_jobs,
        mock_check_telegram_configuration,
    ):
        mock_load_seen_jobs.return_value = set()
        mock_check_telegram_configuration.return_value = type(
            "TelegramStatus",
            (),
            {"is_ok": True, "message": "ok"},
        )()
        mock_fetch_calgary_jobs.return_value = FetchResult(
            status=STATUS_OK,
            jobs=[{"jobId": "JOB-2", "title": "Warehouse Associate", "city": "Calgary", "state": "AB"}],
        )
        mock_send_telegram_alert.return_value = True

        exit_code = run_checker()

        self.assertEqual(exit_code, EXIT_OK)
        mock_save_seen_jobs.assert_called_once_with({"JOB-2"})


if __name__ == "__main__":
    unittest.main()
