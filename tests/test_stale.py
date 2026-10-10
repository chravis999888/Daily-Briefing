"""Run: python -m unittest tests.test_stale  (offline)."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests.harness import run_pipeline, workdir, read_json
from tests.test_data_safety import seeded_memory
from memory import AEST
from page.builder import build_html, freshness
from safety import STALE_AFTER_HOURS, is_stale

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=AEST)


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


class Freshness(unittest.TestCase):
    def test_threshold_is_8_hours_named_constant(self):
        self.assertEqual(STALE_AFTER_HOURS, 8)

    def test_fresh_shows_update_time_not_build_time(self):
        text, warn = freshness({"last_successful_data_update": ago(hours=1)}, NOW)
        self.assertEqual((text, warn), ("11:00 AM AEST", ""))

    def test_boundary(self):
        self.assertFalse(is_stale(ago(hours=8), NOW))
        self.assertTrue(is_stale(ago(hours=8, minutes=1), NOW))

    def test_stale_warns_with_date_when_not_today(self):
        text, warn = freshness({"last_successful_data_update": ago(days=2)}, NOW)
        self.assertIn("09 Oct", text)
        self.assertIn("Stale data", warn)

    def test_missing_or_garbage_is_unknown_and_stale(self):
        for health in (None, {}, {"runs": []}, {"last_successful_data_update": "garbage"}):
            text, warn = freshness(health, NOW)
            self.assertEqual(text, "unknown"); self.assertIn("unknown", warn)

    def test_page_renders_banner_and_stays_escaped(self):
        evil = {"headline": "<script>alert(1)</script>", "score": 5, "summary": "s", "image": "javascript:x",
                "url": "javascript:alert(1)", "articles": [{"title": "t", "source": "s", "url": "javascript:alert(2)"}],
                "timestamp": "", "tracking_suggestions": []}
        html = build_html({"breaking": [evil]}, {}, [], health={"last_successful_data_update": ago(hours=20)}, now=NOW)
        self.assertIn('id="stale-warning"', html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn("Updated 12:00", html)   # build time must not appear as the update time

    def test_no_banner_when_fresh(self):
        html = build_html({}, {}, [], health={"last_successful_data_update": ago(hours=1)}, now=NOW)
        self.assertNotIn('id="stale-warning"', html)


class Pipeline(unittest.TestCase):
    def _html(self, d):
        return (Path(d) / "dist" / "index.html").read_text(encoding="utf-8")

    def test_failed_run_does_not_move_timestamp_and_stale_page_warns(self):
        old = (datetime.now(AEST) - timedelta(hours=9)).isoformat()
        with workdir() as d:
            run_pipeline(d, "full", "error", seed={"memory.json": seeded_memory(),
                                                   "health.json": {"runs": [], "errors": [], "last_successful_data_update": old}})
            self.assertEqual(read_json(d, "health.json")["last_successful_data_update"], old)
            self.assertIn('id="stale-warning"', self._html(d))
            self.assertIn("OLD football headline", self._html(d))

    def test_failed_run_within_threshold_has_no_warning(self):
        recent = (datetime.now(AEST) - timedelta(hours=2)).isoformat()
        with workdir() as d:
            run_pipeline(d, "full", "error", seed={"memory.json": seeded_memory(),
                                                   "health.json": {"runs": [], "errors": [], "last_successful_data_update": recent}})
            self.assertNotIn('id="stale-warning"', self._html(d))

    def test_missing_timestamp_shows_unknown_warning_not_build_time(self):
        with workdir() as d:
            run_pipeline(d, "full", "error", seed={"memory.json": seeded_memory(), "health.json": {"runs": [], "errors": []}})
            self.assertIn("freshness unknown", self._html(d))
            self.assertNotIn("last_successful_data_update", read_json(d, "health.json"))

    def test_failed_run_deploys_only_when_stale(self):
        for hours, expect in ((9, True), (2, False)):
            ts = (datetime.now(AEST) - timedelta(hours=hours)).isoformat()
            with workdir() as d:
                run_pipeline(d, "full", "error", seed={"memory.json": seeded_memory(),
                                                       "health.json": {"runs": [], "errors": [], "last_successful_data_update": ts}})
                self.assertEqual((Path(d) / "dist" / ".deploy_needed").exists(), expect, hours)

    def test_good_run_sets_timestamp(self):
        old = (datetime.now(AEST) - timedelta(hours=9)).isoformat()
        with workdir() as d:
            run_pipeline(d, "full", "ok", seed={"memory.json": seeded_memory(),
                                                "health.json": {"runs": [], "errors": [], "last_successful_data_update": old}})
            self.assertGreater(read_json(d, "health.json")["last_successful_data_update"], old)
            self.assertNotIn('id="stale-warning"', self._html(d))

    def test_deploy_only_never_advances_timestamp(self):
        old = (datetime.now(AEST) - timedelta(hours=1)).isoformat()
        with workdir() as d:
            run_pipeline(d, "deploy_only", "ok", seed={"memory.json": seeded_memory(),
                                                       "health.json": {"runs": [], "errors": [], "last_successful_data_update": old}})
            self.assertEqual(read_json(d, "health.json")["last_successful_data_update"], old)


if __name__ == "__main__":
    unittest.main()
