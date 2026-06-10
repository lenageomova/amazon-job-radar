import unittest

from checker.config import MonitorConfig
from checker.graphql_source import AmazonGraphQLSource, normalize_graphql_job


class GraphQLSourceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
