import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from .config import (
    DEFAULT_EXCLUDE_KEYWORDS,
    DEFAULT_INCLUDE_KEYWORDS,
    KeywordsConfig,
    MonitorConfig,
    SafeMonitorSettings,
    load_config,
)
from .models import (
    SOURCE_STATUS_BLOCKED,
    SOURCE_STATUS_ERROR,
    SOURCE_STATUS_OK,
    JobRecord,
    SourceFetchResult,
)
from .storage import canonical_job_url, resolve_job_id


logger = logging.getLogger(__name__)

STATUS_OK = SOURCE_STATUS_OK
STATUS_BLOCKED = SOURCE_STATUS_BLOCKED
STATUS_HTTP_ERROR = "http-error"
STATUS_NETWORK_ERROR = "network-error"
STATUS_INVALID_RESPONSE = "invalid-response"


@dataclass
class FetchResult:
    status: str
    jobs: List[Dict] = field(default_factory=list)
    message: str = ""
    http_status: Optional[int] = None
    request_id: Optional[str] = None

    @property
    def is_ok(self) -> bool:
        return self.status == STATUS_OK


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


def _headers(settings: SafeMonitorSettings) -> Dict[str, str]:
    return {
        "User-Agent": settings.user_agent,
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "en-CA,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _parse_jobs_from_payload(data: Dict) -> List[Dict]:
    candidates = [
        data.get("jobs"),
        data.get("results"),
        data.get("jobResults"),
        data.get("items"),
        data.get("data", {}).get("jobs") if isinstance(data.get("data"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
    return []


def _normalize_title(raw_job: Dict) -> str:
    for key in ("title", "jobTitle", "job_title", "displayTitle", "name"):
        value = raw_job.get(key)
        if value:
            return str(value).strip()
    return "Amazon Hiring Canada opportunity"


def _normalize_location(raw_job: Dict) -> str:
    location = raw_job.get("location")
    if location:
        return str(location).strip()

    parts = [
        raw_job.get("city"),
        raw_job.get("state"),
        raw_job.get("province"),
        raw_job.get("country"),
    ]
    joined = ", ".join(str(part).strip() for part in parts if part)
    return joined or "Location not specified"


def normalize_api_job(raw_job: Dict) -> Optional[JobRecord]:
    job_id = resolve_job_id(raw_job)
    if not job_id:
        return None

    title = _normalize_title(raw_job)
    location = _normalize_location(raw_job)
    city = str(raw_job.get("city", "")).strip()
    region = str(raw_job.get("state") or raw_job.get("province") or "").strip()
    url = canonical_job_url(job_id)

    return JobRecord(
        job_id=job_id,
        title=title,
        location=location,
        city=city,
        region=region,
        url=url,
        source="amazon_public_search",
        raw=raw_job,
        summary=str(raw_job.get("description", "")).strip()[:280],
    )


def _keyword_list(env_name: str, defaults: List[str]) -> List[str]:
    raw = os.getenv(env_name, "")
    if not raw.strip():
        return list(defaults)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _normalize_location_term(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def matches_location_filters(job: JobRecord, config: MonitorConfig) -> bool:
    city = _normalize_location_term(job.city)
    location = _normalize_location_term(job.location)
    first_location_part = _normalize_location_term(job.location.split(",", 1)[0])

    for item in config.locations:
        terms = {
            _normalize_location_term(item.query),
            _normalize_location_term(item.label),
        }
        terms.discard("")
        if not terms:
            continue

        if item.exact_city:
            if city and city in terms:
                return True
            if not city and first_location_part in terms:
                return True
            continue

        haystack = " ".join(part for part in (location, city) if part)
        if any(term in haystack for term in terms):
            return True

    return False


def matches_monitor_filters(job: JobRecord, config: MonitorConfig) -> bool:
    include_terms = config.keywords.normalized_include()
    exclude_terms = config.keywords.normalized_exclude()

    haystack_location = " ".join([job.location, job.city, job.region]).lower()
    haystack_text = " ".join([job.title, job.summary, haystack_location]).lower()

    location_match = matches_location_filters(job, config)
    include_match = not include_terms or any(term in haystack_text for term in include_terms)
    exclude_match = any(term in haystack_text for term in exclude_terms)
    return location_match and include_match and not exclude_match


def is_relevant_job(job: Dict) -> bool:
    normalized = normalize_api_job(job)
    if normalized is None:
        return False

    config = MonitorConfig(
        keywords=KeywordsConfig(
            include=_keyword_list(
                "JOB_TYPE_KEYWORDS",
                list(DEFAULT_INCLUDE_KEYWORDS),
            ),
            exclude=_keyword_list(
                "JOB_EXCLUDE_KEYWORDS",
                list(DEFAULT_EXCLUDE_KEYWORDS),
            ),
        )
    )
    return matches_monitor_filters(normalized, config)


class AmazonPublicSearchSource:
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.settings = config.safe_monitor
        self.session = requests.Session()

    def _request_search(self, base_query: str, location_query: str) -> FetchResult:
        params = {
            "base_query": base_query,
            "loc_query": location_query,
            "radius": str(self._radius_for_location(location_query)),
            "page": "1",
            "size": str(self.settings.page_size),
        }

        last_result = FetchResult(
            status=STATUS_NETWORK_ERROR,
            message="Amazon source could not be reached",
        )

        for attempt in range(1, self.settings.retry_attempts + 1):
            try:
                response = self.session.get(
                    self.settings.api_base_url,
                    params=params,
                    headers=_headers(self.settings),
                    timeout=self.settings.request_timeout_seconds,
                )

                if is_cloudfront_blocked(response):
                    request_id = _extract_request_id(response)
                    message = "Amazon blocked the public search request"
                    if request_id:
                        message = "%s (request id: %s)" % (message, request_id)
                    return FetchResult(
                        status=STATUS_BLOCKED,
                        message=message,
                        http_status=response.status_code,
                        request_id=request_id,
                    )

                if response.status_code == 429:
                    wait_seconds = self.settings.retry_backoff_seconds * attempt
                    logger.warning(
                        "Amazon rate-limited search '%s' in %s, backing off for %ss",
                        base_query,
                        location_query,
                        wait_seconds,
                    )
                    time.sleep(wait_seconds)
                    continue

                response.raise_for_status()

                try:
                    payload = response.json()
                except ValueError:
                    return FetchResult(
                        status=STATUS_INVALID_RESPONSE,
                        message="Amazon returned a non-JSON search payload",
                        http_status=response.status_code,
                        request_id=_extract_request_id(response),
                    )

                jobs = _parse_jobs_from_payload(payload)
                return FetchResult(
                    status=STATUS_OK,
                    jobs=jobs,
                    message="Amazon search returned %s jobs" % len(jobs),
                    http_status=response.status_code,
                    request_id=_extract_request_id(response),
                )
            except requests.exceptions.HTTPError as error:
                response = error.response
                last_result = FetchResult(
                    status=STATUS_HTTP_ERROR,
                    message="HTTP error from Amazon: %s" % error,
                    http_status=response.status_code if response is not None else None,
                    request_id=_extract_request_id(response),
                )
                logger.warning("%s", last_result.message)
            except requests.exceptions.Timeout:
                last_result = FetchResult(
                    status=STATUS_NETWORK_ERROR,
                    message="Timeout while contacting Amazon search API",
                )
                logger.warning("%s", last_result.message)
            except requests.exceptions.ConnectionError as error:
                last_result = FetchResult(
                    status=STATUS_NETWORK_ERROR,
                    message="Connection error contacting Amazon: %s" % error,
                )
                logger.warning("%s", last_result.message)

            if attempt < self.settings.retry_attempts:
                time.sleep(self.settings.retry_backoff_seconds * attempt)

        return last_result

    def _radius_for_location(self, location_query: str) -> int:
        for item in self.config.locations:
            if item.query.lower() == location_query.lower():
                return item.radius_km
        return 80

    def fetch(self) -> SourceFetchResult:
        planned_requests = []
        base_queries = [""] + list(dict.fromkeys(self.settings.base_queries))
        for base_query in base_queries:
            for location in self.config.locations:
                planned_requests.append((base_query, location.query))

        planned_requests = planned_requests[: self.settings.max_requests_per_run]
        request_count = 0
        inventory_complete = True
        errors: List[str] = []
        jobs_by_id: Dict[str, JobRecord] = {}

        for index, (base_query, location_query) in enumerate(planned_requests):
            if index > 0 and self.settings.request_spacing_seconds > 0:
                time.sleep(self.settings.request_spacing_seconds)

            result = self._request_search(base_query, location_query)
            request_count += 1

            if result.status == STATUS_BLOCKED:
                return SourceFetchResult(
                    status=STATUS_BLOCKED,
                    jobs=list(jobs_by_id.values()),
                    message=result.message,
                    errors=[result.message],
                    request_count=request_count,
                    inventory_complete=False,
                )

            if not result.is_ok:
                inventory_complete = False
                errors.append(
                    "%s @ %s: %s" % (base_query, location_query, result.message)
                )
                continue

            for raw_job in result.jobs:
                normalized = normalize_api_job(raw_job)
                if not normalized:
                    continue
                if not matches_monitor_filters(normalized, self.config):
                    continue
                jobs_by_id[normalized.job_id] = normalized

        status = STATUS_OK if jobs_by_id or not errors else SOURCE_STATUS_ERROR
        message = "Collected %s relevant jobs from Amazon public search" % len(jobs_by_id)
        if errors and status == STATUS_OK:
            message += " (partial visibility)"

        return SourceFetchResult(
            status=status,
            jobs=list(jobs_by_id.values()),
            message=message,
            errors=errors,
            request_count=request_count,
            inventory_complete=inventory_complete and status == STATUS_OK,
        )


def probe_search_page(retries: int = 1) -> FetchResult:
    config = load_config()
    settings = config.safe_monitor
    last_result = FetchResult(
        status=STATUS_NETWORK_ERROR,
        message="Amazon search page could not be reached",
    )

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                settings.search_probe_url,
                headers=_headers(settings),
                timeout=settings.request_timeout_seconds,
            )

            if is_cloudfront_blocked(response):
                request_id = _extract_request_id(response)
                message = "Amazon search page is blocked through CloudFront"
                if request_id:
                    message = "%s (request id: %s)" % (message, request_id)
                return FetchResult(
                    status=STATUS_BLOCKED,
                    message=message,
                    http_status=response.status_code,
                    request_id=request_id,
                )

            response.raise_for_status()
            body = response.text.lower()
            if "job" not in body and "amazon" not in body:
                return FetchResult(
                    status=STATUS_INVALID_RESPONSE,
                    message=(
                        "Amazon search page loaded, but expected content markers were missing"
                    ),
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
            response = error.response
            last_result = FetchResult(
                status=STATUS_HTTP_ERROR,
                message="HTTP error from Amazon search page: %s" % error,
                http_status=response.status_code if response is not None else None,
                request_id=_extract_request_id(response),
            )
        except requests.exceptions.ConnectionError as error:
            last_result = FetchResult(
                status=STATUS_NETWORK_ERROR,
                message="Connection error contacting Amazon search page: %s" % error,
            )
        except requests.exceptions.Timeout:
            last_result = FetchResult(
                status=STATUS_NETWORK_ERROR,
                message="Timeout while contacting Amazon search page",
            )

        if attempt < retries:
            time.sleep(settings.retry_backoff_seconds * attempt)

    return last_result


def fetch_calgary_jobs(retries: int = 3) -> FetchResult:
    config = load_config()
    config.safe_monitor.retry_attempts = retries
    source = AmazonPublicSearchSource(config)
    result = source.fetch()

    status = result.status
    if result.status == SOURCE_STATUS_ERROR:
        status = STATUS_NETWORK_ERROR

    return FetchResult(
        status=status,
        jobs=[job.to_dict() for job in result.jobs],
        message=result.message,
    )
