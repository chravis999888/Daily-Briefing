# Daily Briefing — Project Status

## Last Shipped
v0.5 Bug fixes pass 5 — Breaking news heads-of-state carve-out, Australia prompt philosophy rewrite, AI refusal retry fallback in get_ai_summary()

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
| `api.py` | Claude API wrappers, log_api_call, relative_time, format_articles_for_prompt |
| `fetchers.py` | All data fetching — RSS, GDELT, Guardian, YouTube, Reddit, NewsData |
| `processors.py` | Category processors, world topics, developing situations, HEADLINE_RULES |
| `page/builder.py` | build_html() — loads and renders Jinja2 template, ACCENTS |
| `page/template.html` | Full HTML/CSS/JS page with Jinja2 syntax, unified render_story macro |
| `.github/workflows/briefing.yml` | Scheduling and deployment |
| `memory.json` | Story cache, summaries, world trends, article hashes |
| `health.json` | Run status and errors |
| `cost_log.json` | API call costs — timestamp, model, tokens, USD per call |
| `requirements.txt` | anthropic, requests, feedparser, beautifulsoup4, jinja2 |
