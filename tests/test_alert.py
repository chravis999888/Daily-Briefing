"""Run: python -m unittest tests.test_alert  (offline). Exit code 1 == the Actions run fails == GitHub emails."""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests.harness import run_pipeline, workdir, read_json
from tests.test_data_safety import seeded_memory
from memory import AEST
from safety import decide_alert


def hours_ago(h):
    return (datetime.now(AEST) - timedelta(hours=h)).isoformat()


def fail(d, **kw):
    return run_pipeline(d, "full", "error", **kw)[1]


class Rule(unittest.TestCase):
    def test_only_failed_runs_alert(self):
        for outcome in ("ok", "degraded"):
            self.assertEqual(decide_alert({}, outcome, 5), (False, False))

    def test_first_failure_alerts_then_silent(self):
        h = {"last_successful_data_update": hours_ago(1)}
        self.assertEqual(decide_alert(h, "failed", 1), (True, False))
        self.assertEqual(decide_alert(h, "failed", 2), (False, False))

    def test_stale_crossing_alerts_once(self):
        h = {"last_successful_data_update": hours_ago(9)}
        self.assertEqual(decide_alert(h, "failed", 5), (True, True))
        self.assertEqual(decide_alert({**h, "stale_alerted": True}, "failed", 6), (False, False))


class Pipeline(unittest.TestCase):
    def test_alert_sequence_first_failure_stale_crossing_then_silent_then_reset(self):
        with workdir() as d:
            seed = {"memory.json": seeded_memory(), "health.json": {"runs": [], "errors": [], "last_successful_data_update": hours_ago(1)}}
            self.assertEqual(fail(d, seed=seed), 1)            # (a) consecutive_failures becomes 1
            self.assertEqual(fail(d), 0)                       # silent
            self.assertEqual(fail(d), 0)
            h = read_json(d, "health.json"); h["last_successful_data_update"] = hours_ago(8.5)
            (Path(d) / "health.json").write_text(json.dumps(h))
            self.assertEqual(fail(d), 1)                       # (b) first time past 8h
            self.assertEqual(fail(d), 0)                       # silent after that
            self.assertEqual(fail(d), 0)
            self.assertEqual(run_pipeline(d, "full", "ok")[1], 0)   # good run resets
            h = read_json(d, "health.json")
            self.assertEqual((h["consecutive_failures"], h["stale_alerted"]), (0, False))
            self.assertEqual(fail(d), 1)                       # a new streak alerts again

    def test_missing_timestamp_alerts_once(self):
        with workdir() as d:
            seed = {"memory.json": seeded_memory(), "health.json": {"runs": [], "errors": []}}
            self.assertEqual(fail(d, seed=seed), 1)
            self.assertEqual(fail(d), 0)

    def test_degraded_and_source_errors_do_not_fail_the_workflow(self):
        with workdir() as d:
            out, rc = run_pipeline(d, "full", "ok", fail_when="Australian news editor", seed={"memory.json": seeded_memory()})
            self.assertEqual(rc, 0)
            self.assertEqual(read_json(d, "health.json")["runs"][-1]["outcome"], "degraded")
        with workdir() as d:
            self.assertEqual(run_pipeline(d, "full", "ok", seed={"memory.json": seeded_memory()})[1], 0)

    def test_files_are_written_before_the_nonzero_exit(self):
        with workdir() as d:
            _, rc = run_pipeline(d, "full", "error", seed={"memory.json": seeded_memory()})
            self.assertEqual(rc, 1)
            for name in ("health.json", "cost_log.json", "COST_SUMMARY.md", "dist/index.html"):
                self.assertTrue((Path(d) / name).exists(), name)
            self.assertTrue(read_json(d, "health.json")["runs"][-1]["alerted"])


if __name__ == "__main__":
    unittest.main()
