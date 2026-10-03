# Daily Briefing — Project Status

## Last Shipped
Merged PR #42 to main: security audit fixes (#43–#46, #51) plus the exact per-call cost ledger (#52); first scheduled run on the new code is next

## 🔄 In Progress
Nothing currently in progress.

## 📌 Critical Context
- Owner is in Brisbane, Australia — all timestamps in AEST (UTC+10)
- AUD conversion hardcoded at 1.55
- Claude Code handles all file edits — paste briefs directly into Claude Code chat
- Editorial philosophy: strict quality bars, factual headlines, no clickbait — see `HEADLINE_RULES` constant in `processors.py`
- GitHub Issues is the single source of truth for all bugs and features
- Full bug and feature backlog lives in GitHub Issues — not here

## 📁 Key Files
| File | Purpose |
|------|---------|
| `fetch_news.py` | Entry point — orchestration and run mode switching only |
| `memory.py` | All memory/health functions — load, save, cache, hashing |
| `api.py` | Claude API wrappers (`_attempt`), relative_time, format_articles_for_prompt |
| `fetchers.py` | All data fetching — RSS, GDELT, Guardian, YouTube, Reddit, NewsData |
| `processors.py` | Category processors, world topics, developing situations, HEADLINE_RULES |
| `page/builder.py` | build_html() — loads and renders Jinja2 template, ACCENTS |
| `page/template.html` | Full HTML/CSS/JS page with Jinja2 syntax, unified render_story macro |
| `.github/workflows/briefing.yml` | Scheduling and deployment |
| `memory.json` | Story cache, summaries, world trends, article hashes |
| `health.json` | Run status and errors |
| `pricing.py` | The only price table (USD/MTok + web search fee), with source URL and date |
| `costs.py` | Cost ledger: `log_call`, rollups, COST_SUMMARY.md, health key `api_cost_ledger` |
| `cost_log.json` | Append-only ledger, one row per API attempt (rows without `outcome` are pre-#52 and mispriced) |
| `tests/test_costs.py` | Cost maths + ledger tests: `python -m unittest tests.test_costs` |
| `requirements.txt` | anthropic, requests, feedparser, beautifulsoup4, jinja2 |
