import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import requests


logger = logging.getLogger(__name__)

AMAZON_JOBS_URL = "https://hiring.amazon.com/api/jobs"
AMAZON_SEARCH_URL = (
    "https://hiring.amazon.com/search/warehouse-jobs?base_query=&loc_query=Calgary"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobAlertBot/1.0)",
    "Accept": "application/json",
    "Accept-Language": "en-CA,en;q=0.9",
}
HTML_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "text/html",
    "Accept-Language": HEADERS["Accept-Language"],
}
DEFAULT_LOCATION_KEYWORDS = [
    "calgary",
    "balzac",
    "alberta",
    "ab",
    "crossfield",
    "airdrie",
]
DEFAULT_JOB_TYPE_KEYWORDS = [
    "warehouse",
    "fulfillment",
    "delivery",
    "sortation",
    "associate",
    "picker",
    "packer",
    "stower",
    "scanner",
    "shipper",
]
STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"
STATUS_HTTP_ERROR = "http-error"
STATUS_NETWORK_ERROR = "network-error"
STATUS_INVALID_RESPONSE = "invalid-response"


@dataclass
class FetchResult:
    status: str
    jobs: list[dict] = field(default_factory=list)
    message: str = ""
    http_status: Optional[int] = None
    request_id: Optional[str] = None

    @property
    def is_ok(self) -> bool:
        return self.status == STATUS_OK


def _parse_keywords(env_name: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(env_name, "")
    keywords = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return keywords or defaults


def _extract_request_id(response: Optional[requests.Response]) -> Optional[str]:
    if response is None:
        return None
    return response.headers.get("x-amz-cf-id") or response.headers.get("x-amzn-requestid")


def is_cloudfront_blocked(response: requests.Response) -> bool:
    body = response.text.lower()
    server = response.headers.get("server", "").lower()
    return response.status_code == 403 and (
        "cloudfront" in server
        or "cloudfront" in body
        or "the request could not be satisfied" in body
        or "request blocked" in body
    )


def _build_http_error_result(error: requests.exceptions.HTTPError) -> FetchResult:
    response = error.response
    status_code = response.status_code if response is not None else None
    request_id = _extract_request_id(response)
    body = ""
    if response is not None:
        body = response.text.lower()

    if status_code in (400, 404) and (
        "page not found" in body
        or "this page doesn't exist" in body
        or "we can't find the page you're looking for" in body
    ):
        message = "Amazon jobs API endpoint returned a page-not-found error"
    else:
        message = f"HTTP error from Amazon: {error}"

    return FetchResult(
        status=STATUS_HTTP_ERROR,
        message=message,
        http_status=status_code,
        request_id=request_id,
    )


def fetch_calgary_jobs(retries: int = 3) -> FetchResult:
    """
    Fetch Amazon jobs around Calgary/Alberta with basic retry handling.
    """
    params = {
        "locale": "en-CA",
        "country": "Canada",
        "city": "Calgary",
        "radius": "80km",
        "jobType": "Full-Time,Part-Time,Seasonal",
        "category": "Fulfillment and Operations Management,Warehouse",
        "offset": 0,
        "result_limit": 50,
    }

    last_result = FetchResult(
        status=STATUS_NETWORK_ERROR,
        message="Amazon source could not be reached",
    )

    for attempt in range(retries):
        try:
            response = requests.get(
                AMAZON_JOBS_URL,
                params=params,
                headers=HEADERS,
                timeout=15,
            )

            if is_cloudfront_blocked(response):
                request_id = _extract_request_id(response)
                message = "Amazon blocked the request through CloudFront"
                if request_id:
                    message = f"{message} (request id: {request_id})"
                return FetchResult(
                    status=STATUS_BLOCKED,
                    message=message,
                    http_status=response.status_code,
                    request_id=request_id,
                )

            response.raise_for_status()

            try:
                data = response.json()
            except ValueError:
                return FetchResult(
                    status=STATUS_INVALID_RESPONSE,
                    message="Amazon returned a non-JSON response",
                    http_status=response.status_code,
                    request_id=_extract_request_id(response),
                )

            jobs = data.get("jobs")
            if not isinstance(jobs, list):
                return FetchResult(
                    status=STATUS_INVALID_RESPONSE,
                    message="Amazon API response did not include a jobs list",
                    http_status=response.status_code,
                    request_id=_extract_request_id(response),
                )

            return FetchResult(
                status=STATUS_OK,
                jobs=jobs,
                message=f"Amazon API returned {len(jobs)} jobs",
                http_status=response.status_code,
                request_id=_extract_request_id(response),
            )
        except requests.exceptions.HTTPError as error:
            last_result = _build_http_error_result(error)
            logger.error("HTTP error (attempt %s): %s", attempt + 1, last_result.message)
        except requests.exceptions.ConnectionError as error:
            last_result = FetchResult(
                status=STATUS_NETWORK_ERROR,
                message=f"Connection error contacting Amazon: {error}",
            )
            logger.error("Connection error (attempt %s): %s", attempt + 1, error)
        except requests.exceptions.Timeout:
            last_result = FetchResult(
                status=STATUS_NETWORK_ERROR,
                message="Timeout while contacting Amazon",
            )
            logger.error("Timeout (attempt %s)", attempt + 1)
        except Exception as error:
            last_result = FetchResult(
                status=STATUS_INVALID_RESPONSE,
                message=f"Unexpected error while processing Amazon response: {error}",
            )
            logger.error("Unexpected error (attempt %s): %s", attempt + 1, error)

        if attempt < retries - 1:
            time.sleep(10 * (attempt + 1))

    return last_result


def probe_search_page(retries: int = 1) -> FetchResult:
    """Check whether the public Amazon search page is reachable."""
    last_result = FetchResult(
        status=STATUS_NETWORK_ERROR,
        message="Amazon search page could not be reached",
    )

    for attempt in range(retries):
        try:
            response = requests.get(
                AMAZON_SEARCH_URL,
                headers=HTML_HEADERS,
                timeout=15,
            )

            if is_cloudfront_blocked(response):
                request_id = _extract_request_id(response)
                message = "Amazon search page is blocked through CloudFront"
                if request_id:
                    message = f"{message} (request id: {request_id})"
                return FetchResult(
                    status=STATUS_BLOCKED,
                    message=message,
                    http_status=response.status_code,
                    request_id=request_id,
                )

            response.raise_for_status()
            body = response.text.lower()
            if "warehouse job results" not in body and "job results" not in body:
                return FetchResult(
                    status=STATUS_INVALID_RESPONSE,
                    message="Amazon search page loaded, but the expected job results markers were missing",
                    http_status=response.status_code,
                    request_id=_extract_request_id(response),
                )

            return FetchResult(
                status=STATUS_OK,
                message="Amazon search page is reachable",
                http_status=response.status_code,
                request_id=_extract_request_id(response),
            )
        except requests.exceptions.HTTPError as error:
            last_result = _build_http_error_result(error)
            logger.error(
                "Search page HTTP error (attempt %s): %s",
                attempt + 1,
                last_result.message,
            )
        except requests.exceptions.ConnectionError as error:
            last_result = FetchResult(
                status=STATUS_NETWORK_ERROR,
                message=f"Connection error contacting Amazon search page: {error}",
            )
            logger.error("Search page connection error (attempt %s): %s", attempt + 1, error)
        except requests.exceptions.Timeout:
            last_result = FetchResult(
                status=STATUS_NETWORK_ERROR,
                message="Timeout while contacting Amazon search page",
            )
            logger.error("Search page timeout (attempt %s)", attempt + 1)

        if attempt < retries - 1:
            time.sleep(5 * (attempt + 1))

    return last_result


def is_relevant_job(job: dict) -> bool:
    """
    Match jobs by configured location and title keywords.
    """
    location_keywords = _parse_keywords("LOCATION_KEYWORDS", DEFAULT_LOCATION_KEYWORDS)
    title_keywords = _parse_keywords("JOB_TYPE_KEYWORDS", DEFAULT_JOB_TYPE_KEYWORDS)

    location = " ".join(
        [
            str(job.get("city", "")),
            str(job.get("state", "")),
            str(job.get("location", "")),
        ]
    ).lower()
    title = str(job.get("title", "")).lower()

    location_match = any(keyword in location for keyword in location_keywords)
    title_match = any(keyword in title for keyword in title_keywords)
    return location_match and title_match
