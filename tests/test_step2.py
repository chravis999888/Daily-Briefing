"""Step 2 (free fetch, article IDs, change detection). Offline: python -m unittest tests.test_step2"""
import json
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import api
import articles as art
import costs
import fetchers
from memory import AEST, log_run
from page.builder import build_html
from safety import evaluate_run
from tests.harness import run_pipeline, workdir, read_json
from tests.test_data_safety import seeded_memory

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=AEST)
ALL_CATS = ("breaking", "australia", "archaeology", "football")
AU_ARCH_FEEDS = {"ABC News", "SMH", "The Age", "NewsData", "Nature", "New Scientist", "Science", "PhysOrg",
                 "EurekAlert", "ScienceDaily", "The Conversation"}


def a(title, url=None, source="Test", published=""):
    return {"title": title, "url": url or f"https://example.com/{abs(hash(title))}", "source": source,
            "published": published, "time": ""}


def ledger(d):
    return read_json(d, "cost_log.json") or []


def real_calls(d):
    return [r for r in ledger(d) if r["outcome"] != "skipped"]


def fresh_dist(d):
    shutil.rmtree(Path(d) / "dist", ignore_errors=True)   # each Actions run starts without dist/


class A_ArticleIds(unittest.TestCase):
    def test_tracking_trailing_slash_host_case_fragment_and_param_order_give_the_same_id(self):
        base = "https://www.example.com/news/story-1?id=7&page=2"
        variants = ["HTTPS://WWW.EXAMPLE.COM/news/story-1/?id=7&page=2",
                    base + "&utm_source=twitter&utm_medium=social&utm_campaign=x",
                    base + "&fbclid=abc&gclid=def&ref=home&cmpid=zz",
                    "https://www.example.com/news/story-1?page=2&id=7#comments",
                    "https://www.Example.com/news/story-1/?page=2&id=7&utm_term=a#top"]
        want = art.article_id(base)
        self.assertEqual(len(want), 16)
        int(want, 16)
        for v in variants:
            self.assertEqual(art.article_id(v), want, v)

    def test_different_urls_give_different_ids(self):
        urls = ["https://example.com/a", "https://example.com/b", "https://example.com/a?id=1",
                "https://example.com/a?id=2", "https://other.com/a", "http://example.com/a"]
        self.assertEqual(len({art.article_id(u) for u in urls}), len(urls))

    def test_urlless_articles_do_not_all_collide(self):
        self.assertNotEqual(art.article_id("", "one|x"), art.article_id("", "two|x"))

    def test_published_is_parsed_to_iso_utc(self):
        for raw in ("2026-10-11T02:00:00Z", "Sun, 11 Oct 2026 12:00:00 +1000", "20261011T020000Z", "2026-10-11 02:00:00"):
            self.assertEqual(art.parse_published(raw), "2026-10-11T02:00:00+00:00", raw)
        self.assertEqual(art.parse_published("garbage"), "")
        self.assertEqual(art.parse_published(""), "")

    def test_every_fetcher_returns_id_source_title_url_and_published(self):
        rss = ("<?xml version='1.0'?><rss version='2.0'><channel><title>t</title><item><title>RSS story</title>"
               "<link>https://e.com/r/?utm_source=x</link><pubDate>Sun, 11 Oct 2026 12:00:00 +1000</pubDate>"
               "<description>d</description></item></channel></rss>")
        guardian = {"response": {"results": [{"webTitle": "G story", "webUrl": "https://g.com/1",
                                              "webPublicationDate": "2026-10-11T02:00:00Z", "fields": {}}]}}
        newsdata = {"results": [{"title": "N story", "link": "https://n.com/1", "source_id": "nsrc",
                                 "pubDate": "2026-10-11 02:00:00", "content": "c"}]}
        gdelt = {"articles": [{"title": "D story", "url": "https://d.com/1", "domain": "d.com",
                               "seendate": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}]}

        def resp(payload=None, text=""):
            return mock.Mock(status_code=200, text=text, content=text.encode(), json=lambda: payload)

        def get(url, **kw):
            self.assertEqual(kw.get("timeout"), fetchers.FETCH_TIMEOUT)    # every fetch has a short timeout
            if "guardianapis" in url:
                return resp(guardian)
            if "newsdata" in url:
                return resp(newsdata)
            if "gdeltproject" in url:
                return resp(gdelt)
            return resp(text=rss)

        with mock.patch.object(fetchers.requests, "get", get):
            found = {"rss": fetchers.fetch_rss("https://e.com/feed", "Wire"),
                     "guardian": fetchers.fetch_guardian("q"), "newsdata": fetchers.fetch_newsdata("q"),
                     "gdelt": fetchers.fetch_gdelt_articles("q")[0]}
        for name, got in found.items():
            self.assertEqual(len(got), 1, name)
            x = got[0]
            self.assertEqual(x["id"], art.article_id(x["url"]), name)
            self.assertTrue(x["source"] and x["title"] and x["url"], name)
            self.assertTrue(x["published"].endswith("+00:00"), (name, x["published"]))
        self.assertEqual(found["rss"][0]["id"], art.article_id("https://e.com/r"))
        self.assertEqual(found["rss"][0]["published"], "2026-10-11T02:00:00+00:00")
        self.assertEqual(found["rss"][0]["time"], "Sun, 11 Oct 2026 12:00:00 +1000")   # original field kept


class B_Store(unittest.TestCase):
    def test_new_vs_seen_split(self):
        memory = {}
        first = [a("Alpha beta gamma delta epsilon one"), a("Zeta eta theta iota kappa two")]
        r1 = art.classify_articles(memory, "australia", first, NOW)
        self.assertEqual((r1["new"], r1["has_new"], r1["fetched"]), (2, True, 2))
        again = [dict(x) for x in first] + [a("Lambda mu nu xi omicron three")]
        r2 = art.classify_articles(memory, "australia", again, NOW + timedelta(hours=1))
        self.assertEqual((r2["new"], r2["has_new"]), (1, True))
        self.assertEqual([x["is_new"] for x in again], [False, False, True])
        r3 = art.classify_articles(memory, "australia", [dict(x) for x in again], NOW + timedelta(hours=2))
        self.assertEqual((r3["new"], r3["has_new"]), (0, False))
        rec = memory["article_store"][again[2]["id"]]
        self.assertEqual(set(rec), {"source", "title", "published", "first_seen", "category"})
        self.assertEqual((rec["category"], rec["first_seen"]), ("australia", (NOW + timedelta(hours=1)).isoformat()))

    def test_same_article_in_two_categories_is_new_for_each(self):
        memory = {}
        item = a("Shared story about the senate vote on housing")
        self.assertTrue(art.classify_articles(memory, "breaking", [dict(item)], NOW)["has_new"])
        self.assertTrue(art.classify_articles(memory, "australia", [dict(item)], NOW)["has_new"])
        self.assertFalse(art.classify_articles(memory, "australia", [dict(item)], NOW)["has_new"])

    def test_prune_drops_records_older_than_7_days_only(self):
        memory = {"article_store": {
            "old": {"title": "t", "first_seen": (NOW - timedelta(days=7, minutes=1)).isoformat(), "category": "football"},
            "edge": {"title": "t", "first_seen": (NOW - timedelta(days=6, hours=23)).isoformat(), "category": "football"},
            "new": {"title": "t", "first_seen": NOW.isoformat(), "category": "football"},
            "bad": {"title": "t", "first_seen": "garbage", "category": "football"}}}
        art.prune_store(memory, NOW)
        self.assertEqual(set(memory["article_store"]), {"edge", "new"})

    def test_pipeline_prunes_on_every_run(self):
        old = {"title": "x", "source": "s", "published": "", "category": "football",
               "first_seen": (datetime.now(AEST) - timedelta(days=8)).isoformat()}
        with workdir() as d:
            m = seeded_memory(); m["article_store"] = {"stale1": old}
            run_pipeline(d, "full", "ok", seed={"memory.json": m})
            self.assertNotIn("stale1", read_json(d, "memory.json")["article_store"])

    def test_restore_forgets_a_categorys_new_records_only(self):
        original = {}
        memory = {}
        art.classify_articles(memory, "football", [a("Football one story title here today")], NOW)
        original = json.loads(json.dumps(memory))
        art.classify_articles(memory, "football", [a("Football two story title here now")], NOW)
        art.classify_articles(memory, "australia", [a("Australia one story title here today")], NOW)
        art.restore_store_category(memory, original, "football")
        self.assertEqual({r["title"] for r in memory["article_store"].values()},
                         {"Football one story title here today", "Australia one story title here today"})


class D_NearDuplicates(unittest.TestCase):
    def test_threshold_is_one_named_constant(self):
        self.assertEqual(art.DUPLICATE_THRESHOLD, 0.8)

    def test_same_story_from_two_outlets_merges_and_keeps_the_earliest(self):
        later = a("Arsenal beat Manchester City 2-1 at the Etihad - BBC Sport", published="2026-10-11T03:00:00+00:00")
        earlier = a("Arsenal beat Manchester City 2-1 at the Etihad | The Guardian", published="2026-10-11T01:00:00+00:00")
        info = art.classify_articles({}, "football", [later, earlier], NOW)
        self.assertEqual((info["new"], info["duplicates"]), (1, 1))
        self.assertTrue(earlier["is_new"])
        self.assertFalse(later["is_new"])
        self.assertEqual(later["duplicate_of"], earlier["id"])

    def test_punctuation_case_and_stop_words_are_ignored(self):
        x, y = a("Russia launches record missile barrage on Ukraine!"), a("RUSSIA LAUNCHES RECORD MISSILE BARRAGE ON UKRAINE")
        self.assertEqual(art.classify_articles({}, "breaking", [x, y], NOW)["new"], 1)

    def test_duplicate_against_the_last_48_hours_of_the_store(self):
        memory = {}
        art.classify_articles(memory, "football", [a("Real Madrid reach the Champions League final in Istanbul")], NOW)
        dup = a("Real Madrid reach the Champions League final in Istanbul - Marca")
        info = art.classify_articles(memory, "football", [dup], NOW + timedelta(hours=47))
        self.assertEqual((info["new"], info["duplicates"]), (0, 1))
        self.assertIn("duplicate_of", dup)
        after = a("Real Madrid reach the Champions League final in Istanbul | Marca")
        self.assertEqual(art.classify_articles(memory, "football", [after], NOW + timedelta(hours=49))["new"], 1)

    def test_near_misses_must_not_merge(self):
        pairs = [("Arsenal beat Manchester City 2-1 at the Etihad", "Arsenal beat Manchester City 3-1 at the Etihad"),
                 ("Earthquake kills 340 in Turkey Hatay province", "Earthquake kills 34 in Turkey Hatay province"),
                 ("Senate passes housing bill after Greens deal", "Senate rejects housing bill after Greens deal"),
                 ("Fire in Sydney", "Fire in Sydney"[::-1]),
                 ("Bayern win", "Bayern win again")]            # too short to ever merge
        for x, y in pairs:
            info = art.classify_articles({}, "breaking", [a(x), a(y)], NOW)
            self.assertEqual(info["new"], 2, (x, y))
        self.assertFalse(art.titles_match(art.normalise_title("Bayern win"), art.normalise_title("Bayern win")))

    def test_duplicates_are_stored_so_they_are_not_reconsidered_but_never_count_as_new(self):
        memory, first, dup = {}, a("Big wildfire destroys homes near Perth hills overnight"), \
            a("Big wildfire destroys homes near Perth hills overnight - ABC News")
        art.classify_articles(memory, "australia", [first, dup], NOW)
        self.assertEqual(memory["article_store"][dup["id"]]["duplicate_of"], first["id"])
        again = art.classify_articles(memory, "australia", [dict(first), dict(dup)], NOW + timedelta(hours=1))
        self.assertEqual((again["new"], again["has_new"]), (0, False))


class C_QuietRuns(unittest.TestCase):
    def test_category_with_no_new_articles_skips_its_claude_steps_and_keeps_stories(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", salt=7, seed={"memory.json": seeded_memory()})
            before = read_json(d, "memory.json")["stories"]
            n_calls = len(real_calls(d))
            fresh_dist(d)
            run_pipeline(d, "full", "ok", salt=8, stale_names=AU_ARCH_FEEDS, stale_salt=7)
            new_rows = real_calls(d)[n_calls:]
            cats = {r["category"] for r in new_rows}
            self.assertTrue({"breaking", "football"} <= cats)
            self.assertFalse([r for r in new_rows if r["label"].startswith(("australia", "archaeology"))])
            stats = read_json(d, "health.json")["runs"][-1]["categories"]
            self.assertEqual({c: stats[c]["has_new"] for c in ALL_CATS},
                             {"breaking": True, "football": True, "australia": False, "archaeology": False})
            self.assertEqual(stats["australia"]["new"], 0)
            self.assertGreater(stats["football"]["new"], 0)
            today = datetime.now(AEST).strftime("%Y-%m-%d")
            after = read_json(d, "memory.json")["stories"][today]
            self.assertEqual(after["australia"], before[today]["australia"])          # kept as is
            self.assertEqual(after["archaeology"], before[today]["archaeology"])
            self.assertNotEqual(after["football"], before[today]["football"])         # refreshed

    def test_all_categories_quiet_stops_the_run_with_no_rebuild_and_no_claude(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", salt=3, seed={"memory.json": seeded_memory()})
            mem_before, calls_before = read_json(d, "memory.json"), len(ledger(d))
            fresh_dist(d)
            out, rc = run_pipeline(d, "full", "ok", salt=3)
            self.assertEqual(rc, 0)
            self.assertIn("Quiet run", out)
            self.assertEqual(len(ledger(d)), calls_before)                     # zero Claude calls
            self.assertFalse((Path(d) / "dist").exists())                      # no page rebuild, no deploy
            self.assertEqual(read_json(d, "memory.json"), mem_before)          # nothing rewritten
            run = read_json(d, "health.json")["runs"][-1]
            self.assertEqual((run["outcome"], run["reason"], run["alerted"], run["data_good"]), ("ok", "no_new", False, True))
            self.assertTrue(all(not c["has_new"] for c in run["categories"].values()))

    def test_quiet_breaking_only_and_category_runs_also_stop(self):
        for mode, cat in (("breaking_only", ""), ("category", "football")):
            with workdir() as d:
                run_pipeline(d, "full", "ok", salt=3, seed={"memory.json": seeded_memory()})
                n = len(ledger(d)); fresh_dist(d)
                _, rc = run_pipeline(d, mode, "ok", category=cat, salt=3)
                self.assertEqual((rc, len(ledger(d)), (Path(d) / "dist").exists()), (0, n, False), mode)

    def test_quiet_healthy_overnight_is_not_failed_and_never_trips_the_stale_warning(self):
        t0 = datetime.now(AEST) - timedelta(hours=20)           # last real data update was 20h ago
        seed = {"memory.json": seeded_memory(),
                "health.json": {"runs": [], "errors": [], "consecutive_failures": 0,
                                "last_successful_data_update": t0.isoformat()}}
        with workdir() as d:
            run_pipeline(d, "full", "ok", salt=5, seed={"memory.json": seeded_memory()})   # fills the store
            h = read_json(d, "health.json"); h["last_successful_data_update"] = t0.isoformat()
            Path(d, "health.json").write_text(json.dumps(h))
            for _ in range(3):                                   # three quiet checks through the night
                _, rc = run_pipeline(d, "full", "ok", salt=5)
                self.assertEqual(rc, 0)
            h = read_json(d, "health.json")
            self.assertTrue(all(r["outcome"] == "ok" and r["reason"] == "no_new" for r in h["runs"][-3:]))
            self.assertEqual(h["consecutive_failures"], 0)
            self.assertEqual(h["last_successful_data_update"], t0.isoformat())     # no data was updated...
            self.assertGreater(h["last_successful_check"], t0.isoformat())         # ...but we did check
            self.assertFalse(h["runs"][-1]["banner_deploy"])
            html = build_html({}, {}, [], health=h)
            self.assertNotIn('id="stale-warning"', html)                            # no stale warning
            stale_without_check = {k: v for k, v in h.items() if k != "last_successful_check"}
            self.assertIn('id="stale-warning"', build_html({}, {}, [], health=stale_without_check))   # control

    def test_quiet_breaking_only_does_not_mask_a_failing_full_pipeline(self):     # issue #69
        t0 = (NOW - timedelta(hours=1)).isoformat()
        health = {"runs": [], "errors": [], "consecutive_failures": 3, "stale_alerted": True,
                  "last_successful_data_update": (NOW - timedelta(hours=20)).isoformat()}
        v = evaluate_run("breaking_only", {"stories": {}}, [], True, quiet="no_new")
        self.assertEqual((v["good"], v["outcome"], v["advance"], v["checked_all"]), (True, "ok", False, False))
        h = log_run(health, "breaking_only", [], v, [], [], NOW)
        self.assertEqual((h["consecutive_failures"], h["stale_alerted"]), (3, True))
        self.assertNotIn("last_successful_check", h)
        self.assertEqual(h["last_successful_data_update"], (NOW - timedelta(hours=20)).isoformat())
        v = evaluate_run("full", {"stories": {}}, [], True, quiet="no_new")       # a full quiet check does count
        h = log_run(h, "full", [], v, [], [], NOW)
        self.assertEqual((h["consecutive_failures"], h["stale_alerted"], h["last_successful_check"]), (0, False, NOW.isoformat()))

    def test_all_sources_failing_is_still_failed(self):
        for mode in ("full", "breaking_only"):
            with workdir() as d:
                seed = {"memory.json": seeded_memory(), "health.json": {"runs": [], "errors": []}}
                _, rc = run_pipeline(d, mode, "ok", sources="down", seed=seed)
                h = read_json(d, "health.json")
                self.assertEqual((h["runs"][-1]["outcome"], h["consecutive_failures"], rc), ("failed", 1, 1), mode)
                self.assertIn("no source returned any articles", " ".join(h["runs"][-1]["reasons"]))
                self.assertEqual(read_json(d, "memory.json"), seeded_memory())
                self.assertEqual(len(ledger(d)), 0)                              # and still no Claude calls

    def test_quiet_check_clears_a_live_stale_banner_once(self):
        old = (datetime.now(AEST) - timedelta(hours=10)).isoformat()
        with workdir() as d:
            run_pipeline(d, "full", "ok", salt=5, seed={"memory.json": seeded_memory()})
            h = read_json(d, "health.json")
            h.update(last_successful_data_update=old, stale_deployed=True, consecutive_failures=2)
            Path(d, "health.json").write_text(json.dumps(h))
            fresh_dist(d)
            run_pipeline(d, "full", "ok", salt=5)
            self.assertTrue((Path(d) / "dist" / ".deploy_needed").exists())
            self.assertNotIn('id="stale-warning"', (Path(d) / "dist" / "index.html").read_text(encoding="utf-8"))
            self.assertFalse(read_json(d, "health.json")["stale_deployed"])


class E_Logging(unittest.TestCase):
    def test_per_source_and_per_category_logging(self):
        with workdir() as d:
            run_pipeline(d, "full", "ok", salt=1, seed={"memory.json": seeded_memory()})
            h = read_json(d, "health.json")
            last = h["sources"]["Guardian"]["last_run"]
            self.assertEqual((last["ok"], last["count"], last["error"]), (True, 2, ""))   # called for breaking + football
            self.assertGreater(last["seconds"], 0)
            run = h["runs"][-1]
            fb = run["categories"]["football"]
            self.assertEqual(set(fb), {"fetched", "new", "duplicates", "has_new", "new_by_source"})
            self.assertEqual(fb["fetched"], 12)
            self.assertEqual(fb["new_by_source"]["Marca"], 1)
            self.assertEqual(sum(fb["new_by_source"].values()), fb["new"])

    def test_failed_source_keeps_its_error_text(self):
        with workdir() as d:
            run_pipeline(d, "breaking_only", "ok", sources="down", seed={"memory.json": seeded_memory()})
            last = read_json(d, "health.json")["sources"]["Guardian"]["last_run"]
            self.assertEqual((last["ok"], last["count"]), (False, 0))
            self.assertIn("dry-run network down", last["error"])

    def test_a_failing_or_hanging_feed_never_breaks_a_run(self):
        import requests
        with mock.patch.object(fetchers.requests, "get", side_effect=requests.exceptions.Timeout("read timed out")):
            fetchers.SOURCE_EVENTS.clear()
            self.assertEqual(fetchers.fetch_rss("https://slow.example/feed", "Slow"), [])
            self.assertEqual(fetchers.fetch_guardian("q"), [])
            self.assertEqual(fetchers.fetch_newsdata("q"), [])
        events = fetchers.SOURCE_EVENTS[-3:]
        self.assertTrue(all(not ok and "Timeout" in msg and count == 0 for _, ok, msg, count, _s in events), events)

    def test_http_error_from_a_feed_is_logged_with_status(self):
        resp = mock.Mock(status_code=503, text="Service Unavailable", content=b"")
        with mock.patch.object(fetchers.requests, "get", return_value=resp):
            fetchers.SOURCE_EVENTS.clear()
            self.assertEqual(fetchers.fetch_rss("https://x.example/feed", "X"), [])
        self.assertIn("HTTP 503", fetchers.SOURCE_EVENTS[-1][2])


class F_Gdelt(unittest.TestCase):
    BODY = "<html>" + "x" * 150 + "RATE-LIMITED-TAIL" + "y" * 100 + "</html>"

    def test_failure_logs_status_and_first_200_chars_and_makes_one_try(self):
        resp = mock.Mock(status_code=429, text=self.BODY)
        with mock.patch.object(fetchers.requests, "get", return_value=resp) as get, \
                mock.patch.object(fetchers, "_get_feed", side_effect=RuntimeError("rss down")), \
                mock.patch("time.sleep", side_effect=AssertionError("no retry sleep allowed")):
            fetchers.SOURCE_EVENTS.clear()
            articles, err, memory = fetchers.fetch_gdelt_articles("q", memory=None)
        self.assertEqual(articles, [])
        self.assertEqual(get.call_count, 1)                                      # one try, no retry loop
        self.assertEqual(get.call_args.kwargs["timeout"], fetchers.FETCH_TIMEOUT)
        self.assertIn("HTTP 429", err)
        self.assertIn(self.BODY[:200], err)
        self.assertNotIn(self.BODY[:201], err)                                   # only the first 200 chars
        name, ok, msg, count, seconds = fetchers.SOURCE_EVENTS[-1]
        self.assertEqual((name, ok, count), ("GDELT", False, 0))
        self.assertIn("HTTP 429", msg)

    def test_exception_does_not_raise(self):
        import requests
        with mock.patch.object(fetchers.requests, "get", side_effect=requests.exceptions.ConnectionError("boom")), \
                mock.patch.object(fetchers, "_get_feed", side_effect=RuntimeError("rss down")):
            articles, err, _ = fetchers.fetch_gdelt_articles("q")
        self.assertEqual(articles, [])
        self.assertIn("boom", err)

    def test_a_gdelt_failure_does_not_break_the_run(self):
        with workdir() as d:
            msg = "GDELT fetch failed: HTTP 429 — Too many requests"
            _, rc = run_pipeline(d, "full", "ok", gdelt_error=msg, seed={"memory.json": seeded_memory()})
            h = read_json(d, "health.json")
            self.assertEqual((rc, h["runs"][-1]["outcome"]), (0, "ok"))
            self.assertIn(msg, h["runs"][-1]["errors"])
            self.assertIn("HTTP 429", h["sources"]["GDELT"]["last_error"]["message"])
            self.assertIn("dry-run story", (Path(d) / "dist" / "index.html").read_text(encoding="utf-8"))


class G_ClaudeSwitch(unittest.TestCase):
    def test_claude_off_makes_zero_calls_and_logs_skipped_rows(self):
        with workdir() as d:
            _, rc = run_pipeline(d, "full", "error", claude_enabled="0", seed={"memory.json": seeded_memory()})
            rows = ledger(d)
            self.assertEqual(rc, 0)
            self.assertTrue(rows)
            self.assertTrue(all(r["outcome"] == "skipped" and r["cost_usd"] == 0 for r in rows), rows)   # no real call
            self.assertEqual({r["category"] for r in rows}, set(ALL_CATS))
            self.assertEqual({r["label"] for r in rows}, {f"{c}_selection" for c in ALL_CATS})
            self.assertTrue(all(r["candidates"] and r["candidates"] > 0 for r in rows))
            h = read_json(d, "health.json")
            run = h["runs"][-1]
            self.assertEqual((run["outcome"], run["reason"], run["claude_calls"]), ("ok", "claude_off", 0))
            self.assertEqual(h["api_cost_ledger"]["today"]["calls_skipped"], 4)
            self.assertEqual(h["api_cost_ledger"]["today"]["calls"], 0)
            self.assertIn("skipped", Path(d, "COST_SUMMARY.md").read_text(encoding="utf-8"))
            self.assertIsNone(h["sources"].get("claude"))                       # skipped rows are not Claude failures

    def test_claude_off_still_stores_articles_and_old_stories_stay(self):
        with workdir() as d:
            seed = {"memory.json": seeded_memory()}
            run_pipeline(d, "full", "ok", claude_enabled="0", seed=seed)
            m = read_json(d, "memory.json")
            self.assertEqual(m["stories"], seeded_memory()["stories"])          # stories untouched
            self.assertTrue(m["article_store"])
            self.assertEqual({r["category"] for r in m["article_store"].values()}, set(ALL_CATS))
            self.assertFalse((Path(d) / "dist").exists())                       # nothing changed: no rebuild
            self.assertEqual(read_json(d, "health.json")["last_successful_check"][:10], datetime.now(AEST).strftime("%Y-%m-%d"))

    def test_claude_off_other_run_types_are_also_free(self):
        for mode, cat in (("breaking_only", ""), ("category", "australia"), ("deploy_only", "")):
            with workdir() as d:
                run_pipeline(d, mode, "error", category=cat, claude_enabled="false", seed={"memory.json": seeded_memory()})
                self.assertFalse([r for r in ledger(d) if r["outcome"] != "skipped"], mode)

    def test_api_layer_is_a_safety_net(self):
        with workdir() as d, mock.patch.dict(os.environ, {"CLAUDE_ENABLED": "0"}), \
                mock.patch.object(api, "client", None):                    # any real call would raise AttributeError
            old = os.getcwd(); os.chdir(d)
            try:
                self.assertEqual(api.call_haiku("p", label="x_step"), "")
                self.assertEqual(api.call_sonnet("p", label="y_step"), "")
                self.assertEqual(api.call_sonnet_with_search("p", label="z_search"), "")
                self.assertEqual(api.call_haiku_with_search("p", label="w_search"), "")
                self.assertEqual([r["outcome"] for r in costs.load_rows()], ["skipped"] * 4)
            finally:
                os.chdir(old)

    def test_default_is_on(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("CLAUDE_ENABLED", None)
            self.assertTrue(api.claude_enabled())
        for v in ("0", "false", "OFF", "no"):
            with mock.patch.dict(os.environ, {"CLAUDE_ENABLED": v}):
                self.assertFalse(api.claude_enabled(), v)

    def test_costs_accepts_skipped_rows_and_the_rollup_counts_them_separately(self):
        with workdir() as d:
            old = os.getcwd(); os.chdir(d)
            try:
                costs.log_skipped("football_selection", "football", 40)
                costs.log_skipped("australia_selection", "australia", 12)
                rollup, ledger_rows = costs.build_rollup()
                t = rollup["today"]
                self.assertEqual((t["calls"], t["calls_skipped"], t["total_usd"]), (0, 2, 0))
                self.assertEqual(t["skipped_by_step"]["football_selection"]["calls"], 1)
                self.assertEqual(ledger_rows[0]["candidates"], 40)
                costs.write_summary_md(rollup, ledger_rows)
                self.assertIn("Calls skipped", Path("COST_SUMMARY.md").read_text(encoding="utf-8"))
            finally:
                os.chdir(old)

    def test_workflow_has_the_dispatch_input_and_env(self):
        wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "briefing.yml").read_text(encoding="utf-8")
        self.assertIn("claude_enabled:", wf)
        self.assertIn("CLAUDE_ENABLED: ${{ github.event.inputs.claude_enabled", wf)


class H_ArchaeologyFilter(unittest.TestCase):
    def test_only_new_articles_reach_claude_and_published_story_urls_stay_excluded(self):
        import processors
        seen_prompts = []
        arts = [dict(a("Old find"), url="https://x.com/old", is_new=False),
                dict(a("Published already"), url="https://x.com/pub", is_new=True)]
        memory = {"stories": {"2026-10-10": {"archaeology": [{"url": "https://x.com/pub", "articles": []}]}}}
        with mock.patch.object(processors, "call_sonnet", lambda *args, **kw: seen_prompts.append(args)):
            self.assertEqual(processors.process_archaeology(arts, memory), ([], memory))
        self.assertEqual(seen_prompts, [])                                       # nothing left: no Claude call
        arts.append(dict(a("Fresh find"), url="https://x.com/fresh", is_new=True))
        with mock.patch.object(processors, "call_sonnet", lambda *args, **kw: seen_prompts.append(args) or "[]"):
            processors.process_archaeology(arts, memory)
        self.assertIn("Fresh find", seen_prompts[0][0])
        self.assertNotIn("Old find", seen_prompts[0][0])


if __name__ == "__main__":
    unittest.main()
