"""Run: python -m unittest tests.test_trending_off  (dry run, no network, no spend)."""
import unittest

from tests.harness import run_pipeline, workdir, read_json
from pathlib import Path


class TrendingOff(unittest.TestCase):
    def _labels(self, d):
        return {r["label"] for r in (read_json(d, "cost_log.json") or [])}

    def test_full_run_makes_no_world_topics_calls_and_renders(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok")
            labels = self._labels(d)
            self.assertTrue(labels, "dry run should have made (fake) Claude calls")
            self.assertFalse([l for l in labels if l.startswith("world_topics")], labels)
            html = (Path(d) / "dist" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("What the world is talking about", html)
            self.assertIn("Developing situations", html)
            self.assertIn("Australia dry-run story", html)

    def test_world_topics_category_run_is_a_noop(self):
        with workdir() as d:
            out, _ = run_pipeline(d, "category", "ok", category="world_topics")
            self.assertIn("Unknown category", out)
            self.assertFalse(self._labels(d))

    def test_other_run_types_do_not_touch_trending(self):
        for mode in ("breaking_only", "deploy_only"):
            with workdir() as d:
                run_pipeline(d, mode, "ok")
                self.assertFalse([l for l in self._labels(d) if l.startswith("world_topics")], mode)
                self.assertTrue((Path(d) / "dist" / "index.html").exists(), mode)


if __name__ == "__main__":
    unittest.main()
