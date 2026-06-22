import unittest
from unittest.mock import Mock, patch

import requests

from checker.config import KeywordsConfig, LocationConfig, MonitorConfig
from checker.jobs_api import (
    STATUS_BLOCKED,
    STATUS_INVALID_RESPONSE,
    STATUS_OK,
    fetch_calgary_jobs,
    is_cloudfront_blocked,
    matches_monitor_filters,
    probe_search_page,
)
from checker.models import JobRecord


def make_response(status_code=200, json_data=None, text="", headers=None):
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.text = text

    if json_data is None:
        response.json.side_effect = ValueError("invalid json")
    else:
        response.json.return_value = json_data

    if status_code >= 400:
        error = requests.exceptions.HTTPError(f"{status_code} error")
        error.response = response
        response.raise_for_status.side_effect = error
    else:
        response.raise_for_status.return_value = None

    return response


class JobsApiTests(unittest.TestCase):
    def setUp(self):
        self.calgary_config = MonitorConfig(
            locations=[
                LocationConfig(
                    label="Calgary",
                    query="Calgary",
                    radius_km=25,
                    exact_city=True,
                )
            ],
            keywords=KeywordsConfig(include=[], exclude=[]),
        )

    def test_exact_city_filter_accepts_any_calgary_hourly_title(self):
        job = JobRecord(
            job_id="JOB-CA-1",
            title="Customer Service Team Member",
            location="Calgary, AB, CAN",
            city="Calgary",
            region="AB",
            url="https://example.test/JOB-CA-1",
            source="test",
        )

        self.assertTrue(matches_monitor_filters(job, self.calgary_config))

    def test_exact_city_filter_rejects_nearby_and_partial_city_names(self):
        for city in ("Balzac", "Airdrie", "Calgary Region"):
            job = JobRecord(
                job_id="JOB-CA-%s" % city,
                title="Warehouse Associate",
                location="%s, AB, CAN" % city,
                city=city,
                region="AB",
                url="https://example.test/job",
                source="test",
            )
            with self.subTest(city=city):
                self.assertFalse(matches_monitor_filters(job, self.calgary_config))

    def test_exact_city_filter_uses_location_when_city_is_missing(self):
        job = JobRecord(
            job_id="JOB-CA-2",
            title="Sortation Associate",
            location="Calgary, Alberta, Canada",
            city="",
            region="AB",
            url="https://example.test/JOB-CA-2",
            source="test",
        )

        self.assertTrue(matches_monitor_filters(job, self.calgary_config))

    def test_detects_cloudfront_block_page(self):
        response = make_response(
            status_code=403,
            text="ERROR: The request could not be satisfied. Request blocked.",
            headers={"server": "CloudFront", "x-amz-cf-id": "abc123"},
        )
        self.assertTrue(is_cloudfront_blocked(response))

    @patch("checker.jobs_api.time.sleep")
    @patch("checker.jobs_api.requests.Session.get")
    def test_fetch_returns_blocked_status_for_cloudfront_403(self, mock_get, _mock_sleep):
        mock_get.return_value = make_response(
            status_code=403,
            text="ERROR: The request could not be satisfied. Request blocked.",
            headers={"server": "CloudFront", "x-amz-cf-id": "abc123"},
        )

        result = fetch_calgary_jobs(retries=1)

        self.assertEqual(result.status, STATUS_BLOCKED)

    @patch("checker.jobs_api.time.sleep")
    @patch("checker.jobs_api.requests.Session.get")
    def test_fetch_returns_ok_status_for_valid_json(self, mock_get, _mock_sleep):
        mock_get.return_value = make_response(
            status_code=200,
            json_data={
                "jobs": [
                    {
                        "jobId": "JOB-CA-0000000441",
                        "title": "Warehouse Associate",
                        "location": "Calgary, Alberta, Canada",
                    }
                ]
            },
        )

        result = fetch_calgary_jobs(retries=1)

        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["job_id"], "JOB-CA-0000000441")

    @patch("checker.jobs_api.requests.get")
    def test_probe_search_page_returns_invalid_response_when_markers_missing(self, mock_get):
        mock_get.return_value = make_response(
            status_code=200,
            text="<html><body><p>Unexpected page</p></body></html>",
        )

        result = probe_search_page(retries=1)

        self.assertEqual(result.status, STATUS_INVALID_RESPONSE)

    @patch("checker.jobs_api.requests.get")
    def test_probe_search_page_returns_ok_for_expected_marker(self, mock_get):
        mock_get.return_value = make_response(
            status_code=200,
            text="<html><body><h1>Amazon job results</h1></body></html>",
        )

        result = probe_search_page(retries=1)

        self.assertEqual(result.status, STATUS_OK)


if __name__ == "__main__":
    unittest.main()
