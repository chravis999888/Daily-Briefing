"""Run: python -m unittest tests.test_costs  (no network, no API key needed)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pricing import compute_cost, UnknownModelError  # noqa: E402

SONNET, HAIKU = "claude-sonnet-4-6", "claude-haiku-4-5-20251001"


class CostMaths(unittest.TestCase):
    def test_sonnet(self):
        # 1000 in * $3/M = 0.003 ; 500 out * $15/M = 0.0075 ; total 0.0105
        self.assertAlmostEqual(compute_cost(SONNET, 1000, 500), 0.0105, places=10)

    def test_haiku(self):
        # 2000 in * $1/M = 0.002 ; 1000 out * $5/M = 0.005 ; total 0.007
        self.assertAlmostEqual(compute_cost(HAIKU, 2000, 1000), 0.007, places=10)

    def test_cached_input(self):
        # Sonnet: 100*3/M=0.0003 + 200*15/M=0.003 + 1000 write*3.75/M=0.00375 + 5000 read*0.30/M=0.0015
        self.assertAlmostEqual(compute_cost(SONNET, 100, 200, 1000, 5000), 0.00855, places=10)

    def test_web_search(self):
        # Haiku: 500*1/M=0.0005 + 100*5/M=0.0005 + 2 searches * $0.01 = 0.02 ; total 0.021
        self.assertAlmostEqual(compute_cost(HAIKU, 500, 100, web_search_requests=2), 0.021, places=10)

    def test_unknown_model_fails_loudly(self):
        with self.assertRaises(UnknownModelError):
            compute_cost("claude-made-up", 1, 1)


def resp(model, text="hi", stop="end_turn", i=1000, o=500, cw=0, cr=0, searches=0):
    usage = NS(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw,
               cache_read_input_tokens=cr,
               server_tool_use=NS(web_search_requests=searches) if searches else None)
    content = [NS(type="text", text=text)] if text is not None else []
    return NS(model=model, content=content, usage=usage, stop_reason=stop)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.messages = self

    def create(self, **kw):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Ledger(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        import costs
        import api
        self.costs, self.api = costs, api
        costs.LEDGER_FILE = Path("cost_log.json")
        costs.SUMMARY_FILE = Path("COST_SUMMARY.md")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def rows(self):
        return json.loads(Path("cost_log.json").read_text())

    def test_ok_truncated_empty_and_return_values_unchanged(self):
        self.api.client = FakeClient([resp(SONNET, "story"), resp(SONNET, "cut", stop="max_tokens"),
                                      resp(HAIKU, "")])
        self.assertEqual(self.api.call_sonnet("p", label="breaking_selection"), "story")
        self.assertEqual(self.api.call_sonnet("p", label="story_summary"), "cut")
        self.assertEqual(self.api.call_haiku("p", label="world_topics_today"), "")
        r = self.rows()
        self.assertEqual([x["outcome"] for x in r], ["ok", "truncated", "empty"])
        self.assertEqual(r[0]["cost_usd"], 0.0105)
        self.assertEqual(r[1]["stop_reason"], "max_tokens")
        self.assertEqual([x["category"] for x in r], ["breaking", "summary", "world_topics"])
        for k in ("run_id", "run_type", "timestamp", "cache_read_input_tokens"):
            self.assertIn(k, r[0])

    def test_search_fee_and_cache_logged(self):
        self.api.client = FakeClient([resp(HAIKU, "[]", i=500, o=100, searches=2),
                                      resp(SONNET, "[]", i=100, o=200, cw=1000, cr=5000, searches=0)])
        self.api.call_haiku_with_search("p", label="australia_context_search")
        self.api.call_sonnet_with_search("p", label="breaking_context_search")
        r = self.rows()
        self.assertEqual(r[0]["web_search_requests"], 2)
        self.assertAlmostEqual(r[0]["cost_usd"], 0.021)
        self.assertAlmostEqual(r[1]["cost_usd"], 0.00855)

    def test_failures_logged_zero_cost_and_fallback_has_own_row(self):
        self.api.client = FakeClient([RuntimeError("credit balance too low"), resp(HAIKU, "fallback text")])
        out = self.api.call_sonnet("p", label="football_selection")
        self.assertEqual(out, "fallback text")
        r = self.rows()
        self.assertEqual([x["outcome"] for x in r], ["error", "ok"])
        self.assertEqual(r[0]["model"], SONNET)
        self.assertEqual(r[0]["cost_usd"], 0.0)
        self.assertIn("credit balance", r[0]["error"])
        self.assertEqual(r[1]["label"], "football_selection:haiku_fallback")
        self.assertEqual(r[1]["category"], "football")

    def test_search_error_returns_empty_and_logs(self):
        self.api.client = FakeClient([RuntimeError("529 overloaded")])
        self.assertEqual(self.api.call_haiku_with_search("p", label="x_context_search"), "")
        self.assertEqual(self.rows()[0]["outcome"], "error")

    def test_rollup_summary_and_health_key(self):
        self.api.client = FakeClient([resp(SONNET, "ok"), resp(SONNET, "cut", stop="max_tokens")])
        self.api.call_sonnet("p", label="breaking_selection", retries=1)
        self.api.call_sonnet("p", label="australia_selection", retries=1)
        health = self.costs.update_cost_outputs({"runs": []})
        led = health["api_cost_ledger"]
        self.assertEqual(led["today"]["calls"], 2)
        self.assertAlmostEqual(led["today"]["total_usd"], 0.021)
        self.assertAlmostEqual(led["today"]["wasted_usd"], 0.0105)  # the truncated call
        self.assertAlmostEqual(led["month_to_date"]["total_aud"], 0.021 * 1.55)
        md = Path("COST_SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn("Wasted spend", md)
        self.assertIn("australia_selection", md)


if __name__ == "__main__":
    unittest.main()
