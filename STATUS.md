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
- v0.5 Bug fixes pass 4 — Breaking news modal showing wrong articles fixed: cached_sources was replacing articles_list instead of appending, causing the modal to show unrelated previously-covered stories instead of the actual source article. Now uses articles_list + cached_sources, matching Australia and Football processors.

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
- **World topics on every breaking-only run** — `process_world_topics` is called in every 30-minute breaking-only run, fetching Google News, YouTube, Google Trends and Reddit and making 3 Haiku calls. World topics only need updating in full runs. Unnecessary API and cost spend every half hour.
- **Double context search in Australia and Football** — when a story has both `context` and `deeper_search: true`, both processors run `find_related_cached_stories` + `call_haiku_with_search` twice with the same query. Results are appended to `articles_list` twice, producing duplicate article entries in the modal and doubling API calls for those stories.
- **`call_haiku` has no error handling** — `call_sonnet` has retries, rate limit backoff and a Haiku fallback. `call_haiku` is a bare call with no try/except. A 429 or 529 from Haiku will raise an unhandled exception and crash the entire run.
- **Sonnet silent Haiku fallback** — any Sonnet error other than `RateLimitError` (network error, 529 overloaded etc.) silently falls back to Haiku with no health log entry written. Summaries and story selection degrade to Haiku quality with no visible signal.
- **GDELT race condition** — the 2-hour gate relies on `last_gdelt_attempt` in committed `memory.json`. If two runs are dispatched close together (manual dispatch + scheduled cron), both can read the same memory before either commits, both see the gate as inactive and both hit GDELT simultaneously. Likely cause of some of the 429s in health.json.
- **Escape key doesn't close star popup** — the keydown handler only calls `closeModal()`. If the star popup is open, Escape does nothing. `closeStarPopup()` needs to be added to the handler.
- **Star popup can't be dismissed by clicking outside** — the story modal closes when clicking the overlay background. The star popup overlay has no `onclick` handler so click-outside-to-dismiss doesn't work.
- **Haiku used for complex reasoning** — world topics clustering and developing situations processing use Haiku despite having complex REJECT/ONLY instructions. Haiku frequently doesn't follow these reliably, likely why world topics sometimes returns generic categories despite explicit instructions.
- **Dead `errors` key in health.json** — the top-level `"errors": []` key is written in the default health dict and never populated by `log_run`. Whatever it was intended for is unimplemented.
- **Summary cache eviction is insertion-order not LRU** — when the summary cache exceeds 500 entries it deletes the oldest-inserted keys regardless of recent use. Recurring stories lose cached summaries faster than new ones.
- **`call_sonnet_with_search` silent empty return** — if Sonnet uses web search but the response contains only `tool_use` blocks with no final `text` block, the function returns empty string silently. Callers treat this the same as a legitimate empty result.
- **Breaking news prompt over-filtering significant stories** *(observed 17 April 2026)* — Trump threatening to leave NATO did not surface; the prompt is rejecting high-profile political statements as "ongoing situation" coverage rather than discrete events. Fix: explicitly allow significant statements and announcements from heads of state as breaking events.
- **Australia prompt scoped too narrowly** *(observed 17 April 2026)* — Oil refinery fire did not surface; the prompt is limited to parliamentary/legal events only. Fix: expand to include major incidents, disasters, and significant infrastructure events affecting Australians. Both prompts were tightened aggressively during v0.5 to fix quality issues and have overcorrected into false negatives on genuinely important stories.
- **AI refusal leaking into summary field** *(observed in memory.json 16 April 2026)* — `get_ai_summary()` for the story "US Navy torpedo attack strands 200+ Iranian sailors" returned a Claude refusal message instead of a factual summary (likely triggered by "torpedo attack" + US-Iran context), which was persisted to memory.json and would display verbatim in the modal. Fix: add a validation check after `get_ai_summary()` — if the returned text contains phrases like `"I can't verify"`, `"I'm unable to"`, or `"no credible news sources"`, discard it and fall back to a plain one-sentence summary derived from the headline alone.

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
