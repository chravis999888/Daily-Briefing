# Daily Briefing — Project Status

## Last Shipped
Monthly spend cap in code (A$15 default, 80%/100% alerts, Breaking-only at cap, hard stop at 120%), per-category Claude switches, cap state in COST_SUMMARY.md, SHA + cap % footer stamp (PR open for review, not merged; #80-#83)

## 🔄 In Progress
Nothing in progress. Open follow-ups: #68 (crash handling), #70 (PR #62 review leftovers), #78 (GDELT rate-limit investigation, parked).

## 📌 Critical Context
- Steps 1 and 2 (PR #62, PR #79) are merged to main
- Spend controls (repo variables): `MONTHLY_CAP_AUD` (default 15), `CLAUDE_ENABLED`, `CLAUDE_BREAKING/AUSTRALIA/ARCHAEOLOGY/FOOTBALL/TRACKED` (on unless `false`). Global off beats category flags
- Owner is in Brisbane, Australia — all timestamps in AEST (UTC+10)
- Live site: https://daily-briefing-2wm.pages.dev (Cloudflare Pages project daily-briefing; the plain daily-briefing.pages.dev is NOT ours)
- AUD conversion hardcoded at 1.55
- Claude Code handles all file edits — paste briefs directly into Claude Code chat
- Editorial philosophy: strict quality bars, factual headlines, no clickbait — see `HEADLINE_RULES` constant in `processors.py`
- GitHub Issues is the single source of truth for all bugs and features
- Full bug and feature backlog lives in GitHub Issues — not here

## 📁 Key Files
| File | Purpose |
|------|---------|
| `fetch_news.py` | Entry point — orchestration and run mode switching only |
| `memory.py` | All memory/health functions — load, save, cache, `log_run` |
| `articles.py` | Article IDs (sha256 of canonical URL), `article_store` in memory.json (7-day prune), new-vs-seen split, near-duplicate titles (`DUPLICATE_THRESHOLD`) |
| `api.py` | Claude API wrappers (`_attempt`), the one gate in front of every call (`claude_blocked`: global switch, category switch, spend cap), relative_time, format_articles_for_prompt |
| `fetchers.py` | All data fetching — RSS, GDELT, Guardian, YouTube, Reddit, NewsData; `timed_fetch` is the shared routine (10s timeout, ids, per-source timing) |
| `processors.py` | Category processors, world topics, developing situations, HEADLINE_RULES |
| `page/builder.py` | build_html() — loads and renders Jinja2 template, ACCENTS |
| `page/template.html` | Full HTML/CSS/JS page with Jinja2 syntax, unified render_story macro |
| `.github/workflows/briefing.yml` | Scheduling and deployment |
| `memory.json` | Story cache, summaries, world trends, `article_store` (id -> source/title/published/first_seen/category) |
| `health.json` | Single status file: runs, per-source status, errors, last_successful_data_update, last_successful_check (healthy all-quiet full run), consecutive_failures, per-source `last_run`, per-run per-category new-article stats, api_cost_ledger |
| `pricing.py` | The only price table (USD/MTok + web search fee), with source URL and date |
| `costs.py` | Cost ledger: `log_call`, `log_skipped(reason)`, rollups, COST_SUMMARY.md, month-to-date + `cap_state`, cap alerts, health key `api_cost_ledger` |
| `cost_log.json` | Append-only ledger, one row per API attempt (plus `skipped` rows when `CLAUDE_ENABLED` is off) (rows without `outcome` are pre-#52 and mispriced) |
| `tests/test_costs.py` | Cost maths + ledger tests: `python -m unittest tests.test_costs` |
| `safety.py` | Good-data rule (`evaluate_run`), `STALE_AFTER_HOURS = 8`, alert rule (`decide_alert`) |
| `tests/harness.py` | Offline dry-run harness (fake Claude + fetchers); tests: `python -m unittest discover -s tests -t .` |
| `requirements.txt` | anthropic, requests, feedparser, beautifulsoup4, jinja2 |
