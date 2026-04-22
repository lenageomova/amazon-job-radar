import unittest

from checker.storage import resolve_job_id


class StorageTests(unittest.TestCase):
    def test_resolve_job_id_uses_explicit_identifier_first(self):
        job = {"jobId": "JOB123", "id": "fallback"}

        self.assertEqual(resolve_job_id(job), "JOB123")

    def test_resolve_job_id_uses_fingerprint_when_identifiers_missing(self):
        job = {
            "title": "Warehouse Associate",
            "city": "Calgary",
            "postalCode": "T1X0L3",
        }

        resolved_id = resolve_job_id(job)

        self.assertTrue(resolved_id.startswith("fp_"))
        self.assertEqual(len(resolved_id), 15)


if __name__ == "__main__":
    unittest.main()
