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
- Summaries missing from HTML — save_today_stories now persists all fields; url added to all four results dicts
- 429 rate limit crash on tracking suggestions — RateLimitError caught, returns []; sleep(2) added before each call
- GDELT root cause diagnosis — exact error captured in health.json; gate skips logged as info not error
- v0.5 Bug fixes pass 1 — GDELT RSS URL fix (urllib.parse.urlencode), star popup cache busting (?t=Date.now + Cache-Control), Previously cards clickable (full story passed to render_story; breaking PREVIOUSLY cards get onclick+cursor:pointer), breaking news persistence in full run (fallback to cache on empty), deploy flag only on content change (all run modes), health dot custom tooltip (CSS hover, fade-in), Fabrizio Romano Telegram scraper removed
- v0.5 Bug fixes pass 2 — Tracking suggestions merged into Sonnet summary pass (single API call, no separate Haiku), generate_tracking_suggestions() deleted, archaeology seen-URL filter (cross-references all memory URLs before passing to Claude)
- v0.5 Bug fixes pass 3 — get_articles_hash switched from hash() to hashlib.md5 (fixes PYTHONHASHSEED randomization that was making category_has_changed always return True), process_australia category-mode crash fixed (was passing 2 args to 3-arg function), removeSituation visual removal fixed (Python sit_id now uses urllib.parse.quote, JS uses encodeURIComponent — both produce the same ID)

---

## 🔄 In Progress

Nothing currently in progress.

---

## 📋 Next Up

### v0.5 — Bug fixes
- Auto-refresh 404 handling — graceful fallback if page not found on poll
- GDELT failure alert — email or webhook after 3 consecutive failures
- Archaeology duplicate detection — same story different headline
- Archaeology recency filter on RSS feeds
- Foreign language RSS — drop or replace with English equivalents
- Fabrizio Romano — gold standard transfer source, needs a reliable feed. Telegram scraper removed as too fragile. Find a stable solution.

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

- **GDELT rate-limited on shared GitHub Actions IPs** — 2h gate + Reuters/AP/BBC/Al Jazeera RSS backbone in place as fallback
- **Auto-developing situations not triggering** — needs ~1 week of consistent memory history to build up enough signal
- **529 overloaded errors** — transient Anthropic API issue, retry after 10-15 mins
- **Football sourcing bias** — context articles sometimes skewed by player nationality rather than footballing relevance. Example: Enez Abde/Real Betis story pulled Marca, Mundo Betis and Africa Soccer rather than major European outlets because he is Moroccan. Needs prompt-level fix in context article search.

---

## 🐛 Known Bugs

### Australia category — stale story + only 1 Previously card
*Observed in production 15 April 2026*

**Root cause confirmed and partially fixed (Pass 3):** The `get_articles_hash` function was using Python's built-in `hash()`, which is randomized per-process via PYTHONHASHSEED. This meant `category_has_changed` always returned True (hashes never matched across GitHub Actions runs), so every run re-processed all articles as if they were new — defeating dedup entirely. Fixed in Pass 3 by switching to `hashlib.md5`.

**Remaining risk:** A story with a slightly different URL or headline each run can still defeat story-level dedup (the seen-URL filter added in Pass 2 covers archaeology; Australia and Football do not have the same filter yet). Monitor in production — if the stale story issue persists after the hash fix, the seen-URL filter should be extended to Australia and Football.

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
