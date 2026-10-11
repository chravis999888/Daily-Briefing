"""Monthly spend cap + per-category Claude switches. Offline: python -m unittest tests.test_cap"""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import api
import costs
from memory import AEST
from page.builder import build_html, footer_stamp
from pricing import USD_TO_AUD
from tests.harness import run_pipeline, workdir, read_json
from tests.test_data_safety import seeded_memory

ALL_CATS = ("breaking", "australia", "archaeology", "football")
CAP = costs.DEFAULT_MONTHLY_CAP_AUD


def row(aud, when=None, outcome="ok", **kw):
    """A ledger row that cost `aud` Australian dollars."""
    r = {"timestamp": (when or datetime.now(AEST)).isoformat(), "run_id": "t", "run_type": "full",
         "label": "seed", "category": "other", "model": "claude-sonnet-4-6", "outcome": outcome,
         "cost_usd": aud / USD_TO_AUD, "input_tokens": 0, "output_tokens": 0}
    r.update(kw)
    return r


def spent_seed(aud, **health):
    """Seed files for a pipeline run whose month-to-date spend is already `aud`."""
    h = {"runs": [], "errors": [], "cap_month": datetime.now(AEST).strftime("%Y-%m"),
         "cap_alerted_80": True, "cap_alerted_100": True}
    h.update(health)
    return {"memory.json": seeded_memory(), "cost_log.json": [row(aud)], "health.json": h}


def ledger(d):
    return read_json(d, "cost_log.json") or []


def in_dir(d):
    class _Cd:
        def __enter__(self):
            self.old = os.getcwd(); os.chdir(d)

        def __exit__(self, *a):
            os.chdir(self.old)
    return _Cd()


class A_Maths(unittest.TestCase):
    def test_default_cap_is_15_and_env_overrides(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("MONTHLY_CAP_AUD", None)
            self.assertEqual(costs.monthly_cap_aud(), 15)
            os.environ["MONTHLY_CAP_AUD"] = "40"
            self.assertEqual(costs.monthly_cap_aud(), 40)
            os.environ["MONTHLY_CAP_AUD"] = "garbage"
            self.assertEqual(costs.monthly_cap_aud(), 15)

    def test_one_named_constant_for_120_percent_and_the_existing_rate(self):
        self.assertEqual(costs.HARD_STOP_FACTOR, 1.2)
        self.assertEqual(USD_TO_AUD, 1.55)

    def test_states(self):
        for spent, want in ((0, "ok"), (11.99, "ok"), (12, "warn_80"), (14.99, "warn_80"), (15, "capped"),
                            (17.99, "capped"), (18, "hard_stop"), (30, "hard_stop")):
            self.assertEqual(costs.cap_state(spent, 15), want, spent)

    def test_month_to_date_sums_the_current_brisbane_month_in_aud(self):
        now = datetime(2026, 10, 15, 9, 0, tzinfo=AEST)
        rows = [row(1.0, now), row(2.0, now - timedelta(days=3)), row(100, now - timedelta(days=20)),   # September
                row(0.5, now, outcome="error"), row(0, now, outcome="skipped")]
        self.assertAlmostEqual(costs.month_to_date_aud(rows, now), 3.5)

    def test_pre_52_rows_without_outcome_are_ignored(self):
        now = datetime(2026, 10, 15, 9, 0, tzinfo=AEST)
        legacy = {"timestamp": now.isoformat(), "cost_usd": 50.0}
        self.assertAlmostEqual(costs.month_to_date_aud([legacy, row(1.0, now)], now), 1.0)

    def test_month_boundary_is_brisbane_time_not_utc(self):
        now = datetime(2026, 10, 20, 9, 0, tzinfo=AEST)
        utc = timezone.utc
        rows = [row(1.0, datetime(2026, 9, 30, 15, 0, tzinfo=utc)),     # = 1 Oct 01:00 Brisbane: October
                row(2.0, datetime(2026, 9, 30, 13, 59, tzinfo=utc)),    # = 30 Sep 23:59 Brisbane: September
                row(4.0, datetime(2026, 10, 31, 13, 59, tzinfo=utc)),   # = 31 Oct 23:59 Brisbane: October
                row(8.0, datetime(2026, 10, 31, 14, 0, tzinfo=utc))]    # = 1 Nov 00:00 Brisbane: November
        self.assertAlmostEqual(costs.month_to_date_aud(rows, now), 5.0)

    def test_empty_ledger_is_zero_and_ok(self):
        with workdir() as d, in_dir(d):
            self.assertEqual(costs.month_to_date_aud(), 0)
            self.assertEqual(costs.cap_state(), "ok")
            self.assertFalse(costs.cap_blocks("australia"))
            self.assertIsNone(api.claude_blocked("australia"))     # the cap works with no ledger rows at all


class B_Gate(unittest.TestCase):
    def blocked(self, d, aud):
        Path(d, "cost_log.json").write_text(json.dumps([row(aud)]))
        with in_dir(d):
            return {c: api.claude_blocked(c) for c in ALL_CATS + ("developing", "summary")}

    def test_under_cap_everything_proceeds(self):
        with workdir() as d:
            self.assertEqual(set(self.blocked(d, 5).values()), {None})

    def test_at_cap_only_breaking_proceeds(self):
        with workdir() as d:
            got = self.blocked(d, 15)
            self.assertIsNone(got["breaking"])
            self.assertTrue(all(v == "cap" for k, v in got.items() if k != "breaking"), got)

    def test_at_120_percent_everything_is_blocked_breaking_too(self):
        with workdir() as d:
            self.assertEqual(set(self.blocked(d, 18).values()), {"cap"})

    def test_global_off_beats_per_category_on(self):
        with workdir() as d, mock.patch.dict(os.environ, {"CLAUDE_ENABLED": "false", "CLAUDE_BREAKING": "true"}):
            self.assertEqual(set(self.blocked(d, 0).values()), {"claude_disabled"})

    def test_each_category_flag_turns_off_only_its_own_category(self):
        flags = {"breaking": "CLAUDE_BREAKING", "australia": "CLAUDE_AUSTRALIA", "archaeology": "CLAUDE_ARCHAEOLOGY",
                 "football": "CLAUDE_FOOTBALL", "developing": "CLAUDE_TRACKED"}
        for cat, var in flags.items():
            for off in ("false", "0", "OFF", "no"):
                with workdir() as d, mock.patch.dict(os.environ, {var: off}):
                    got = self.blocked(d, 0)
                    self.assertEqual({k for k, v in got.items() if v}, {cat}, (var, off))
                    self.assertEqual(got[cat], "category_off")

    def test_api_calls_are_skipped_with_a_reason_and_make_no_request(self):
        with workdir() as d, mock.patch.object(api, "client", None):     # a real call would raise
            Path(d, "cost_log.json").write_text(json.dumps([row(18)]))
            with in_dir(d), api.category_scope("breaking"):
                self.assertEqual(api.call_sonnet("p", label="story_summary"), "")
            rows = [r for r in ledger(d) if r["outcome"] == "skipped"]
            self.assertEqual((len(rows), rows[0]["reason"], rows[0]["category"]), (1, "cap", "breaking"))

    def test_scope_lets_a_generic_summary_call_follow_its_category(self):
        with workdir() as d:
            Path(d, "cost_log.json").write_text(json.dumps([row(15)]))
            with in_dir(d):
                with api.category_scope("breaking"):
                    self.assertIsNone(api.claude_blocked("breaking"))
                with mock.patch.object(api, "client", None):
                    with api.category_scope("football"):
                        self.assertEqual(api.call_haiku("p", label="story_summary"), "")
                self.assertEqual([r["category"] for r in costs.load_rows() if r["outcome"] == "skipped"], ["football"])


class C_PipelineCap(unittest.TestCase):
    def test_under_cap_runs_normally(self):
        with workdir() as d:
            _, rc = run_pipeline(d, "full", "ok", seed=spent_seed(1))
            h = read_json(d, "health.json")
            self.assertEqual((rc, h["runs"][-1]["outcome"], h["cap_state"]), (0, "ok", "ok"))
            self.assertFalse([r for r in ledger(d) if r["outcome"] == "skipped"])

    def test_at_cap_breaking_proceeds_others_skipped_with_reason_and_run_is_degraded_not_failed(self):
        with workdir() as d:
            _, rc = run_pipeline(d, "full", "ok", seed=spent_seed(15.05))
            rows = ledger(d)
            skipped = [r for r in rows if r["outcome"] == "skipped"]
            self.assertTrue({"australia", "archaeology", "football"} <= {r["category"] for r in skipped})
            self.assertTrue(all(r["reason"] == "cap" and r["candidates"] != 0 for r in skipped if r["label"].endswith("selection")))
            ran = {r["category"] for r in rows if r["outcome"] == "ok" and r["label"] != "seed"}
            self.assertIn("breaking", ran)
            self.assertFalse(ran & {"australia", "archaeology", "football"})
            h = read_json(d, "health.json")
            run = h["runs"][-1]
            self.assertEqual((run["outcome"], run["data_good"], rc), ("degraded", True, 0))
            self.assertEqual(h["consecutive_failures"], 0)
            self.assertEqual((h["cap_state"], h["monthly_cap_aud"]), ("capped", 15))
            self.assertGreater(h["month_to_date_aud"], 15)
            today = datetime.now(AEST).strftime("%Y-%m-%d")
            stories = read_json(d, "memory.json")["stories"].get(today, {})
            self.assertEqual(stories["football"][0]["headline"], "OLD football headline")   # kept as is
            self.assertIn("OLD football headline", (Path(d) / "dist" / "index.html").read_text(encoding="utf-8"))

    def test_cap_blocked_articles_are_not_marked_seen_so_they_are_retried(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", seed=spent_seed(15.05), salt=9)
            store = read_json(d, "memory.json")["article_store"]
            self.assertEqual({r["category"] for r in store.values()}, {"breaking"})

    def test_hard_stop_blocks_everything_and_keeps_all_stories(self):
        with workdir() as d:
            _, rc = run_pipeline(d, "full", "ok", seed=spent_seed(19))
            rows = ledger(d)
            self.assertEqual({r["outcome"] for r in rows if r["label"] != "seed"}, {"skipped"})
            self.assertEqual({r["reason"] for r in rows if r["outcome"] == "skipped"}, {"cap"})
            h = read_json(d, "health.json")
            self.assertEqual((h["runs"][-1]["outcome"], h["cap_state"], rc), ("degraded", "hard_stop", 0))
            self.assertEqual(h["runs"][-1]["reason"], "cap")
            self.assertEqual(read_json(d, "memory.json")["stories"], seeded_memory()["stories"])

    def test_capped_breaking_only_run_is_degraded_not_failed(self):
        with workdir() as d:
            run_pipeline(d, "breaking_only", "ok", seed=spent_seed(19))
            self.assertEqual(read_json(d, "health.json")["runs"][-1]["outcome"], "degraded")

    def test_cap_works_with_an_empty_ledger_and_a_tiny_cap(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", env={"MONTHLY_CAP_AUD": "0.001"}, seed={"memory.json": seeded_memory()})
            h = read_json(d, "health.json")                  # first calls push spend past the cap mid-run
            self.assertIn(h["cap_state"], ("capped", "hard_stop"))
            self.assertTrue([r for r in ledger(d) if r["outcome"] == "skipped" and r["reason"] == "cap"])
            self.assertEqual(h["runs"][-1]["outcome"], "degraded")


class D_Alerts(unittest.TestCase):
    def update(self, d, aud, health):
        Path(d, "cost_log.json").write_text(json.dumps([row(aud)]))
        health.setdefault("runs", [{"alerted": False}])
        health["runs"][-1]["alerted"] = False
        health["runs"][-1].pop("cap_alert", None)
        with in_dir(d):
            costs.update_cost_outputs(health)
        return health

    def test_80_and_100_each_alert_exactly_once_per_month(self):
        with workdir() as d:
            h = {}
            h = self.update(d, 5, h);   self.assertFalse(h["runs"][-1]["alerted"])
            h = self.update(d, 12.5, h); self.assertTrue(h["runs"][-1]["alerted"]); self.assertIn("80%", h["runs"][-1]["cap_alert"])
            self.assertEqual((h["cap_alerted_80"], h["cap_alerted_100"]), (True, False))
            h = self.update(d, 13, h);  self.assertFalse(h["runs"][-1]["alerted"])         # no second 80% alert
            h = self.update(d, 15.1, h); self.assertTrue(h["runs"][-1]["alerted"]); self.assertIn("100%", h["runs"][-1]["cap_alert"])
            h = self.update(d, 16, h);  self.assertFalse(h["runs"][-1]["alerted"])         # no second 100% alert
            h = self.update(d, 25, h);  self.assertFalse(h["runs"][-1]["alerted"])         # not at 120% either
            self.assertEqual((h["cap_state"], h["month_to_date_aud"] > 15), ("hard_stop", True))

    def test_jumping_past_both_thresholds_alerts_once(self):
        with workdir() as d:
            h = self.update(d, 16, {})
            self.assertTrue(h["runs"][-1]["alerted"])
            self.assertEqual((h["cap_alerted_80"], h["cap_alerted_100"]), (True, True))

    def test_flags_reset_when_the_month_rolls_and_alert_again(self):
        with workdir() as d:
            h = {"cap_month": "2000-01", "cap_alerted_80": True, "cap_alerted_100": True}
            h = self.update(d, 1, h)
            self.assertEqual((h["cap_month"], h["cap_alerted_80"], h["cap_alerted_100"]),
                             (datetime.now(AEST).strftime("%Y-%m"), False, False))
            h = self.update(d, 13, h)
            self.assertTrue(h["runs"][-1]["alerted"])

    def test_pipeline_fails_the_actions_run_once_at_80_percent(self):
        with workdir() as d:
            seed = spent_seed(12.2, cap_alerted_80=False, cap_alerted_100=False)
            _, rc1 = run_pipeline(d, "full", "ok", seed=seed)
            h = read_json(d, "health.json")
            self.assertEqual((rc1, h["cap_alerted_80"], h["cap_alerted_100"]), (1, True, False))
            self.assertTrue((Path(d) / "dist" / "index.html").exists())          # build + deploy still happen
            self.assertEqual(h["runs"][-1]["outcome"], "ok")                       # an alert is not a failure
            _, rc2 = run_pipeline(d, "full", "ok")
            self.assertEqual(rc2, 0)


class E_CategorySwitches(unittest.TestCase):
    def test_one_category_off_skips_only_it_and_keeps_its_stories(self):
        with workdir() as d:
            _, rc = run_pipeline(d, "full", "ok", env={"CLAUDE_AUSTRALIA": "false"}, seed={"memory.json": seeded_memory()})
            rows = ledger(d)
            skipped = [r for r in rows if r["outcome"] == "skipped"]
            self.assertEqual([(r["label"], r["category"], r["reason"]) for r in skipped],
                             [("australia_selection", "australia", "category_off")])
            self.assertGreater(skipped[0]["candidates"], 0)
            self.assertFalse([r for r in rows if r["category"] == "australia" and r["outcome"] != "skipped"])
            for cat in ("breaking", "archaeology", "football"):
                self.assertTrue([r for r in rows if r["category"] == cat and r["outcome"] == "ok"], cat)
            today = datetime.now(AEST).strftime("%Y-%m-%d")
            self.assertEqual(read_json(d, "memory.json")["stories"][today]["australia"][0]["headline"],
                             "OLD Australia headline")                                       # kept as is
            self.assertIn("OLD Australia headline", (Path(d) / "dist" / "index.html").read_text(encoding="utf-8"))
            h = read_json(d, "health.json")
            self.assertEqual((rc, h["runs"][-1]["outcome"], h["consecutive_failures"]), (0, "ok", 0))
            store = read_json(d, "memory.json")["article_store"]
            self.assertNotIn("australia", {r["category"] for r in store.values()})          # retried when switched on

    def test_all_categories_off_is_a_healthy_quiet_run(self):
        env = {v: "false" for v in ("CLAUDE_BREAKING", "CLAUDE_AUSTRALIA", "CLAUDE_ARCHAEOLOGY", "CLAUDE_FOOTBALL", "CLAUDE_TRACKED")}
        with workdir() as d:
            _, rc = run_pipeline(d, "full", "error", env=env, seed={"memory.json": seeded_memory()})
            run = read_json(d, "health.json")["runs"][-1]
            self.assertEqual((rc, run["outcome"], run["reason"]), (0, "ok", "category_off"))
            self.assertEqual({r["reason"] for r in ledger(d)}, {"category_off"})
            self.assertEqual(read_json(d, "memory.json")["stories"], seeded_memory()["stories"])

    def test_tracked_switch_controls_only_the_developing_call(self):
        for off, expect_developing in (("false", False), ("true", True)):
            with workdir() as d:
                Path(d, "pinned.txt").write_text("Ukraine war\n", encoding="utf-8")
                run_pipeline(d, "full", "ok", env={"CLAUDE_TRACKED": off}, seed={"memory.json": seeded_memory()})
                dev = [r for r in ledger(d) if r["category"] == "developing"]
                self.assertEqual(any(r["outcome"] == "ok" for r in dev), expect_developing, off)
                if not expect_developing:
                    self.assertEqual([(r["outcome"], r["reason"]) for r in dev], [("skipped", "category_off")])
                    self.assertTrue([r for r in ledger(d) if r["category"] == "breaking" and r["outcome"] == "ok"])
                    self.assertEqual(read_json(d, "health.json")["runs"][-1]["outcome"], "ok")

    def test_global_off_beats_category_on_in_a_pipeline_run(self):
        with workdir() as d:
            run_pipeline(d, "full", "error", claude_enabled="0", env={"CLAUDE_BREAKING": "true"},
                         seed={"memory.json": seeded_memory()})
            self.assertEqual({r["reason"] for r in ledger(d)}, {"claude_disabled"})
            self.assertEqual({r["outcome"] for r in ledger(d)}, {"skipped"})


class F_Show(unittest.TestCase):
    def test_rollup_and_summary_show_cap_and_skips_by_reason(self):
        with workdir() as d, in_dir(d):
            Path("cost_log.json").write_text(json.dumps([row(3.0)]))
            costs.log_skipped("a_selection", "australia", 5, reason="cap")
            costs.log_skipped("b_selection", "football", 5, reason="category_off")
            costs.log_skipped("c_selection", "breaking", 5, reason="category_off")
            old = costs.log_skipped("d_selection", "breaking", 5)          # Step 2 row, reason added later
            rows = costs.load_rows(); [r.pop("reason") for r in rows if r["label"] == "d_selection"]
            Path("cost_log.json").write_text(json.dumps(rows))
            health = costs.update_cost_outputs({"runs": []})
            today = health["api_cost_ledger"]["today"]
            self.assertEqual(today["skipped_by_reason"], {"cap": 1, "category_off": 2, "claude_disabled": 1})
            cap = health["api_cost_ledger"]["cap"]
            self.assertEqual((cap["monthly_cap_aud"], cap["percent_used"], cap["state"]), (15, 20.0, "ok"))
            md = Path("COST_SUMMARY.md").read_text(encoding="utf-8")
            self.assertIn("A$3.00 of A$15.00 used this month (20.0%)", md)
            for text in ("category_off 2", "cap 1", "claude_disabled 1"):
                self.assertIn(text, md)

    def test_footer_stamp_has_sha_and_percent_not_dollars(self):
        health = {"month_to_date_aud": 7.5, "monthly_cap_aud": 15}
        self.assertEqual(footer_stamp(health, sha="abcdef1234567"), "abcdef1 · 50% of cap")
        self.assertEqual(footer_stamp(health, sha=""), "50% of cap")
        self.assertEqual(footer_stamp({}, sha="abcdef1234567"), "abcdef1")
        self.assertEqual(footer_stamp(None, sha=""), "")
        with mock.patch.dict(os.environ, {"GITHUB_SHA": "0123456789abcdef"}):
            html = build_html({}, {}, [], health=health)
        self.assertIn("Page built", html)
        self.assertIn("0123456 · 50% of cap", html)
        self.assertNotIn("$7.5", html)

    def test_page_with_no_cap_data_still_renders(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("GITHUB_SHA", None)
            self.assertNotIn(" of cap", build_html({}, {}, [], health={}))

    def test_workflow_wires_the_switches_and_cap(self):
        wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "briefing.yml").read_text(encoding="utf-8")
        for v in ("CLAUDE_BREAKING", "CLAUDE_AUSTRALIA", "CLAUDE_ARCHAEOLOGY", "CLAUDE_FOOTBALL", "CLAUDE_TRACKED"):
            self.assertIn(f"{v}: ${{{{ vars.{v} || 'true' }}}}", wf)
        self.assertIn("MONTHLY_CAP_AUD: ${{ vars.MONTHLY_CAP_AUD || '15' }}", wf)


if __name__ == "__main__":
    unittest.main()
