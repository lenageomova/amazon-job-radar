import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .config import MonitorConfig
from .jobs_api import is_cloudfront_blocked, matches_monitor_filters
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
STATUS_ERROR = SOURCE_STATUS_ERROR

POSTED_STATUSES = {"POSTED", "ACTIVE", "OPEN"}
UNPOSTED_STATUSES = {"UNPOSTED", "CLOSED", "INACTIVE", "REMOVED"}

SEARCH_JOB_CARDS_QUERY = """
query searchJobCardsByLocation($searchJobRequest: SearchJobRequest!) {
  searchJobCardsByLocation(searchJobRequest: $searchJobRequest) {
    nextToken
    jobCards {
      jobId
      language
      dataSource
      requisitionType
      jobTitle
      jobType
      employmentType
      city
      state
      postalCode
      locationName
      totalPayRateMin
      totalPayRateMax
      tagLine
      bannerText
      distance
      featuredJob
      bonusJob
      bonusPay
      scheduleCount
      currencyCode
      geoClusterDescription
      surgePay
      jobTypeL10N
      employmentTypeL10N
      bonusPayL10N
      surgePayL10N
      totalPayRateMinL10N
      totalPayRateMaxL10N
      distanceL10N
      payFrequency
      jobLocationType
      agencyName
      advertisedBasePay
      advertisedBasePayL10N
      advertisedPayFrequency
      advertisedPayFrequencyL10N
      __typename
    }
    __typename
  }
}
"""

GET_JOB_DETAIL_QUERY = """
query getJobDetail($getJobDetailRequest: GetJobDetailRequest!) {
  getJobDetail(getJobDetailRequest: $getJobDetailRequest) {
    agencyName
    jobId
    language
    dataSource
    requisitionType
    jobIdNumber
    jobTitle
    jobType
    jobTypeL10N
    employmentType
    employmentTypeL10N
    fullAddress
    country
    city
    state
    postalCode
    totalPayRateMin
    totalPayRateMinL10N
    totalPayRateMax
    totalPayRateMaxL10N
    currencyCode
    tagLine
    distance
    featuredJob
    bonusJob
    bonusPayL10N
    postingStatus
    uiPath
    siteId
    locationDescription
    locationName
    jobBannerText
    geoClusterId
    geoClusterName
    geoClusterRegion
    geoClusterDescription
    locationCode
    compliancePayRangeMin
    compliancePayRangeMinL10N
    compliancePayRangeMax
    compliancePayRangeMaxL10N
    compliancePayRangeFrequency
    requiredLanguage
    monthlyBasePay
    jobContainerJobMetaL1
    mostRecentPostedDate
    mostRecentUnpostedDate
    address
    poolingEnabled
    __typename
  }
}
"""

SEARCH_SCHEDULE_CARDS_QUERY = """
query searchScheduleCards($searchScheduleRequest: SearchScheduleRequest!) {
  searchScheduleCards(searchScheduleRequest: $searchScheduleRequest) {
    nextToken
    scheduleCards {
      hireStartDate
      address
      basePay
      bonusSchedule
      city
      currencyCode
      dataSource
      distance
      employmentType
      externalJobTitle
      featuredSchedule
      firstDayOnSite
      hoursPerWeek
      jobId
      language
      postalCode
      priorityRank
      scheduleBannerText
      scheduleId
      scheduleText
      scheduleType
      signOnBonus
      state
      surgePay
      tagLine
      geoClusterId
      geoClusterName
      siteId
      scheduleBusinessCategory
      totalPayRate
      financeWeekStartDate
      laborDemandAvailableCount
      scheduleBusinessCategoryL10N
      firstDayOnSiteL10N
      financeWeekStartDateL10N
      scheduleTypeL10N
      employmentTypeL10N
      basePayL10N
      signOnBonusL10N
      totalPayRateL10N
      distanceL10N
      requiredLanguage
      payFrequency
      locationType
      scheduleTextDescription
      parsedTrainingDate
      trainingLocationSiteId
      trainingLocationAddress
      trainingLocationCity
      trainingLocationState
      trainingLocationPostalCode
      __typename
    }
    __typename
  }
}
"""


class GraphQLSourceError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def _today_iso() -> str:
    return datetime.now().date().isoformat()


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        cleaned = _clean(item)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _pay_range(card: Optional[Dict[str, Any]], detail: Optional[Dict[str, Any]]) -> str:
    card = card or {}
    detail = detail or {}
    min_pay = (
        detail.get("compliancePayRangeMinL10N")
        or detail.get("totalPayRateMinL10N")
        or card.get("totalPayRateMinL10N")
    )
    max_pay = (
        detail.get("compliancePayRangeMaxL10N")
        or detail.get("totalPayRateMaxL10N")
        or card.get("totalPayRateMaxL10N")
    )
    values = [item for item in (_clean(min_pay), _clean(max_pay)) if item]
    return " - ".join(values)


def _location_from(card: Optional[Dict[str, Any]], detail: Optional[Dict[str, Any]]) -> str:
    card = card or {}
    detail = detail or {}
    location = (
        detail.get("locationName")
        or detail.get("locationDescription")
        or detail.get("fullAddress")
        or card.get("locationName")
        or card.get("geoClusterDescription")
    )
    if location:
        return _clean(location)

    city = detail.get("city") or card.get("city")
    state = detail.get("state") or card.get("state")
    joined = ", ".join(_clean(part) for part in (city, state) if _clean(part))
    return joined or "Location not specified"


def _city_from(card: Optional[Dict[str, Any]], detail: Optional[Dict[str, Any]]) -> str:
    card = card or {}
    detail = detail or {}
    city = detail.get("city") or card.get("city")
    if city:
        return _clean(city)
    location = _location_from(card, detail)
    return location.split(",")[0].strip() if "," in location else location


def _region_from(card: Optional[Dict[str, Any]], detail: Optional[Dict[str, Any]]) -> str:
    card = card or {}
    detail = detail or {}
    return _clean(detail.get("state") or card.get("state") or detail.get("geoClusterRegion"))


def _schedule_count(card: Optional[Dict[str, Any]], schedules: List[Dict[str, Any]]) -> int:
    card = card or {}
    if schedules:
        return len(schedules)
    return _as_int(card.get("scheduleCount"), 0)


def _status_from(detail: Optional[Dict[str, Any]], card: Optional[Dict[str, Any]]) -> str:
    if detail and detail.get("postingStatus"):
        return _upper(detail.get("postingStatus"))
    if card:
        return "POSTED"
    return "UNKNOWN"


def _site_ids(detail: Optional[Dict[str, Any]], schedules: List[Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    if detail:
        raw_site_id = detail.get("siteId")
        if isinstance(raw_site_id, list):
            ids.extend(_clean(item) for item in raw_site_id)
        elif raw_site_id:
            ids.append(_clean(raw_site_id))
    ids.extend(_clean(item.get("siteId")) for item in schedules if item.get("siteId"))
    return _dedupe(ids)


def _compact_schedule(schedule: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scheduleId": schedule.get("scheduleId"),
        "scheduleText": schedule.get("scheduleText"),
        "scheduleType": schedule.get("scheduleTypeL10N") or schedule.get("scheduleType"),
        "employmentType": schedule.get("employmentTypeL10N") or schedule.get("employmentType"),
        "firstDayOnSite": schedule.get("firstDayOnSiteL10N") or schedule.get("firstDayOnSite"),
        "hoursPerWeek": schedule.get("hoursPerWeek"),
        "pay": schedule.get("totalPayRateL10N") or schedule.get("basePayL10N"),
        "availableCount": schedule.get("laborDemandAvailableCount"),
    }


def normalize_graphql_job(
    card: Optional[Dict[str, Any]] = None,
    detail: Optional[Dict[str, Any]] = None,
    schedules: Optional[List[Dict[str, Any]]] = None,
) -> Optional[JobRecord]:
    schedules = schedules or []
    raw_for_id: Dict[str, Any] = {}
    if detail:
        raw_for_id.update(detail)
    if card:
        raw_for_id.update(card)
    job_id = resolve_job_id(raw_for_id)
    if not job_id or job_id.startswith("fp_"):
        return None

    title = _clean(
        (detail or {}).get("jobTitle")
        or (card or {}).get("jobTitle")
        or "Amazon Hiring Canada opportunity"
    )
    location = _location_from(card, detail)
    city = _city_from(card, detail)
    region = _region_from(card, detail)
    status = _status_from(detail, card)
    count = _schedule_count(card, schedules)
    pay = _pay_range(card, detail)
    site_ids = _site_ids(detail, schedules)
    posted = _clean((detail or {}).get("mostRecentPostedDate"))
    unposted = _clean((detail or {}).get("mostRecentUnpostedDate"))

    summary_parts = [
        "posting=%s" % status,
        "schedules=%s" % count,
    ]
    if pay:
        summary_parts.append("pay=%s" % pay)
    if site_ids:
        summary_parts.append("site=%s" % ",".join(site_ids))
    if posted:
        summary_parts.append("posted=%s" % posted)
    if unposted:
        summary_parts.append("unposted=%s" % unposted)

    raw = {
        "posting_status": status,
        "schedule_count": count,
        "schedule_available": count > 0,
        "pay_range": pay,
        "site_ids": site_ids,
        "most_recent_posted_date": posted,
        "most_recent_unposted_date": unposted,
        "job_card": card or {},
        "job_detail": detail or {},
        "schedules": [_compact_schedule(item) for item in schedules[:10]],
    }

    return JobRecord(
        job_id=job_id,
        title=title,
        location=location,
        city=city,
        region=region,
        url=canonical_job_url(job_id),
        source="amazon_graphql",
        raw=raw,
        summary="; ".join(summary_parts),
    )


def is_graphql_inactive(job: JobRecord) -> bool:
    status = _upper(job.raw.get("posting_status"))
    return status in UNPOSTED_STATUSES


class AmazonGraphQLSource:
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.settings = config.safe_monitor
        self.session = requests.Session()

    def fetch(self) -> SourceFetchResult:
        errors: List[str] = []
        last_status = STATUS_ERROR

        if self.settings.enable_direct_graphql:
            try:
                payload = self._collect_with_direct_graphql()
                return self._payload_to_result(payload, strategy="direct_graphql")
            except GraphQLSourceError as error:
                last_status = error.status
                errors.append(error.message)
                logger.warning("Direct GraphQL source failed: %s", error.message)

        if self.settings.enable_browser_graphql:
            try:
                payload = self._collect_with_browser_graphql()
                result = self._payload_to_result(payload, strategy="browser_graphql")
                result.errors = errors + result.errors
                return result
            except GraphQLSourceError as error:
                last_status = error.status
                errors.append(error.message)
                logger.warning("Browser GraphQL source failed: %s", error.message)

        message = "Amazon GraphQL source could not collect job data"
        if errors:
            message = "; ".join(errors[-3:])
        return SourceFetchResult(
            status=last_status,
            message=message,
            errors=errors or [message],
            inventory_complete=False,
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.settings.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-CA,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": "https://hiring.amazon.ca",
            "Referer": "https://hiring.amazon.ca/app",
            "country": "Canada",
            "iscanary": "false",
        }

    def _graphql_post(
        self,
        operation_name: str,
        query: str,
        variables: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = {
            "operationName": operation_name,
            "variables": variables,
            "query": query,
        }
        last_error = "Amazon GraphQL request failed"
        for attempt in range(1, self.settings.retry_attempts + 1):
            try:
                response = self.session.post(
                    self.settings.graphql_url,
                    headers=self._headers(),
                    json=body,
                    timeout=self.settings.request_timeout_seconds,
                )
                if is_cloudfront_blocked(response):
                    raise GraphQLSourceError(
                        STATUS_BLOCKED,
                        "Amazon blocked the GraphQL request through CloudFront",
                    )
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    messages = [
                        _clean(item.get("message"))
                        for item in payload.get("errors", [])
                        if isinstance(item, dict)
                    ]
                    raise GraphQLSourceError(
                        STATUS_ERROR,
                        "Amazon GraphQL returned errors: %s"
                        % "; ".join(messages[:3]),
                    )
                return payload
            except GraphQLSourceError:
                raise
            except Exception as error:
                last_error = "Amazon GraphQL %s failed: %s" % (operation_name, error)
                if attempt < self.settings.retry_attempts:
                    time.sleep(self.settings.retry_backoff_seconds * attempt)
        raise GraphQLSourceError(STATUS_ERROR, last_error)

    def _search_requests(self) -> List[Tuple[str, Dict[str, Any]]]:
        public_filter = {"key": "isPrivateSchedule", "val": ["false"]}
        sorters = [{"fieldName": "totalPayRateMax", "ascending": "false"}]
        base = {
            "locale": "en-CA",
            "country": "Canada",
            "pageSize": self.settings.page_size,
            "sorters": sorters,
            "containFilters": [public_filter],
        }
        today_filter = [{"key": "firstDayOnSite", "range": {"startDate": _today_iso()}}]
        keywords = [""] + self.settings.base_queries
        requests_plan: List[Tuple[str, Dict[str, Any]]] = []
        for keyword in _dedupe(keywords):
            label_keyword = keyword or "all"
            requests_plan.append((f"{label_keyword}/no-date", {**base, "keyWords": keyword}))
            requests_plan.append(
                (
                    f"{label_keyword}/from-today",
                    {**base, "keyWords": keyword, "dateFilters": today_filter},
                )
            )
        return requests_plan[: self.settings.max_requests_per_run]

    def _schedule_request(self, job_id: str) -> Dict[str, Any]:
        return {
            "locale": "en-CA",
            "country": "Canada",
            "keyWords": "",
            "equalFilters": [],
            "containFilters": [{"key": "isPrivateSchedule", "val": ["false"]}],
            "rangeFilters": [],
            "orFilters": [],
            "dateFilters": [{"key": "firstDayOnSite", "range": {"startDate": _today_iso()}}],
            "excludeFilters": [],
            "sorters": [{"fieldName": "totalPayRateMax", "ascending": "false"}],
            "pageSize": self.settings.schedule_page_size,
            "jobId": job_id,
            "consolidateSchedule": True,
        }

    def _collect_with_direct_graphql(self) -> Dict[str, Any]:
        # Prime normal site cookies first. This is polite and keeps direct mode close
        # to the browser app flow, but direct GraphQL is disabled by default because
        # Amazon often requires browser-generated session state.
        try:
            self.session.get(
                self.settings.app_search_url,
                headers=self._headers(),
                timeout=self.settings.request_timeout_seconds,
            )
        except Exception as error:
            logger.debug("Direct GraphQL priming failed: %s", error)

        job_cards: List[Dict[str, Any]] = []
        request_count = 0
        for _label, request in self._search_requests():
            payload = self._graphql_post(
                "searchJobCardsByLocation",
                SEARCH_JOB_CARDS_QUERY,
                {"searchJobRequest": request},
            )
            request_count += 1
            cards = (
                payload.get("data", {})
                .get("searchJobCardsByLocation", {})
                .get("jobCards", [])
            )
            if isinstance(cards, list):
                job_cards.extend(cards)

        job_ids = _dedupe(
            [resolve_job_id(item) for item in job_cards] + list(self.config.seed_job_ids)
        )
        details: Dict[str, Dict[str, Any]] = {}
        schedules: Dict[str, List[Dict[str, Any]]] = {}

        for job_id in job_ids:
            detail_payload = self._graphql_post(
                "getJobDetail",
                GET_JOB_DETAIL_QUERY,
                {"getJobDetailRequest": {"locale": "en-CA", "jobId": job_id}},
            )
            request_count += 1
            detail = detail_payload.get("data", {}).get("getJobDetail")
            if isinstance(detail, dict):
                details[job_id] = detail

            schedule_payload = self._graphql_post(
                "searchScheduleCards",
                SEARCH_SCHEDULE_CARDS_QUERY,
                {"searchScheduleRequest": self._schedule_request(job_id)},
            )
            request_count += 1
            cards = (
                schedule_payload.get("data", {})
                .get("searchScheduleCards", {})
                .get("scheduleCards", [])
            )
            schedules[job_id] = cards if isinstance(cards, list) else []

        return {
            "jobCards": job_cards,
            "details": details,
            "schedulesByJobId": schedules,
            "searchComplete": True,
            "requestCount": request_count,
            "errors": [],
        }

    def _collect_with_browser_graphql(self) -> Dict[str, Any]:
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise GraphQLSourceError(
                STATUS_ERROR,
                "Python Playwright is not installed. Run: python3 -m pip install -r requirements.txt && python3 -m playwright install chromium",
            ) from error

        try:
            return asyncio.run(
                self._collect_with_browser_graphql_async(
                    async_playwright,
                    PlaywrightTimeoutError,
                )
            )
        except GraphQLSourceError:
            raise
        except RuntimeError as error:
            raise GraphQLSourceError(STATUS_ERROR, str(error)) from error
        except Exception as error:
            raise GraphQLSourceError(
                STATUS_ERROR,
                "Browser GraphQL run failed: %s" % error,
            ) from error

    async def _collect_with_browser_graphql_async(
        self,
        async_playwright,
        PlaywrightTimeoutError,
    ) -> Dict[str, Any]:
        timeout_ms = self.settings.browser_timeout_seconds * 1000
        post_load_wait_ms = int(self.settings.browser_post_load_wait_seconds * 1000)
        browser = None
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                locale="en-CA",
                timezone_id="America/Edmonton",
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={"Accept-Language": "en-CA,en;q=0.9"},
            )
            await context.add_init_script(
                """
                window.localStorage.setItem("cookieConsent", "true");
                window.localStorage.setItem("hideGuidedSearchOnLoad", "true");
                window.localStorage.setItem("geoInfo", JSON.stringify({
                  country: "CAN",
                  lat: 51.0447,
                  lng: -114.0719,
                  postalCode: "T2E 5L4",
                  label: "Calgary, AB, CAN",
                  municipality: "Calgary",
                  region: "Alberta",
                  shownValue: "Calgary"
                }));
                """
            )
            page = await context.new_page()
            try:
                await page.goto(
                    self.settings.app_search_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                if post_load_wait_ms:
                    await page.wait_for_timeout(post_load_wait_ms)

                body_text = ""
                try:
                    body_text = await page.locator("body").inner_text(timeout=5000)
                except Exception:
                    body_text = await page.content()
                if re.search(
                    r"request blocked|cloudfront|the request could not be satisfied",
                    body_text,
                    re.IGNORECASE,
                ):
                    raise GraphQLSourceError(
                        STATUS_BLOCKED,
                        "Amazon blocked the browser search page through CloudFront",
                    )

                payload = await page.evaluate(
                    """
                    async ({searchRequests, seedJobIds, schedulePageSize, requestSpacingMs}) => {
                      const searchQuery = `%s`;
                      const detailQuery = `%s`;
                      const scheduleQuery = `%s`;
                      const headers = {
                        "content-type": "application/json",
                        "accept": "*/*",
                        "country": "Canada",
                        "iscanary": "false"
                      };
                      const token = window.localStorage.getItem("sessionToken");
                      if (token) {
                        headers.authorization = `Bearer Status|unauthenticated|Session|${token}`;
                      }
                      const today = new Date().toISOString().slice(0, 10);
                      const errors = [];
                      let requestCount = 0;

                      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

                      async function post(operationName, query, variables) {
                        if (requestSpacingMs > 0 && requestCount > 0) {
                          await sleep(requestSpacingMs);
                        }
                        requestCount += 1;
                        const response = await fetch("/graphql", {
                          method: "POST",
                          headers,
                          body: JSON.stringify({operationName, variables, query})
                        });
                        const json = await response.json().catch(() => ({}));
                        if (!response.ok || json.errors) {
                          const messages = (json.errors || []).map((e) => e.message).filter(Boolean);
                          throw new Error(`${operationName} HTTP ${response.status}: ${messages.join("; ")}`);
                        }
                        return json;
                      }

                      const cardsById = {};
                      let searchComplete = false;
                      for (const item of searchRequests) {
                        try {
                          const json = await post("searchJobCardsByLocation", searchQuery, {
                            searchJobRequest: item.request
                          });
                          const cards = json?.data?.searchJobCardsByLocation?.jobCards || [];
                          searchComplete = true;
                          for (const card of cards) {
                            if (card?.jobId) cardsById[card.jobId] = card;
                          }
                        } catch (error) {
                          errors.push(`${item.label}: ${error.message}`);
                        }
                      }

                      const jobIds = Array.from(new Set([
                        ...Object.keys(cardsById),
                        ...seedJobIds
                      ].filter(Boolean)));
                      const details = {};
                      const schedulesByJobId = {};

                      for (const jobId of jobIds) {
                        try {
                          const json = await post("getJobDetail", detailQuery, {
                            getJobDetailRequest: {locale: "en-CA", jobId}
                          });
                          const detail = json?.data?.getJobDetail;
                          if (detail) details[jobId] = detail;
                        } catch (error) {
                          errors.push(`${jobId}/detail: ${error.message}`);
                        }

                        try {
                          const json = await post("searchScheduleCards", scheduleQuery, {
                            searchScheduleRequest: {
                              locale: "en-CA",
                              country: "Canada",
                              keyWords: "",
                              equalFilters: [],
                              containFilters: [{key: "isPrivateSchedule", val: ["false"]}],
                              rangeFilters: [],
                              orFilters: [],
                              dateFilters: [{key: "firstDayOnSite", range: {startDate: today}}],
                              excludeFilters: [],
                              sorters: [{fieldName: "totalPayRateMax", ascending: "false"}],
                              pageSize: schedulePageSize,
                              jobId,
                              consolidateSchedule: true
                            }
                          });
                          schedulesByJobId[jobId] = json?.data?.searchScheduleCards?.scheduleCards || [];
                        } catch (error) {
                          errors.push(`${jobId}/schedules: ${error.message}`);
                          schedulesByJobId[jobId] = [];
                        }
                      }

                      return {
                        jobCards: Object.values(cardsById),
                        details,
                        schedulesByJobId,
                        searchComplete,
                        requestCount,
                        errors
                      };
                    }
                    """
                    % (
                        SEARCH_JOB_CARDS_QUERY.replace("`", "\\`"),
                        GET_JOB_DETAIL_QUERY.replace("`", "\\`"),
                        SEARCH_SCHEDULE_CARDS_QUERY.replace("`", "\\`"),
                    ),
                    {
                        "searchRequests": [
                            {"label": label, "request": request}
                            for label, request in self._search_requests()
                        ],
                        "seedJobIds": list(self.config.seed_job_ids),
                        "schedulePageSize": self.settings.schedule_page_size,
                        "requestSpacingMs": int(self.settings.request_spacing_seconds * 1000),
                    },
                )
                return payload
            finally:
                await context.close()
                await browser.close()

    def _payload_to_result(self, payload: Dict[str, Any], strategy: str) -> SourceFetchResult:
        job_cards = payload.get("jobCards", [])
        if not isinstance(job_cards, list):
            job_cards = []
        details = payload.get("details", {})
        if not isinstance(details, dict):
            details = {}
        schedules_by_id = payload.get("schedulesByJobId", {})
        if not isinstance(schedules_by_id, dict):
            schedules_by_id = {}

        cards_by_id: Dict[str, Dict[str, Any]] = {}
        for card in job_cards:
            if not isinstance(card, dict):
                continue
            job_id = resolve_job_id(card)
            if job_id and not job_id.startswith("fp_"):
                cards_by_id[job_id] = card

        all_job_ids = _dedupe(
            list(cards_by_id.keys()) + list(details.keys()) + list(self.config.seed_job_ids)
        )
        active_jobs: Dict[str, JobRecord] = {}
        inactive_jobs: Dict[str, JobRecord] = {}

        for job_id in all_job_ids:
            card = cards_by_id.get(job_id)
            detail = details.get(job_id) if isinstance(details.get(job_id), dict) else None
            schedules = schedules_by_id.get(job_id, [])
            if not isinstance(schedules, list):
                schedules = []
            job = normalize_graphql_job(card=card, detail=detail, schedules=schedules)
            if job is None:
                continue
            if is_graphql_inactive(job):
                inactive_jobs[job.job_id] = job
                continue
            if matches_monitor_filters(job, self.config):
                active_jobs[job.job_id] = job

        errors = [
            _clean(item)
            for item in payload.get("errors", [])
            if _clean(item)
        ]
        inventory_complete = bool(payload.get("searchComplete"))
        status = STATUS_OK if inventory_complete or active_jobs or inactive_jobs else STATUS_ERROR
        message = (
            "Collected %s active and %s inactive jobs from Amazon GraphQL (%s)"
            % (len(active_jobs), len(inactive_jobs), strategy)
        )
        if errors:
            message += " with partial GraphQL errors"

        return SourceFetchResult(
            status=status,
            jobs=list(active_jobs.values()),
            inactive_jobs=list(inactive_jobs.values()),
            message=message,
            errors=errors,
            request_count=_as_int(payload.get("requestCount"), 0),
            inventory_complete=inventory_complete and status == STATUS_OK,
        )
