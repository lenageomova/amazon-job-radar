import unittest
from unittest.mock import Mock, patch

import requests

from checker.jobs_api import (
    STATUS_BLOCKED,
    STATUS_INVALID_RESPONSE,
    STATUS_OK,
    fetch_calgary_jobs,
    is_cloudfront_blocked,
    probe_search_page,
)


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
    def test_detects_cloudfront_block_page(self):
        response = make_response(
            status_code=403,
            text="ERROR: The request could not be satisfied. Request blocked.",
            headers={"server": "CloudFront", "x-amz-cf-id": "abc123"},
        )

        self.assertTrue(is_cloudfront_blocked(response))

    @patch("checker.jobs_api.requests.get")
    def test_fetch_returns_blocked_status_for_cloudfront_403(self, mock_get):
        mock_get.return_value = make_response(
            status_code=403,
            text="ERROR: The request could not be satisfied. Request blocked.",
            headers={"server": "CloudFront", "x-amz-cf-id": "abc123"},
        )

        result = fetch_calgary_jobs(retries=1)

        self.assertEqual(result.status, STATUS_BLOCKED)
        self.assertEqual(result.http_status, 403)
        self.assertEqual(result.request_id, "abc123")

    @patch("checker.jobs_api.requests.get")
    def test_fetch_returns_ok_status_for_valid_json(self, mock_get):
        mock_get.return_value = make_response(
            status_code=200,
            json_data={"jobs": [{"jobId": "J1"}, {"jobId": "J2"}]},
        )

        result = fetch_calgary_jobs(retries=1)

        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(len(result.jobs), 2)
        self.assertTrue(result.is_ok)

    @patch("checker.jobs_api.requests.get")
    def test_fetch_returns_invalid_response_when_jobs_missing(self, mock_get):
        mock_get.return_value = make_response(
            status_code=200,
            json_data={"unexpected": []},
        )

        result = fetch_calgary_jobs(retries=1)

        self.assertEqual(result.status, STATUS_INVALID_RESPONSE)

    @patch("checker.jobs_api.requests.get")
    def test_fetch_returns_page_not_found_message_for_api_400_page(self, mock_get):
        mock_get.return_value = make_response(
            status_code=400,
            text="<html><body><div>This page doesn't exist</div></body></html>",
        )

        result = fetch_calgary_jobs(retries=1)

        self.assertEqual(result.status, "http-error")
        self.assertEqual(result.http_status, 400)
        self.assertEqual(result.message, "Amazon jobs API endpoint returned a page-not-found error")

    @patch("checker.jobs_api.requests.get")
    def test_probe_search_page_returns_ok_for_expected_marker(self, mock_get):
        mock_get.return_value = make_response(
            status_code=200,
            text="<html><body><h1>Warehouse Job Results</h1></body></html>",
        )

        result = probe_search_page(retries=1)

        self.assertEqual(result.status, STATUS_OK)


if __name__ == "__main__":
    unittest.main()
