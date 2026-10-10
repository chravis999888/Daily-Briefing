"""Regression tests for the PR #62 review findings (A-E). Offline: python -m unittest tests.test_review_fixes"""
import json
import re
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests.harness import run_pipeline, workdir, read_json
from tests.test_data_safety import seeded_memory
from memory import AEST


def hrs(h):
    return (datetime.now(AEST) - timedelta(hours=h)).isoformat()


def set_age(d, hours):
    h = read_json(d, "health.json")
    h["last_successful_data_update"] = hrs(hours)
    Path(d, "health.json").write_text(json.dumps(h))


def deployed(d):
    return (Path(d) / "dist" / ".deploy_needed").exists()


def fresh_runner(d):
    shutil.rmtree(Path(d) / "dist", ignore_errors=True)   # each Actions run starts without dist/


class A_DeployOnStaleOnce(unittest.TestCase):
    def test_exactly_one_deploy_per_outage(self):
        with workdir() as d:
            seed = {"memory.json": seeded_memory(), "health.json": {"runs": [], "errors": [], "last_successful_data_update": hrs(7)}}
            results = []
            run_pipeline(d, "full", "error", seed=seed)
            results.append((7, deployed(d)))
            for age in (8.5, 9, 9.5, 10):
                fresh_runner(d); set_age(d, age)
                run_pipeline(d, "full", "error")
                results.append((age, deployed(d)))
            self.assertEqual(results, [(7, False), (8.5, True), (9, False), (9.5, False), (10, False)])
            fresh_runner(d)                                             # good run resets the flag
            run_pipeline(d, "full", "ok")
            self.assertFalse(read_json(d, "health.json")["stale_deployed"])
            fresh_runner(d); set_age(d, 9)                              # a later outage deploys once again
            run_pipeline(d, "full", "error")
            self.assertTrue(deployed(d))
            fresh_runner(d); set_age(d, 9.5)
            run_pipeline(d, "full", "error")
            self.assertFalse(deployed(d))


class B_EmptySummaries(unittest.TestCase):
    def setUp(self):
        self.ts = hrs(1)

    def _seed(self):
        return {"memory.json": seeded_memory(),
                "health.json": {"runs": [], "errors": [], "last_successful_data_update": self.ts}}

    def test_all_summary_calls_fail_nothing_is_replaced(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", fail_when="In 3-4 sentences", seed=self._seed())
            self.assertEqual(read_json(d, "memory.json"), seeded_memory())
            h = read_json(d, "health.json")
            self.assertEqual(h["runs"][-1]["outcome"], "failed")
            self.assertEqual(h["last_successful_data_update"], self.ts)
            self.assertNotIn("dry-run story", (Path(d) / "dist" / "index.html").read_text(encoding="utf-8"))

    def test_one_category_summaries_fail_only_that_category_falls_back(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", fail_when="(football)", seed=self._seed())
            today = datetime.now(AEST).strftime("%Y-%m-%d")
            m = read_json(d, "memory.json")
            stories = m["stories"][today]
            self.assertNotIn("football", stories)                              # not overwritten with empty-summary stories
            for cat in ("breaking", "australia", "archaeology"):
                self.assertTrue(stories[cat][0]["summary"].strip(), cat)       # others refreshed
            self.assertFalse([s for c in stories.values() for s in c if not s["summary"].strip()])
            self.assertFalse([k for k, v in (m.get("summaries") or {}).items() if not v.strip()])   # no empty cached
            h = read_json(d, "health.json")
            self.assertEqual(h["runs"][-1]["outcome"], "degraded")
            self.assertGreater(h["last_successful_data_update"], self.ts)
            self.assertIn("OLD football headline", (Path(d) / "dist" / "index.html").read_text(encoding="utf-8"))

    def test_category_run_whose_summaries_fail_keeps_old_stories(self):
        with workdir() as d:
            run_pipeline(d, "category", "ok", category="football", fail_when="In 3-4 sentences", seed=self._seed())
            self.assertEqual(read_json(d, "memory.json"), seeded_memory())
            self.assertEqual(read_json(d, "health.json")["runs"][-1]["outcome"], "failed")


class D_HealthDot(unittest.TestCase):
    def _dot(self, run, now=None):
        from page.builder import build_html
        health = {"runs": [run], "last_successful_data_update": hrs(1)}
        html = build_html({}, {}, [], health=health)
        colour = re.search(r"border-radius:50%;background:(#\w+)", html).group(1)
        tip = re.search(r'class="health-tooltip"[^>]*>([^<]*)<', html).group(1)
        return colour, tip, html

    def test_failed_run_is_red_and_says_how_many_calls_failed(self):
        colour, tip, _ = self._dot({"run_type": "full", "outcome": "failed", "claude_calls": 8,
                                    "claude_calls_errored": 8, "errors": [], "reasons": ["all 8 Claude calls failed"]})
        self.assertEqual(colour, "#e74c3c")
        self.assertIn("failed", tip); self.assertIn("8 of 8 Claude calls failed", tip)

    def test_degraded_is_orange(self):
        colour, tip, _ = self._dot({"run_type": "full", "outcome": "degraded", "claude_calls": 12,
                                    "claude_calls_errored": 2, "errors": []})
        self.assertEqual(colour, "#e67e22")
        self.assertIn("degraded", tip); self.assertIn("2 of 12 Claude calls failed", tip)

    def test_ok_is_green(self):
        colour, tip, _ = self._dot({"run_type": "full", "outcome": "ok", "claude_calls": 12, "claude_calls_errored": 0, "errors": []})
        self.assertEqual(colour, "#2ecc71"); self.assertIn("ok", tip)

    def test_legacy_run_without_outcome_still_renders(self):
        self.assertEqual(self._dot({"run_type": "full", "status": "ok", "errors": []})[0], "#2ecc71")
        self.assertEqual(self._dot({"run_type": "full", "errors": ["GDELT fetch failed"]})[0], "#e67e22")

    def test_tooltip_is_escaped(self):
        evil = "<script>alert(1)</script>"
        _, tip, html = self._dot({"run_type": "full", "outcome": "failed", "claude_calls": 1, "claude_calls_errored": 1,
                                  "errors": [evil], "reasons": [evil]})
        self.assertNotIn(evil, html)
        self.assertIn("&lt;script&gt;", tip)


if __name__ == "__main__":
    unittest.main()
