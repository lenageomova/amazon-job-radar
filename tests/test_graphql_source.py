import unittest
from unittest.mock import patch

from checker.config import KeywordsConfig, LocationConfig, MonitorConfig, SafeMonitorSettings
from checker.graphql_source import AmazonGraphQLSource, normalize_graphql_job


class GraphQLSourceTests(unittest.TestCase):
    def test_search_plan_uses_two_broad_requests_without_title_queries(self):
        config = MonitorConfig(safe_monitor=SafeMonitorSettings(base_queries=[]))

        requests = AmazonGraphQLSource(config)._search_requests()

        self.assertEqual([label for label, _request in requests], ["all/no-date", "all/from-today"])

    def test_direct_collection_filters_city_before_detail_and_skips_inactive_schedule(self):
        config = MonitorConfig(
            safe_monitor=SafeMonitorSettings(base_queries=[]),
            keywords=KeywordsConfig(include=[], exclude=[]),
        )
        source = AmazonGraphQLSource(config)
        operations = []

        def graphql_post(operation_name, _query, variables):
            operations.append(operation_name)
            if operation_name == "searchJobCardsByLocation":
                return {
                    "data": {
                        "searchJobCardsByLocation": {
                            "jobCards": [
                                {
                                    "jobId": "JOB-CA-CALGARY",
                                    "jobTitle": "Team Member",
                                    "city": "Calgary",
                                    "locationName": "Calgary, AB",
                                },
                                {
                                    "jobId": "JOB-CA-EDMONTON",
                                    "jobTitle": "Warehouse Associate",
                                    "city": "Edmonton",
                                    "locationName": "Edmonton, AB",
                                },
                            ]
                        }
                    }
                }
            if operation_name == "getJobDetail":
                self.assertEqual(
                    variables["getJobDetailRequest"]["jobId"],
                    "JOB-CA-CALGARY",
                )
                return {
                    "data": {
                        "getJobDetail": {
                            "jobId": "JOB-CA-CALGARY",
                            "jobTitle": "Team Member",
                            "city": "Calgary",
                            "locationName": "Calgary, AB",
                            "postingStatus": "UNPOSTED",
                        }
                    }
                }
            self.fail("Inactive jobs must not trigger a schedule request")

        with patch.object(source.session, "get"), patch.object(
            source,
            "_graphql_post",
            side_effect=graphql_post,
        ):
            payload = source._collect_with_direct_graphql()

        self.assertEqual(
            operations,
            [
                "searchJobCardsByLocation",
                "searchJobCardsByLocation",
                "getJobDetail",
            ],
        )
        self.assertEqual(list(payload["details"]), ["JOB-CA-CALGARY"])
        self.assertEqual(payload["schedulesByJobId"]["JOB-CA-CALGARY"], [])

    def test_normalize_graphql_job_includes_schedule_metadata(self):
        job = normalize_graphql_job(
            card={
                "jobId": "JOB-CA-0000000552",
                "jobTitle": "Amazon Delivery Station Warehouse Associate",
                "locationName": "Calgary, AB",
                "scheduleCount": 1,
                "totalPayRateMinL10N": "$23.10",
                "totalPayRateMaxL10N": "$23.60",
            },
            detail={
                "jobId": "JOB-CA-0000000552",
                "jobTitle": "Amazon Delivery Station Warehouse Associate",
                "postingStatus": "POSTED",
                "locationName": "Calgary, AB",
                "siteId": ["SITE-DCG4"],
                "mostRecentPostedDate": "2026-06-10",
            },
            schedules=[
                {
                    "scheduleId": "sch-1",
                    "scheduleText": "Night shift",
                    "totalPayRateL10N": "$23.60",
                    "laborDemandAvailableCount": 3,
                }
            ],
        )

        self.assertEqual(job.job_id, "JOB-CA-0000000552")
        self.assertEqual(job.raw["posting_status"], "POSTED")
        self.assertEqual(job.raw["schedule_count"], 1)
        self.assertTrue(job.raw["schedule_available"])
        self.assertEqual(job.raw["site_ids"], ["SITE-DCG4"])
        self.assertIn("schedules=1", job.summary)

    def test_payload_splits_posted_and_unposted_jobs(self):
        config = MonitorConfig(seed_job_ids=["JOB-CA-0000000441"])
        source = AmazonGraphQLSource(config)

        result = source._payload_to_result(
            {
                "jobCards": [
                    {
                        "jobId": "JOB-CA-0000000552",
                        "jobTitle": "Amazon Delivery Station Warehouse Associate",
                        "locationName": "Calgary, AB",
                        "scheduleCount": 0,
                    }
                ],
                "details": {
                    "JOB-CA-0000000552": {
                        "jobId": "JOB-CA-0000000552",
                        "jobTitle": "Amazon Delivery Station Warehouse Associate",
                        "postingStatus": "POSTED",
                        "locationName": "Calgary, AB",
                    },
                    "JOB-CA-0000000441": {
                        "jobId": "JOB-CA-0000000441",
                        "jobTitle": "Amazon Fulfillment Centre Warehouse Associate",
                        "postingStatus": "UNPOSTED",
                        "locationName": "Calgary, AB",
                        "mostRecentUnpostedDate": "2026-03-16",
                    },
                },
                "schedulesByJobId": {
                    "JOB-CA-0000000552": [],
                    "JOB-CA-0000000441": [],
                },
                "searchComplete": True,
                "requestCount": 4,
                "errors": [],
            },
            strategy="test",
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual([job.job_id for job in result.jobs], ["JOB-CA-0000000552"])
        self.assertEqual(
            [job.job_id for job in result.inactive_jobs],
            ["JOB-CA-0000000441"],
        )
        self.assertTrue(result.inventory_complete)

    def test_payload_keeps_only_exact_calgary_jobs_without_title_filter(self):
        config = MonitorConfig(
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
        source = AmazonGraphQLSource(config)

        result = source._payload_to_result(
            {
                "jobCards": [
                    {
                        "jobId": "JOB-CA-CALGARY",
                        "jobTitle": "Customer Service Team Member",
                        "city": "Calgary",
                        "state": "AB",
                        "locationName": "Calgary, AB",
                    },
                    {
                        "jobId": "JOB-CA-AIRDRIE",
                        "jobTitle": "Warehouse Associate",
                        "city": "Airdrie",
                        "state": "AB",
                        "locationName": "Airdrie, AB",
                    },
                ],
                "details": {},
                "schedulesByJobId": {},
                "searchComplete": True,
                "requestCount": 2,
                "errors": [],
            },
            strategy="test",
        )

        self.assertEqual([job.job_id for job in result.jobs], ["JOB-CA-CALGARY"])


if __name__ == "__main__":
    unittest.main()
