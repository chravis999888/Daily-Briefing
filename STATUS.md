# Daily Briefing — Project Status

## ✅ Shipped

- UI overhaul — modal system, 3-column layout, breaking news full-width at top
- Logo and favicon — pulse dot mark, Playfair Display wordmark
- World trends section — Google News RSS + YouTube + Google Trends RSS + Reddit fallback, Today/Week/Month tabs built from memory
- Developing situations tracker — star/pin system, auto-detection, remove button
- Cost optimisations — article hashing, caching, deploy-only mode, per-story sleeps, memory-based summary reuse
- GDELT hardening — separated JSON/network error handling, 30s timeout, HTTP status logging, one retry + RSS fallback, error bubbled into health.json, 2h rate-limit gate in memory, URL-encoded RSS fallback query
- Breaking news RSS backbone — Reuters, AP News, BBC News, Al Jazeera added to both breaking_only and full runs alongside Guardian + GDELT
- Mock mode — hardcoded data for local preview without burning API credits
- Cloudflare Pages deployment — wrangler deploy via GitHub Actions, sentinel-file gating, confirmed working end-to-end
- Rate limit fixes — sleep spacing across all four processors
- "Previously" cards — yesterday's stories shown below each category

---

## 🔄 In Progress

Nothing currently in progress. Next full run will repopulate memory.json with fixed story structure (summary + url + image + articles now persisted).

---

## 📋 Next Up

### v0.5 — Bug fixes
- Previously cards — clicking them should open modal with summary snippet
- Star popup stale pinned.txt bug — sometimes shows outdated topics
- Auto-refresh 404 handling — graceful fallback if page not found on poll
- Tracking suggestions — move into Sonnet pass instead of separate Haiku calls
- GDELT failure alert — email or webhook after 3 consecutive failures
- Deploy flag — only deploy to Cloudflare when content actually changed
- Breaking news persistence — don't overwrite with empty on failed runs
- GDELT root cause diagnosis — exact error now in health.json; gate skips are logged as info not error
- Archaeology duplicate detection — same story different headline
- Archaeology recency filter on RSS feeds
- Fabrizio Romano — confirm working in production
- Foreign language RSS — drop or replace with English equivalents

### v0.6 — Infra Reset
- Jinja2 templating — extract 1500-line HTML f-string into template.html
- Cloudflare Workers + KV — replace GitHub API browser hacks for starring/deleting/refreshing
- render_story() consolidation — one function not three diverged versions
- Memory/KV migration plan — decide what stays in GitHub vs moves to KV

### v0.7 — Features
- Cost/stats dashboard — separate cost_log.json, token accumulator, Chart.js visualisation, AUD conversion at 1.55
- Sleep mode — restrict cron to waking hours (user to confirm hours, Brisbane AEST)
- Instant delete on developing situations — no page reload needed
- Loading state for category refresh buttons
- Breaking news graceful degradation — handle 1, 2, or 3 stories without breaking grid
- Settings/stats modal — ⚙ icon in header

### v0.8 — Polish
- Mobile responsiveness — full pass
- Typography — tighten type scale, Playfair Display on all headlines
- Modal improvements — tracking pills, better image handling
- JS robustness — var globals, error boundaries, safe JSON parsing

### v0.9 — Personalisation
- Favourite team/league preferences
- Thumbs up/down feedback loop
- Per-user prompts
- Category creation UI

### v1.0 — Product
- Multi-user — GitHub OAuth, shared processing pipeline
- Brother's instance
- Landing page and onboarding flow

---

## 🐛 Known Issues

- **GDELT consistently failing** — retries + RSS fallback in place; 2h gate prevents hammering; breaking news now has Reuters/AP/BBC/Al Jazeera as backbone regardless of GDELT status; memory corruption guard added (isinstance check + reload from disk if corrupted)
- **Summaries missing from HTML** — fixed: save_today_stories now persists summary/url/image/articles/tracking_suggestions; all four processors now include url in results dict. Next full run will repopulate cache with correct structure.
- **429 rate limit on tracking suggestions** — fixed: generate_tracking_suggestions now catches anthropic.RateLimitError and returns [] instead of crashing; explicit time.sleep(2) added before each call in all four processor loops (after existing time.sleep(3)); internal sleep removed from function body
- **Auto-developing situations not triggering** — needs ~1 week of consistent memory history to build up enough signal
- **529 overloaded errors** — transient Anthropic API issue, retry after 10-15 mins

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `fetch_news.py` | Everything — fetchers, processors, HTML builder (~2200 lines) |
| `.github/workflows/briefing.yml` | Scheduling and deployment |
| `memory.json` | Story cache, summaries, world trends, article hashes |
| `health.json` | Run status and errors |
| `requirements.txt` | anthropic, requests, feedparser, beautifulsoup4 |
| `STATUS.md` | This file — update whenever a feature ships |

---

## 📌 Context

- Owner is in Brisbane, Australia — all timestamps in AEST (UTC+10)
- AUD conversion hardcoded at 1.55
- Claude Code handles all file edits — paste briefs directly into Claude Code chat
- Editorial philosophy: strict quality bars, factual headlines, no clickbait — see `HEADLINE_RULES` constant in `fetch_news.py`
- **Always add to Claude Code briefs:** "When done, update STATUS.md — move completed items to ✅ Shipped, update 🔄 In Progress and 🐛 Known Issues accordingly."
