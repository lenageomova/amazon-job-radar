import json
import tempfile
import unittest

from checker.config import MIN_SAFE_INTERVAL_SECONDS, load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_clamps_unsafe_poll_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/monitor.json"
            with open(path, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "poll_interval_seconds": 60,
                        "seed_job_ids": ["JOB-CA-0000000441"],
                        "locations": [{"label": "Calgary", "query": "Calgary", "radius_km": 80}],
                    },
                    file,
                )

            config = load_config(path)

        self.assertEqual(config.poll_interval_seconds, MIN_SAFE_INTERVAL_SECONDS)
        self.assertEqual(config.seed_job_ids, ["JOB-CA-0000000441"])


if __name__ == "__main__":
    unittest.main()
