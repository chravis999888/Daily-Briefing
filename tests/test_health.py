"""Run: python -m unittest tests.test_health  (offline)."""
import unittest
from datetime import datetime, timedelta

from tests.harness import CREDIT_ERROR, run_pipeline, workdir, read_json
from tests.test_data_safety import seeded_memory
from memory import AEST, log_run
from safety import evaluate_run

ERR = {"outcome": "error", "label": "football_selection", "error": CREDIT_ERROR}
OK = {"outcome": "ok", "label": "football_selection"}
T0 = datetime(2026, 10, 11, 12, 0, tzinfo=AEST)


class LogRun(unittest.TestCase):
    def test_sources_errors_and_run_fields(self):
        v = evaluate_run("full", {"stories": {}}, [OK, ERR], True, fresh={"football": [{"headline": "h"}]})
        h = log_run({"runs": [], "errors": []}, "full", [], v,
                    [("Reuters", True, ""), ("GDELT", False, "HTTP 500"), ("Reuters", False, "0 articles returned")], [OK, ERR], T0)
        self.assertEqual(h["sources"]["Reuters"]["last_success"], T0.isoformat())
        self.assertEqual(h["sources"]["Reuters"]["last_error"]["message"], "0 articles returned")
        self.assertEqual(h["sources"]["GDELT"]["last_success"], None)
        self.assertEqual(h["sources"]["claude"]["last_error"]["message"], CREDIT_ERROR)
        run = h["runs"][-1]
        self.assertEqual((run["outcome"], run["claude_calls"], run["claude_calls_errored"]), ("degraded", 2, 1))
        self.assertEqual(h["last_successful_run"], T0.isoformat())
        self.assertTrue(h["errors"])                                   # top-level errors key is now filled
        self.assertEqual({e["source"] for e in h["errors"]}, {"GDELT", "Reuters", "claude"})

    def test_failed_run_does_not_set_last_successful_run(self):
        v = evaluate_run("full", {"stories": {}}, [ERR] * 3, True, fresh={})
        h = log_run({"runs": [], "errors": []}, "full", [], v, [], [ERR] * 3, T0)
        self.assertNotIn("last_successful_run", h)
        self.assertTrue(any(e["source"] == "run" for e in h["errors"]))

    def test_errors_capped_at_50(self):
        h = {"runs": [], "errors": [{"x": i} for i in range(60)]}
        v = evaluate_run("breaking_only", {"stories": {}}, [], False)
        self.assertEqual(len(log_run(h, "breaking_only", [], v, [], [], T0)["errors"]), 50)


class Pipeline(unittest.TestCase):
    def test_cost_ledger_key_kept_and_errors_filled(self):
        with workdir() as d:
            run_pipeline(d, "full", "error", seed={"memory.json": seeded_memory()})
            h = read_json(d, "health.json")
            self.assertIn("api_cost_ledger", h)
            self.assertTrue(h["errors"])
            self.assertIn("credit balance is too low", h["sources"]["claude"]["last_error"]["message"])
            self.assertTrue(h["sources"]["Guardian"]["last_success"])

    def test_source_down_is_recorded(self):
        with workdir() as d:
            run_pipeline(d, "breaking_only", "ok", sources="down", seed={"memory.json": seeded_memory()})
            h = read_json(d, "health.json")
            self.assertIn("dry-run network down", h["sources"]["Guardian"]["last_error"]["message"])
            self.assertIsNone(h["sources"]["Guardian"]["last_success"])


if __name__ == "__main__":
    unittest.main()
