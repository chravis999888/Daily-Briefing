"""Run: python -m unittest tests.test_data_safety  (offline: fake Claude client, fake fetchers)."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests.harness import CREDIT_ERROR, run_pipeline, workdir, read_json
from safety import evaluate_run, failed_categories, claude_stats
from memory import AEST

OK = {"outcome": "ok", "label": "football_selection"}
ERR = {"outcome": "error", "label": "football_selection", "error": CREDIT_ERROR}


def seeded_memory():
    y = (datetime.now(AEST) - timedelta(days=1)).strftime("%Y-%m-%d")
    story = lambda h: {"headline": h, "timestamp": "1 day ago", "score": 7, "summary": "old", "url": "https://example.com/old",
                       "image": "", "articles": [], "tracking_suggestions": []}
    return {"stories": {y: {"breaking": [story("OLD breaking headline")], "australia": [story("OLD Australia headline")],
                            "archaeology": [story("OLD archaeology headline")], "football": [story("OLD football headline")]}},
            "article_hashes": {"australia": "oldhash"}}


class Rule(unittest.TestCase):
    def test_all_claude_calls_failed_is_failed(self):
        v = evaluate_run("full", {"stories": {}}, [ERR] * 5, True, fresh={c: [] for c in ["breaking", "australia", "archaeology", "football"]})
        self.assertFalse(v["good"]); self.assertEqual(v["outcome"], "failed"); self.assertFalse(v["advance"])
        self.assertIn("credit balance is too low", " ".join(v["reasons"]))
        self.assertEqual(v["claude_calls_errored"], 5)

    def test_some_failed_is_degraded_but_good(self):
        v = evaluate_run("full", {"stories": {}}, [OK, ERR], True, fresh={"football": [{"headline": "x"}]})
        self.assertTrue(v["good"]); self.assertEqual(v["outcome"], "degraded"); self.assertTrue(v["advance"])

    def test_no_calls_and_sources_up_is_ok(self):
        v = evaluate_run("breaking_only", {"stories": {}}, [], True)
        self.assertEqual((v["good"], v["outcome"]), (True, "ok"))

    def test_no_source_returned_anything_is_failed(self):
        self.assertFalse(evaluate_run("breaking_only", {"stories": {}}, [], False)["good"])

    def test_full_run_needs_one_category_with_picks(self):
        empty = {c: [] for c in ["breaking", "australia", "archaeology", "football"]}
        self.assertFalse(evaluate_run("full", {"stories": {}}, [OK], True, fresh=empty)["good"])
        self.assertTrue(evaluate_run("full", {"stories": {}}, [OK], True, fresh={**empty, "football": [{"headline": "h"}]})["good"])

    def test_malformed_story_and_corrupt_memory_fail(self):
        bad = {"football": [{"headline": "  "}]}
        self.assertFalse(evaluate_run("full", {"stories": {}}, [OK], True, fresh=bad)["good"])
        self.assertFalse(evaluate_run("breaking_only", {"stories": []}, [], True)["good"])
        self.assertFalse(evaluate_run("breaking_only", None, [], True)["good"])

    def test_deploy_only_is_ok_but_never_advances(self):
        v = evaluate_run("deploy_only", {"stories": {}}, [], True)
        self.assertEqual((v["good"], v["advance"]), (True, False))

    def test_failed_categories(self):
        rows = [{"label": "australia_selection", "outcome": "error"}, {"label": "australia_selection:haiku_fallback", "outcome": "error"},
                {"label": "football_selection", "outcome": "error"}, {"label": "football_selection:haiku_fallback", "outcome": "ok"}]
        self.assertEqual(failed_categories(rows), {"australia"})


class Pipeline(unittest.TestCase):
    def test_all_claude_calls_fail_old_data_survives(self):
        with workdir() as d:
            seed = {"memory.json": seeded_memory(), "health.json": {"runs": [], "errors": []}}
            out, rc = run_pipeline(d, "full", "error", seed=seed)
            self.assertEqual(read_json(d, "memory.json"), seeded_memory())     # untouched
            html = (Path(d) / "dist" / "index.html").read_text(encoding="utf-8")
            self.assertIn("OLD football headline", html)
            self.assertNotIn("dry-run story", html)
            h = read_json(d, "health.json")
            self.assertEqual(h["runs"][-1]["outcome"], "failed")
            self.assertEqual(h["consecutive_failures"], 1)
            self.assertGreater(h["runs"][-1]["claude_calls_errored"], 0)
            ledger = read_json(d, "cost_log.json")
            self.assertTrue(ledger and all(r["outcome"] == "error" for r in ledger))   # ledger still written
            self.assertIn("RUN NOT GOOD", out)
            run_pipeline(d, "full", "error")                                    # second failure
            self.assertEqual(read_json(d, "health.json")["consecutive_failures"], 2)

    def test_good_run_updates_memory_and_resets_failures(self):
        with workdir() as d:
            seed = {"memory.json": seeded_memory(), "health.json": {"runs": [], "errors": [], "consecutive_failures": 3}}
            run_pipeline(d, "full", "ok", seed=seed)
            self.assertNotEqual(read_json(d, "memory.json"), seeded_memory())
            h = read_json(d, "health.json")
            self.assertEqual((h["runs"][-1]["outcome"], h["consecutive_failures"]), ("ok", 0))

    def test_sources_down_keeps_old_data(self):
        with workdir() as d:
            run_pipeline(d, "breaking_only", "ok", sources="down", seed={"memory.json": seeded_memory()})
            self.assertEqual(read_json(d, "memory.json"), seeded_memory())
            self.assertEqual(read_json(d, "health.json")["runs"][-1]["outcome"], "failed")

    def test_one_category_failing_keeps_its_old_stories_and_retries(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", fail_when="Australian news editor", seed={"memory.json": seeded_memory()})
            html = (Path(d) / "dist" / "index.html").read_text(encoding="utf-8")
            today = datetime.now(AEST).strftime("%Y-%m-%d")
            stories = read_json(d, "memory.json")["stories"][today]
            self.assertNotIn("australia", stories)       # nothing new stored: page falls back to the old stories
            self.assertIn("dry-run story", stories["football"][0]["headline"])                # others refreshed
            self.assertIn("OLD Australia headline", html)
            self.assertEqual(read_json(d, "memory.json")["article_hashes"].get("australia"), "oldhash")  # retry next time
            h = read_json(d, "health.json")
            self.assertEqual(h["runs"][-1]["outcome"], "degraded")
            self.assertEqual(h["consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main()
