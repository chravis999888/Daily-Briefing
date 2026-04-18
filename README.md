# Daily Briefing

A personal AI-powered news dashboard that fetches, filters and summarises 
news across 4 custom categories. Runs on a schedule, deploys automatically 
to Cloudflare Pages.

## What it does

Twice an hour it checks for breaking news. Every two hours it runs a full 
rebuild across all four categories — Breaking News, Australia, Archaeology, 
and Football. Claude selects the most significant stories, writes factual 
headlines, and generates 3-4 sentence summaries. The result is a clean, 
single-page dashboard designed to keep you fully informed in one visit.

Stories are remembered across runs. Yesterday's coverage stays visible 
below each category. Developing situations can be starred and tracked over 
time.

## Architecture

- **GitHub Actions** — scheduled pipeline, runs every 30 minutes 
  (breaking only) and every 2 hours (full rebuild)
- **Python** — fetches from RSS feeds, GDELT, Guardian API, NewsData, 
  YouTube and Reddit
- **Claude** — Sonnet for story selection and summarisation, Haiku for 
  lightweight tasks
- **Jinja2** — builds the static HTML page
- **Cloudflare Pages** — hosts the dashboard, deployed on content change

## Stack

- Python 3.11
- Anthropic Claude (Sonnet + Haiku)
- GitHub Actions
- Cloudflare Pages + Wrangler
- Jinja2

## Secrets required

Set these in GitHub → Settings → Secrets → Actions:

| Secret | Purpose |
|--------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GUARDIAN_API_KEY` | Guardian news feed |
| `NEWSDATA_API_KEY` | NewsData.io feed |
| `YOUTUBE_API_KEY` | YouTube trending |
| `CLOUDFLARE_API_TOKEN` | Cloudflare Pages deployment |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account |

## Run modes

Triggered via `workflow_dispatch` with a `mode` input:

| Mode | Description |
|------|-------------|
| `full` | Full rebuild across all categories |
| `breaking_only` | Breaking news check only |
| `category` | Rebuild a single category |
| `deploy_only` | Redeploy without fetching |
| `mock` | Local preview with hardcoded data, no API calls |

## Key files

| File | Purpose |
|------|---------|
| `fetch_news.py` | Entry point — orchestration and run mode switching |
| `memory.py` | Memory and health functions |
| `api.py` | Claude API wrappers and cost logging |
| `fetchers.py` | All data fetching — RSS, GDELT, Guardian, YouTube, Reddit, NewsData |
| `processors.py` | Category processors, HEADLINE_RULES |
| `page/builder.py` | HTML builder |
| `page/template.html` | Jinja2 page template |
| `memory.json` | Story cache, summaries, article hashes |
| `health.json` | Run status and errors |
| `cost_log.json` | API cost tracking per call |
