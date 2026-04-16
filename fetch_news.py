import os
import re
import json
import time
import hashlib
import requests
import feedparser
import anthropic
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime
MOCK_MODE = False
RUN_MODE = os.environ.get("RUN_MODE", "full")
RUN_CATEGORY = os.environ.get("RUN_CATEGORY", "")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWSDATA_KEY = os.environ.get("NEWSDATA_API_KEY", "")
GUARDIAN_KEY = os.environ.get("GUARDIAN_API_KEY", "")

if not MOCK_MODE:
    if not ANTHROPIC_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY not set")
    if not NEWSDATA_KEY:
        raise EnvironmentError("NEWSDATA_API_KEY not set")
    if not GUARDIAN_KEY:
        raise EnvironmentError("GUARDIAN_API_KEY not set")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if not MOCK_MODE else None

AEST = timezone(timedelta(hours=10))
MEMORY_FILE = "memory.json"
PINNED_FILE = "pinned.txt"
HEALTH_FILE = "health.json"

def load_health():
    try:
        if Path(HEALTH_FILE).exists():
            with open(HEALTH_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"runs": [], "errors": []}

def save_health(health):
    try:
        with open(HEALTH_FILE, "w") as f:
            json.dump(health, f, indent=2)
    except Exception as e:
        print(f"Health save error: {e}")

def log_run(health, run_type, errors):
    now = datetime.now(AEST).isoformat()
    health["runs"].append({
        "timestamp": now,
        "run_type": run_type,
        "errors": errors,
        "status": "degraded" if errors else "ok"
    })
    health["runs"] = health["runs"][-50:]
    return health

def log_api_call(label: str, model: str, input_tokens: int, output_tokens: int):
    """Append one API call record to cost_log.json. Keeps last 1000 entries."""
    HAIKU_IN   = 0.80  / 1_000_000
    HAIKU_OUT  = 4.00  / 1_000_000
    SONNET_IN  = 3.00  / 1_000_000
    SONNET_OUT = 15.00 / 1_000_000

    if "haiku" in model.lower():
        cost_usd = (input_tokens * HAIKU_IN) + (output_tokens * HAIKU_OUT)
    else:
        cost_usd = (input_tokens * SONNET_IN) + (output_tokens * SONNET_OUT)

    record = {
        "timestamp": datetime.now(AEST).isoformat(),
        "run_type": RUN_MODE,
        "label": label,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6)
    }

    log_path = Path("cost_log.json")
    try:
        existing = json.loads(log_path.read_text()) if log_path.exists() else []
    except Exception:
        existing = []

    existing.append(record)
    if len(existing) > 1000:
        existing = existing[-1000:]

    log_path.write_text(json.dumps(existing, indent=2))

ACCENTS = {
    "breaking": "#c0392b",
    "australia": "#2e7bbf",
    "archaeology": "#b07d2a",
    "football": "#2a7a52",
    "world": "#7b68c8",
    "developing": "#2a7a6e"
}

HEADLINE_RULES = """
CRITICAL HEADLINE RULES:
- Write a single sentence stating the actual specific fact. Reader must be fully informed without clicking.
- Include real names, real numbers, real outcomes.
- NEVER use: "faces", "races against time", "sparks debate", "raises concerns", "under pressure", "amid tensions", "could impact", "warns of", "signals", "eyes", "targets", "mulls", "critical moment", "decisive", "implications"
- BAD: "Manchester City and Arsenal face critical final month with title implications"
- GOOD: "Manchester City lead Arsenal by 2 points with 5 games remaining as Premier League title race enters final stretch"
The headline must be a factual summary with specific details, not a news teaser.
"""

# ── Memory ────────────────────────────────────────────────────────────────────

def load_memory():
    try:
        if Path(MEMORY_FILE).exists():
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"stories": {}, "developing": {}}

def save_memory(memory):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=2)
    except Exception as e:
        print(f"Memory save error: {e}")

def load_pinned():
    try:
        if Path(PINNED_FILE).exists():
            with open(PINNED_FILE, "r") as f:
                return [line.strip() for line in f.readlines() if line.strip()]
    except:
        pass
    return []

def get_previous_stories(memory, category, limit=3):
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    stories_by_date = memory.get("stories", {})
    for date in sorted(stories_by_date.keys(), reverse=True):
        if date < today:
            stories = stories_by_date[date].get(category, [])
            if stories:
                return stories[:limit]
    return []

def save_today_stories(memory, category, stories):
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    if "stories" not in memory:
        memory["stories"] = {}
    if today not in memory["stories"]:
        memory["stories"][today] = {}
    memory["stories"][today][category] = [
        {"headline": s["headline"], "timestamp": s.get("timestamp",""), "score": s.get("score", 5),
         "summary": s.get("summary",""), "url": s.get("url",""), "image": s.get("image",""),
         "articles": s.get("articles",[]), "tracking_suggestions": s.get("tracking_suggestions",[])}
        for s in stories
    ]
    cutoff = (datetime.now(AEST) - timedelta(days=3)).strftime("%Y-%m-%d")
    memory["stories"] = {k: v for k, v in memory["stories"].items() if k >= cutoff}
    return memory

def get_articles_hash(articles):
    """Hash the titles of a list of articles to detect changes.
    Uses MD5 for deterministic output across processes and GitHub Actions runners.
    (Python's built-in hash() is PYTHONHASHSEED-randomized per process.)
    """
    titles = sorted([a.get("title","") for a in articles])
    combined = "".join(titles)
    return hashlib.md5(combined.encode()).hexdigest()

def category_has_changed(memory, category, articles):
    """Returns True if articles are different from last run."""
    current_hash = get_articles_hash(articles)
    last_hash = memory.get("article_hashes", {}).get(category)
    return current_hash != last_hash

def save_article_hash(memory, category, articles):
    """Save current article hash to memory."""
    if "article_hashes" not in memory:
        memory["article_hashes"] = {}
    memory["article_hashes"][category] = get_articles_hash(articles)
    return memory

def get_cached_category(memory, category):
    """Get the most recently saved stories for a category."""
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    stories = memory.get("stories", {}).get(today, {}).get(category)
    if stories:
        return stories
    # Fall back to previous
    return get_previous_stories(memory, category)

def get_cached_summary(memory, url):
    return memory.get("summaries", {}).get(url)

def save_summary(memory, url, summary):
    if "summaries" not in memory:
        memory["summaries"] = {}
    memory["summaries"][url] = summary
    # Trim to last 500 entries to avoid bloat
    if len(memory["summaries"]) > 500:
        keys = list(memory["summaries"].keys())
        for k in keys[:-500]:
            del memory["summaries"][k]
    return memory

def find_related_cached_stories(memory, topic, days=7):
    """Check memory for stories related to this topic using keyword matching.
    No API call — uses word overlap heuristic instead of Haiku to save cost.
    """
    cutoff = (datetime.now(AEST) - timedelta(days=days)).strftime("%Y-%m-%d")
    STOPWORDS = {"that", "this", "with", "from", "have", "been", "will", "they",
                 "their", "what", "which", "were", "when", "than", "then", "also",
                 "into", "over", "after", "about", "more", "some", "such", "says"}
    topic_words = {w.lower().strip(".,;:\"'()") for w in topic.split()
                   if len(w) > 3 and w.lower() not in STOPWORDS}
    if not topic_words:
        return None

    sources = []
    for date, cats in memory.get("stories", {}).items():
        if date < cutoff:
            continue
        for cat, stories in cats.items():
            for s in stories:
                headline = s.get("headline", "")
                hl_words = {w.lower().strip(".,;:\"'()") for w in headline.split()}
                if len(topic_words & hl_words) >= 2:
                    url = next((a.get("url","") for a in s.get("articles",[]) if a.get("url","")), "")
                    if url:
                        sources.append({"title": headline, "source": "Previously covered", "url": url})
    return sources[:4] if sources else None

def save_trend_topics(memory, topics):
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    if "world_trends" not in memory:
        memory["world_trends"] = {}
    memory["world_trends"][today] = topics
    # Keep only last 30 days
    cutoff = (datetime.now(AEST) - timedelta(days=30)).strftime("%Y-%m-%d")
    memory["world_trends"] = {k: v for k, v in memory["world_trends"].items() if k >= cutoff}
    return memory

def detect_developing_situations(memory, all_data):
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    story_history = memory.get("stories", {})
    headline_counts = {}
    for date, cats in story_history.items():
        if date == today:
            continue
        for cat, stories in cats.items():
            for s in stories:
                key = s["headline"][:60].lower()
                headline_counts[key] = headline_counts.get(key, 0) + 1

    today_headlines = []
    for cat, stories in all_data.items():
        for s in stories:
            today_headlines.append(s["headline"][:60].lower())

    auto_detected = []
    for h in today_headlines:
        if headline_counts.get(h, 0) >= 2:
            auto_detected.append(h)

    return auto_detected

# ── Time ──────────────────────────────────────────────────────────────────────

def relative_time(date_str):
    if not date_str:
        return ""
    try:
        dt = None
        for parser in [
            lambda s: datetime.fromisoformat(s),
            lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
            lambda s: parsedate_to_datetime(s),
            lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z"),
            lambda s: datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %z"),
            lambda s: datetime.strptime(s, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s[:25], "%Y-%m-%dT%H:%M:%S+00:00").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc),
        ]:
            try:
                dt = parser(date_str)
                break
            except:
                continue
        if not dt:
            return ""
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = (now - dt).total_seconds()
        if seconds < 3600:
            mins = int(seconds // 60)
            return "just now" if mins < 2 else f"{mins} mins ago"
        elif seconds < 86400:
            hours = int(seconds // 3600)
            return f"{hours} hr{'s' if hours > 1 else ''} ago"
        elif seconds < 172800:
            return "yesterday"
        elif seconds < 604800:
            days = int(seconds // 86400)
            return f"{days} days ago"
        else:
            weeks = int(seconds // 604800)
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    except:
        return ""

# ── Claude ────────────────────────────────────────────────────────────────────

def call_haiku(prompt, max_tokens=500, label="haiku"):
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
    return msg.content[0].text

def call_sonnet(prompt, max_tokens=1000, retries=3, label="sonnet"):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
            return msg.content[0].text
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"Sonnet rate limit, waiting {wait}s (attempt {attempt+1}/{retries})...")
            time.sleep(wait)
        except Exception as e:
            print(f"Sonnet error: {e}")
            break
    print("Falling back to Haiku...")
    return call_haiku(prompt, max_tokens, label="sonnet_haiku_fallback")

def call_sonnet_with_search(prompt, max_tokens=1500, retries=3, label="context_search_sonnet"):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": prompt}]
            )
            log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
            for block in msg.content:
                if block.type == "text":
                    return block.text
            return ""
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"Sonnet search rate limit, waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"Sonnet search error: {e}")
            break
    return ""

def call_haiku_with_search(prompt, max_tokens=500, label="context_search"):
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
        for block in msg.content:
            if block.type == "text":
                return block.text
        return ""
    except Exception as e:
        print(f"Haiku search error: {e}")
        return ""

def get_ai_summary(headline, content="", context=""):
    prompt = f"""In 3-4 sentences, explain this news story clearly and factually.
Headline: "{headline}"
{f'Article content: {content[:1200]}' if content else ''}
{f'Additional context: {context}' if context else ''}
Cover what happened, why it matters, and any important background or broader significance. Plain English, no fluff.

Also suggest 3-4 short trackable topic labels for this story, from specific to broad (2-5 words each).

Return a JSON object:
{{"summary": "3-4 sentence explanation...", "tracking_suggestions": ["specific topic", "broader topic", "wider context"]}}
Raw JSON only, no markdown."""
    text = call_sonnet(prompt, 400, label="story_summary")
    try:
        data = json.loads(text.replace("```json","").replace("```","").strip())
        summary = re.sub(r'^#+\s*\w*\s*', '', str(data.get("summary", ""))).strip()
        suggestions = data.get("tracking_suggestions", [])
        if not isinstance(suggestions, list):
            suggestions = []
        return summary, suggestions
    except Exception:
        # Fallback: treat entire response as summary, no suggestions
        summary = re.sub(r'^#+\s*\w*\s*', '', text).strip()
        return summary, []

# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_gdelt_articles(query, timespan="1h", max_records=25, memory=None):
    # 2-hour rate-limit gate
    if memory is not None:
        last = memory.get("last_gdelt_attempt")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if age < 2 * 3600:
                    msg = f"GDELT skipped — rate limit gate (last attempt {int(age/60)}m ago)"
                    print(msg)
                    return [], msg, memory
            except Exception:
                pass
        memory["last_gdelt_attempt"] = datetime.now(timezone.utc).isoformat()

    DOMAIN_BLACKLIST = ["wikipedia.org", "wikipedia.com", "britannica.com", "fandom.com", "wikimedia.org"]
    base_url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {"query": query, "mode": "artlist", "maxrecords": max_records, "timespan": timespan}

    def _parse_articles(raw_list):
        now_utc = datetime.now(timezone.utc)
        articles = []
        for a in raw_list:
            domain = a.get("domain", "")
            if any(bl in domain for bl in DOMAIN_BLACKLIST):
                continue
            seendate = a.get("seendate", "")
            if seendate:
                try:
                    dt = datetime.strptime(seendate[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                    if (now_utc - dt).total_seconds() > 6 * 3600:
                        continue
                except Exception:
                    pass
            articles.append({"title": a.get("title", ""), "url": a.get("url", ""),
                             "source": domain, "time": seendate, "content": ""})
        return articles

    def _parse_rss_articles(feed):
        now_utc = datetime.now(timezone.utc)
        articles = []
        for e in feed.entries:
            url = e.get("link", "")
            domain = url.split("/")[2] if url.startswith("http") else ""
            if any(bl in domain for bl in DOMAIN_BLACKLIST):
                continue
            pub = e.get("published", "")
            if pub:
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (now_utc - dt).total_seconds() > 6 * 3600:
                        continue
                except Exception:
                    pass
            articles.append({"title": e.get("title", ""), "url": url,
                             "source": domain, "time": pub, "content": ""})
        return articles

    def _attempt_json():
        r = requests.get(base_url, params={**params, "format": "json"}, timeout=30)
        if r.status_code != 200:
            return [], f"GDELT fetch failed: HTTP {r.status_code}"
        try:
            data = r.json()
        except Exception as e:
            return [], f"GDELT fetch failed: JSON decode error — {e}"
        articles = _parse_articles(data.get("articles", []))
        return articles, ""

    def _attempt_rss():
        rss_params = {**params, "format": "rss"}
        rss_url = base_url + "?" + urllib.parse.urlencode(rss_params)
        feed = feedparser.parse(rss_url)
        articles = _parse_rss_articles(feed)
        return articles, "" if articles else "GDELT RSS returned empty"

    # First JSON attempt
    err = "GDELT fetch failed: unknown"
    try:
        articles, err = _attempt_json()
        if articles:
            return articles, "", memory
        print(f"GDELT JSON attempt 1: {err or 'empty'} — retrying in 5s")
    except Exception as e:
        err = f"GDELT fetch failed: {e}"
        print(f"GDELT JSON attempt 1 exception: {e} — retrying in 5s")

    time.sleep(5)

    # Second JSON attempt
    try:
        articles, err = _attempt_json()
        if articles:
            return articles, "", memory
        print(f"GDELT JSON attempt 2: {err or 'empty'} — falling back to RSS")
    except Exception as e:
        err = f"GDELT fetch failed: {e}"
        print(f"GDELT JSON attempt 2 exception: {e} — falling back to RSS")

    # RSS fallback
    try:
        articles, rss_err = _attempt_rss()
        if articles:
            print(f"GDELT RSS fallback succeeded: {len(articles)} articles")
            return articles, "", memory
        final_err = err + "; RSS fallback also empty"
        print(f"GDELT RSS fallback: {rss_err}")
        return [], final_err, memory
    except Exception as e:
        final_err = err + f"; RSS fallback failed: {e}"
        print(f"GDELT RSS fallback exception: {e}")
        return [], final_err, memory

def fetch_google_news_rss():
    try:
        feed = feedparser.parse("https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en")
        topics = []
        for entry in feed.entries[:20]:
            topics.append(entry.get("title", ""))
        return [t for t in topics if t]
    except Exception as e:
        print(f"Google News RSS fetch error: {e}")
        return []

def fetch_youtube_trending():
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return []
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet",
        "chart": "mostPopular",
        "regionCode": "US",
        "maxResults": 20,
        "key": api_key
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        return [item["snippet"]["title"] for item in data.get("items", [])]
    except Exception as e:
        print(f"YouTube trending fetch error: {e}")
        return []

def fetch_google_trends_rss():
    try:
        feed = feedparser.parse("https://trends.google.com/trending/rss?geo=US")
        topics = []
        for entry in feed.entries[:20]:
            topics.append(entry.get("title", ""))
        return [t for t in topics if t]
    except Exception as e:
        print(f"Google Trends RSS fetch error: {e}")
        return []

def fetch_reddit_json():
    url = "https://www.reddit.com/r/worldnews+news/top.json?limit=25&t=day"
    headers = {"User-Agent": "DailyBriefing/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        posts = r.json()["data"]["children"]
        return [p["data"]["title"] for p in posts if not p["data"].get("stickied")]
    except Exception as e:
        print(f"Reddit JSON fetch error: {e}")
        return []

def fetch_guardian(query, page_size=15, section=None):
    url = "https://content.guardianapis.com/search"
    params = {
        "q": query,
        "api-key": GUARDIAN_KEY,
        "page-size": page_size,
        "order-by": "newest",
        "show-fields": "headline,trailText,bodyText,thumbnail"
    }
    if section:
        params["section"] = section
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        results = data.get("response", {}).get("results", [])
        articles = []
        for a in results:
            fields = a.get("fields", {})
            body = fields.get("bodyText", "") or fields.get("trailText", "")
            articles.append({
                "title": a.get("webTitle", ""),
                "url": a.get("webUrl", ""),
                "source": "The Guardian",
                "time": a.get("webPublicationDate", ""),
                "content": body[:2000],
                "image": fields.get("thumbnail", "")
            })
        return articles
    except Exception as e:
        print(f"Guardian fetch error: {e}")
        return []

def fetch_rss(url, source_name):
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:20]:
            summary = entry.get("summary", "") or entry.get("description", "")
            image = ""
            if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                image = entry.media_thumbnail[0].get("url","")
            articles.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": source_name,
                "time": entry.get("published", ""),
                "content": re.sub(r'<[^>]+>', '', summary)[:1000],
                "image": image
            })
        return articles
    except Exception as e:
        print(f"RSS fetch error {url}: {e}")
        return []

def fetch_newsdata(query, country=None):
    url = "https://newsdata.io/api/1/news"
    params = {"apikey": NEWSDATA_KEY, "q": query, "language": "en", "full_content": 1}
    if country:
        params["country"] = country
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        results = data.get("results", [])
        if not isinstance(results, list):
            return []
        articles = []
        for a in results:
            if not isinstance(a, dict):
                continue
            content = a.get("full_content") or a.get("content") or a.get("description") or ""
            articles.append({
                "title": a.get("title", ""),
                "url": a.get("link", ""),
                "source": a.get("source_id", ""),
                "time": a.get("pubDate", ""),
                "content": content[:2000],
                "image": a.get("image_url", "")
            })
        return articles
    except Exception as e:
        print(f"NewsData fetch error: {e}")
        return []

def format_articles_for_prompt(articles, limit=25, titles_only=False):
    parts = []
    for a in articles[:limit]:
        rel = relative_time(a.get("time", ""))
        time_str = f"PUBLISHED: {rel}\n" if rel else ""
        if titles_only:
            parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}URL: {a['url']}")
        else:
            content = a.get("content", "").strip()
            if content:
                parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}CONTENT: {content[:600]}\nURL: {a['url']}")
            else:
                parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}URL: {a['url']}")
    return "\n---\n".join(parts)

# ── World Topics ──────────────────────────────────────────────────────────────

def fetch_world_topic_sources():
    """Fetch from all sources with fallback chain. Returns merged topic list."""
    topics = []

    # Google News RSS — primary
    google_news = fetch_google_news_rss()
    if google_news:
        topics += [f"[NEWS] {t}" for t in google_news[:15]]
        print(f"Google News RSS: {len(google_news)} topics")
    else:
        print("Google News RSS failed")

    # YouTube trending — cultural signal
    youtube = fetch_youtube_trending()
    if youtube:
        topics += [f"[YOUTUBE] {t}" for t in youtube[:10]]
        print(f"YouTube trending: {len(youtube)} topics")
    else:
        print("YouTube trending failed")

    # Google Trends RSS — search signal
    trends = fetch_google_trends_rss()
    if trends:
        topics += [f"[TRENDING] {t}" for t in trends[:10]]
        print(f"Google Trends RSS: {len(trends)} topics")
    else:
        print("Google Trends RSS failed")

    # Reddit JSON — fallback discussion signal
    if not google_news and not youtube:
        reddit = fetch_reddit_json()
        if reddit:
            topics += [f"[REDDIT] {t}" for t in reddit[:15]]
            print(f"Reddit JSON fallback: {len(reddit)} topics")

    return topics

def process_world_topics(memory):
    """Process today's topics and aggregate weekly/monthly from memory."""
    results = {}

    # TODAY
    raw_topics = fetch_world_topic_sources()
    memory = save_trend_topics(memory, [t.split("] ", 1)[-1] for t in raw_topics])

    if raw_topics:
        formatted = "\n".join(raw_topics)
        prompt = f"""You are a global news and culture editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are signals from multiple sources showing what people are searching, watching, and reading globally today:
{formatted}

[NEWS] = Google News top stories
[YOUTUBE] = YouTube trending videos
[TRENDING] = Google trending searches
[REDDIT] = Reddit top posts

Identify the 5 most significant topics people are talking about globally today. Include the full spectrum — major world events, political controversies, cultural moments, viral stories. For each:
- Write a clean plain English topic label
- Write one sentence explaining why people are paying attention right now
- Note which sources it appeared in

IMPORTANT: Each topic must be a specific named event, person, situation or story — not a generic category.
REJECT topics like: "entertainment news", "sports content", "trending videos", "music releases", "gaming content", "lifestyle news".
ONLY include: named conflicts, named people, named events, specific political situations, specific technological developments.

Return ONLY a JSON array:
[{{"headline":"...","why":"...","signal":"..."}}]
Raw JSON only, no markdown."""
        text = call_haiku(prompt, 800, label="world_topics_today")
        try:
            results["today"] = json.loads(text.replace("```json","").replace("```","").strip())
        except:
            results["today"] = []
    else:
        results["today"] = []

    # WEEK and MONTH — from memory
    results["week"] = aggregate_trend_memory(memory, 7)
    results["month"] = aggregate_trend_memory(memory, 30)

    # Cache results so category-only runs can retrieve them
    memory["world_topics_cache"] = results

    return results, memory


def aggregate_trend_memory(memory, days):
    """Pull raw trend topics from last N days of memory and cluster them via Claude."""
    cutoff = (datetime.now(AEST) - timedelta(days=days)).strftime("%Y-%m-%d")
    raw = []
    for date, topics in memory.get("world_trends", {}).items():
        if date >= cutoff:
            raw += topics
    if not raw:
        return []

    formatted = "\n".join(raw)
    period = "week" if days <= 7 else "month"
    prompt = f"""You are a global news editor. Here are raw trending topic strings collected daily over the past {days} days:
{formatted}

Many of these refer to the same underlying story with slightly different wording.
Cluster them into the top 5 distinct topics that dominated this {period}. For each:
- Write a clean canonical topic label
- Write one sentence on why it dominated
- Note roughly how many days it appeared

IMPORTANT: Each topic must be a specific named event, person, situation or story — not a generic category.
REJECT topics like: "entertainment news", "sports content", "trending videos", "music releases", "gaming content", "lifestyle news".
ONLY include: named conflicts, named people, named events, specific political situations, specific technological developments.

Return ONLY a JSON array:
[{{"headline":"...","why":"...","signal":"trending for X days"}}]
Raw JSON only, no markdown."""
    text = call_haiku(prompt, 800, label=f"world_topics_{period}")
    try:
        return json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

# ── Developing Situations ─────────────────────────────────────────────────────

def process_developing_situations(pinned, auto_detected, all_fetched_articles):
    situations = []
    all_articles_text = format_articles_for_prompt(all_fetched_articles, 40)

    all_topics = []
    for topic in pinned:
        all_topics.append({"topic": topic, "type": "pinned"})
    for topic in auto_detected[:5]:
        if not any(t["topic"].lower() == topic.lower() for t in all_topics):
            all_topics.append({"topic": topic, "type": "auto"})

    if not all_topics:
        return []

    topics_list = "\n".join([f"- {t['topic']}" for t in all_topics])
    prompt = f"""You are a news editor tracking ongoing situations. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

You are tracking these ongoing situations:
{topics_list}

Here are today's fetched articles from all sources:
{all_articles_text}

For each tracked situation, check if any of today's articles contain relevant updates. For each situation:
- If there are relevant articles, write a 2-3 sentence update on what has happened today
- If nothing relevant found, write "No significant updates today"
- Extract any relevant article URLs

Return ONLY a JSON array:
[{{"topic":"...","update":"...","has_update":true,"articles":[{{"title":"...","source":"...","url":"..."}}]}}]
Raw JSON only, no markdown."""

    text = call_haiku(prompt, 1000, label="developing_situations")
    try:
        updates = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        updates = []

    # Index Claude's output by topic for lookup
    updates_by_topic = {u.get("topic","").lower(): u for u in updates}

    # Always include every tracked topic — pinned ones especially must always appear
    for t in all_topics:
        topic = t["topic"]
        u = updates_by_topic.get(topic.lower(), {})
        situations.append({
            "topic": topic,
            "type": t["type"],
            "update": u.get("update", "No significant updates today."),
            "has_update": u.get("has_update", False),
            "articles": u.get("articles", [])
        })
    return situations

# ── Category Processors ───────────────────────────────────────────────────────

def process_breaking_news(gdelt_articles, guardian_articles, memory):
    guardian_urls = {a["url"] for a in guardian_articles}
    all_articles = guardian_articles + [a for a in gdelt_articles if a["url"] not in guardian_urls]
    if not all_articles:
        return [], memory

    formatted = format_articles_for_prompt(all_articles, 30, titles_only=True)
    prompt = f"""You are a world news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles:
{formatted}

Select ONLY stories that are historic in scale — active major wars significantly escalating with large casualties, world leader deaths, terrorist attacks killing hundreds+, catastrophic natural disasters with mass casualties, nuclear threats. DO NOT include diplomatic talks, peace negotiations, ceasefire discussions, court cases, political scandals, or warnings. If nothing meets this bar return [].
CRITICAL: Only include stories where the article describes a specific event that occurred in the last 6 hours. Do NOT include background articles, explainers, or ongoing situation coverage where no new event is described today. If the article is about a situation rather than a specific new development, exclude it.

For each story:
- Write a specific factual headline with real numbers, names, locations
- Assign importance score 1-10
- Estimate timestamp
- Identify a "so_what" broader context thread
- Flag if deeper search needed

ONLY include facts explicitly stated in the article content.

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","score":8,"timestamp":"...","deeper_search":false,"so_what":"...","url":"...","source":""}}]
Raw JSON only, no markdown."""

    text = call_sonnet(prompt, 1200, label="breaking_selection")
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in all_articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title","") or story.get("headline",""), "source": story.get("source",""), "url": story.get("url","")}]
        context = ""
        if story.get("deeper_search") or story.get("so_what"):
            search_q = story.get("so_what") or story["headline"]
            cached_sources = find_related_cached_stories(memory, search_q)
            if cached_sources:
                print(f"Using cached context for: {search_q}")
                articles_list = articles_list + cached_sources
                context = story.get("so_what","")
            else:
                search_prompt = f"""Search for latest news and context about: "{search_q}"
Return ONLY JSON array of up to 5 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
                search_text = call_sonnet_with_search(search_prompt, 800)
                try:
                    extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                    articles_list = extra
                    context = story.get("so_what","")
                except:
                    pass
        ts = relative_time(orig.get("time",""))
        if not ts:
            ts = relative_time(story.get("timestamp",""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content",""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image",""),
            "articles": articles_list,
            "tracking_suggestions": suggestions
        })
    return results, memory

def process_australia(rss_articles, newsdata_articles, memory):
    all_articles = rss_articles + newsdata_articles
    if not all_articles:
        return [], memory

    formatted = format_articles_for_prompt(all_articles, 30, titles_only=True)
    prompt = f"""You are an Australian federal politics editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles:
{formatted}

Select ONLY stories about Australian FEDERAL parliament: bills passed or defeated in the House or Senate, federal budget decisions, federal elections, federal party leadership changes, High Court rulings on federal matters, major national policy changes announced by federal ministers.

REJECT everything else — including:
- State or territory parliament (WA, NSW, Qld, Vic etc.)
- Economic forecasts, modelling, or reports from consultancies or think tanks (e.g. Deloitte, Grattan Institute)
- Business news, commodity prices, fuel costs
- Crime, accidents, weather, sport
- International news
- Anything where no actual parliamentary vote, bill, or federal decision has occurred

If nothing meets this bar return [].

For each story:
- Write a specific factual headline stating what was decided and by whom
- Assign importance score 1-10
- Estimate timestamp
- Identify a "so_what" broader political context
- Set "deeper_search": true ONLY if this is a High Court ruling or major constitutional matter

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","score":7,"timestamp":"...","so_what":"...","url":"...","source":"","deeper_search":false}}]
Raw JSON only, no markdown."""

    text = call_sonnet(prompt, 1000, label="australia_selection")
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in all_articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source",""), "url": story.get("url","")}]
        context = story.get("so_what","")
        if context:
            cached_sources = find_related_cached_stories(memory, context)
            if cached_sources:
                print(f"Using cached context for: {context}")
                articles_list = articles_list + cached_sources
            else:
                search_prompt = f"""Search for context on: "{context}"
Return ONLY JSON array of up to 3 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
                search_text = call_haiku_with_search(search_prompt, 600)
                try:
                    extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                    articles_list = articles_list + extra
                except:
                    pass
        ts = relative_time(orig.get("time",""))
        if not ts:
            ts = relative_time(story.get("timestamp",""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content",""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image",""),
            "articles": articles_list,
            "tracking_suggestions": suggestions
        })
    return results, memory

def process_archaeology(articles, memory):
    if not articles:
        return [], memory

    # Filter out URLs already seen in memory to prevent old stories resurfacing
    seen_urls = set()
    for date, cats in memory.get("stories", {}).items():
        for cat, stories in cats.items():
            for s in stories:
                seen_urls.add(s.get("url", ""))
                for a in s.get("articles", []):
                    seen_urls.add(a.get("url", ""))
    articles = [a for a in articles if a.get("url", "") not in seen_urls]
    if not articles:
        return [], memory

    formatted = format_articles_for_prompt(articles, 30, titles_only=True)
    prompt = f"""You are a science editor specialising in human origins. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles:
{formatted}

Select ONLY significant palaeoanthropological discoveries: new hominin species, fossil finds, ancient DNA findings, discoveries contradicting existing models. Aim for 2-4 stories. If nothing meets the bar return [].

For each story:
- Write a specific factual headline with discovery, location, age, species
- Assign importance score 1-10
- Estimate timestamp
- Identify a "so_what" — which theory does this challenge?

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","score":7,"timestamp":"...","so_what":"...","url":"...","source":""}}]
Raw JSON only, no markdown."""

    text = call_sonnet(prompt, 800, label="archaeology_selection")
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source",""), "url": story.get("url","")}]
        context = story.get("so_what","")
        ts = relative_time(orig.get("time",""))
        if not ts:
            ts = relative_time(story.get("timestamp",""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content",""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image",""),
            "articles": articles_list,
            "tracking_suggestions": suggestions
        })
    return results, memory

def process_football(articles, memory):
    if not articles:
        return [], memory

    formatted = format_articles_for_prompt(articles, 30, titles_only=True)
    prompt = f"""You are a football editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles:
{formatted}

Select ONLY significant stories from Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League. Cover all leagues equally. Only include confirmed results, injuries, sackings, transfers, extraordinary performances. NO rumours or previews. Aim for 6-10 stories. If nothing meets the bar return [].

For each story:
- Write a specific factual headline with actual scores, names, standings
- Assign importance score 1-10
- Estimate timestamp
- Identify "so_what" — title race, Golden Boot, relegation context

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","score":7,"timestamp":"...","so_what":"...","url":"...","source":"","deeper_search":false}}]
Raw JSON only, no markdown."""

    text = call_sonnet(prompt, 1200, label="football_selection")
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source","The Guardian"), "url": story.get("url","")}]
        context = story.get("so_what","")
        if context:
            cached_sources = find_related_cached_stories(memory, context)
            if cached_sources:
                print(f"Using cached context for: {context}")
                articles_list = articles_list + cached_sources
                context = story["so_what"]
            else:
                search_prompt = f"""Search for context on: "{context}"
Return ONLY JSON array of up to 3 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
                search_text = call_haiku_with_search(search_prompt, 600)
                try:
                    extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                    articles_list = articles_list + extra
                    context = story["so_what"]
                except:
                    pass
        ts = relative_time(orig.get("time",""))
        if not ts:
            ts = relative_time(story.get("timestamp",""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content",""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image",""),
            "articles": articles_list,
            "tracking_suggestions": suggestions
        })
    return results, memory

# ── HTML Builder ──────────────────────────────────────────────────────────────

def build_html(all_data, yesterday_data, world_topics, developing_situations, health=None):
    date_str = datetime.now(AEST).strftime("%A %d %B %Y").upper()
    updated_str = datetime.now(AEST).strftime("%I:%M %p AEST").lstrip("0")
    build_ts = int(datetime.now(timezone.utc).timestamp())

    last_run = health["runs"][-1] if health and health.get("runs") else None
    if last_run:
        has_errors = bool(last_run.get("errors"))
        dot_color = "#e67e22" if has_errors else "#2ecc71"
        if last_run.get("errors"):
            tooltip_text = "Issues: " + "; ".join(last_run["errors"][:3])
        else:
            tooltip_text = "All sources OK"
        health_dot = f'<span class="health-dot-wrap" style="position:relative;display:inline-block;vertical-align:middle;margin-right:6px;"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{dot_color};cursor:default;"></span><span class="health-tooltip" style="display:none;position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);background:#1c1c1a;border:1px solid rgba(255,255,255,0.1);border-radius:6px;padding:6px 10px;white-space:nowrap;font-size:11px;font-family:Inter,sans-serif;color:#c8c4bc;pointer-events:none;z-index:100;opacity:0;transition:opacity 0.2s;">{tooltip_text}</span></span>'
    else:
        health_dot = ""

    gh_setup_btn = '''<button id="gh-connect-btn" onclick="setupGhToken()" style="background:none;border:1px solid rgba(255,255,255,0.08);border-radius:6px;cursor:pointer;padding:4px 10px;color:#444440;font-size:11px;font-family:Inter,sans-serif;transition:all 0.15s;display:none;" onmouseover="this.style.color='#f0ece4';this.style.borderColor='rgba(255,255,255,0.2)'" onmouseout="this.style.color='#444440';this.style.borderColor='rgba(255,255,255,0.08)'">Connect GitHub</button>
<button id="gh-connected-btn" style="background:none;border:1px solid rgba(42,122,110,0.3);border-radius:6px;padding:4px 10px;color:#4aaa99;font-size:11px;font-family:Inter,sans-serif;display:none;">&#10003; GitHub connected</button>'''

    logo_html = '''<div style="display:flex;align-items:center;gap:12px;">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 32" width="160" height="26">
    <circle cx="14" cy="16" r="12" fill="none" stroke="#c0392b" stroke-width="1" opacity="0.25"/>
    <circle cx="14" cy="16" r="8" fill="none" stroke="#c0392b" stroke-width="1.2" opacity="0.45"/>
    <circle cx="14" cy="16" r="4.5" fill="#c0392b"/>
    <text x="32" y="22" font-family="\'Playfair Display\',Georgia,serif" font-size="20" font-weight="400" fill="#e8e4dc" letter-spacing="0.02em">Daily Briefing</text>
  </svg>
</div>'''

    def render_story(story, i, ac, is_top=False, is_yesterday=False):
        num = f"0{i+1}" if i+1 < 10 else str(i+1)
        arts = story.get("articles", [])
        summary = story.get("summary","").replace("<","&lt;").replace(">","&gt;")
        score = story.get("score", 5)
        image = story.get("image","")
        headline_escaped = story["headline"].replace("'", "\\'").replace('"', '&quot;')
        # Suggest core topic by stripping match details to get the underlying story
        suggested = story["headline"][:60].rstrip(".,")

        meta_parts = []
        if story.get("timestamp"):
            meta_parts.append(f'<span style="color:{ac};font-weight:500;font-size:11px;">{story["timestamp"]}</span>')
        if arts and arts[0].get("source"):
            meta_parts.append(f'<span style="color:#555550;font-size:11px;">{arts[0].get("source","")}</span>')
        if len(arts) > 1:
            meta_parts.append(f'<span style="color:#444440;font-size:11px;">{len(arts)} sources</span>')
        meta_html = ' <span style="color:#2a2a28;font-size:10px;">·</span> '.join(meta_parts)

        headline_size = "17px" if is_top else "15px"
        card_bg = "#1c1c1a" if is_top else "#161614"
        card_border = "1px solid rgba(255,255,255,0.09)" if is_top else "1px solid rgba(255,255,255,0.04)"
        score_dot = f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{ac};margin-right:8px;margin-bottom:1px;vertical-align:middle;flex-shrink:0;"></span>' if score >= 8 else ""
        opacity = "0.55" if is_yesterday else "1"

        suggestions_json = json.dumps(story.get("tracking_suggestions", [suggested]))
        suggestions_attr = suggestions_json.replace('"', '&quot;')
        star_btn = "" if is_yesterday else f'<button class="star-btn" onclick="showStarPopup(\'{headline_escaped}\',{suggestions_attr});event.stopPropagation();" title="Track this story" style="background:none;border:none;cursor:pointer;padding:4px;color:#333330;font-size:14px;flex-shrink:0;line-height:1;margin-left:4px;transition:color 0.15s;" onmouseover="this.style.color=\'#c9a96e\'" onmouseout="this.style.color=\'#333330\'">&#9734;</button>'

        summary_escaped = story.get("summary","").replace("'", "\\'").replace('"', '&quot;').replace("\n", " ")
        articles_json = json.dumps(story.get("articles", [])).replace('"', '&quot;')

        return f'''<div class="story" onclick="openModal('{headline_escaped}','{summary_escaped}',{articles_json},'{image}')" style="border-radius:10px;background:{card_bg};border:{card_border};margin-bottom:8px;opacity:{opacity};cursor:pointer;transition:border-color 0.2s;" onmouseover="this.style.borderColor='rgba(255,255,255,0.15)'" onmouseout="this.style.borderColor=''" >
  <div style="display:flex;align-items:flex-start;gap:14px;padding:16px 18px;border-radius:10px;">
    <span style="font-size:11px;color:#2a2a28;min-width:20px;margin-top:3px;flex-shrink:0;">{num}</span>
    <div style="flex:1;min-width:0;">
      <div style="font-size:{headline_size};font-weight:400;line-height:1.45;color:#f0ece4;margin-bottom:8px;letter-spacing:-0.01em;display:flex;align-items:flex-start;">{score_dot}<span>{story["headline"].replace("<","&lt;").replace(">","&gt;")}</span></div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">{meta_html}</div>
    </div>
    {star_btn}
  </div>
</div>'''

    # World topics section
    def render_world_topics():
        tabs_html = ""
        panels_html = ""
        for idx, (label, display) in enumerate([("today","Today"), ("week","This Week"), ("month","This Month")]):
            stories = world_topics.get(label, [])
            active = "true" if idx == 0 else "false"
            tab_style = f'padding:6px 16px;border-radius:999px;font-size:12px;cursor:pointer;border:none;font-family:Inter,sans-serif;font-weight:{"500" if idx==0 else "400"};background:{"rgba(123,104,200,0.2)" if idx==0 else "transparent"};color:{"#b8b0e8" if idx==0 else "#555550"};'
            tabs_html += f'<button onclick="switchTab(\'{label}\')" id="tab-{label}" style="{tab_style}">{display}</button>'

            stories_html = ""
            if not stories:
                stories_html = '<p style="color:#333330;font-size:13px;padding:1rem 0;">No data available.</p>'
            else:
                for i, s in enumerate(stories[:5]):
                    num = f"0{i+1}" if i+1 < 10 else str(i+1)
                    stories_html += f'''<div style="display:flex;gap:14px;padding:14px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
  <span style="font-size:11px;color:#2a2a28;min-width:20px;margin-top:2px;flex-shrink:0;">{num}</span>
  <div style="flex:1;">
    <div style="font-size:14px;font-weight:400;color:#f0ece4;line-height:1.4;margin-bottom:4px;">{s.get("headline","").replace("<","&lt;").replace(">","&gt;")}</div>
    <div style="font-size:12px;color:#555550;line-height:1.5;">{s.get("why","").replace("<","&lt;").replace(">","&gt;")}</div>
    <div style="font-size:11px;color:#333330;margin-top:4px;">{s.get("coverage","")}</div>
  </div>
</div>'''

            display_style = "block" if idx == 0 else "none"
            panels_html += f'<div id="panel-{label}" style="display:{display_style};">{stories_html}</div>'

        return f'''<div>
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:10px;">
      <div style="width:3px;height:24px;border-radius:2px;background:#7b68c8;flex-shrink:0;"></div>
      <div style="font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:500;letter-spacing:-0.01em;">What the world is talking about</div>
      <button id="refresh-world_topics" onclick="triggerCategoryRefresh('world_topics')" title="Refresh World Topics" style="background:none;border:none;cursor:pointer;padding:4px;color:#2a2a28;font-size:13px;flex-shrink:0;line-height:1;transition:color 0.15s;display:none;" onmouseover="this.style.color='#6e6b64'" onmouseout="this.style.color='#2a2a28'">↻</button>
    </div>
    <div style="display:flex;gap:4px;">{tabs_html}</div>
  </div>
  {panels_html}
</div>'''

    # Developing situations section
    def render_developing():
        ac = ACCENTS["developing"]
        items_html = ""
        if not developing_situations:
            items_html = '<p style="font-size:13px;line-height:1.7;color:#8a8680;font-style:italic;padding:1rem 0;">No situations being tracked. Star a story to start tracking.</p>'
        for s in developing_situations:
            badge = f'<span style="font-size:10px;padding:2px 8px;border-radius:999px;background:{"rgba(42,122,110,0.2)" if s["type"]=="auto" else "rgba(123,104,200,0.2)"};color:{"#4aaa99" if s["type"]=="auto" else "#b8b0e8"};margin-left:8px;vertical-align:middle;">{"auto" if s["type"]=="auto" else "pinned"}</span>'
            arts = s.get("articles",[])
            art_html = "".join([
                f'<a href="{a.get("url","#")}" target="_blank" rel="noreferrer noopener" style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 12px;border-radius:8px;background:#0d0d0c;text-decoration:none;margin-bottom:3px;border:1px solid rgba(255,255,255,0.04);">'
                f'<span style="font-size:13px;color:#c8c4bc;line-height:1.4;flex:1;font-weight:300;">{a.get("title","").replace("<","&lt;").replace(">","&gt;")}</span>'
                f'<span style="font-size:11px;color:#444440;white-space:nowrap;flex-shrink:0;margin-left:8px;">{a.get("source","")}</span></a>'
                for a in arts if a.get("url","").startswith("http")
            ]) if s.get("has_update") else ""

            update_style = "font-size:13px;line-height:1.7;color:#8a8680;" if s.get("has_update") else "font-size:13px;line-height:1.7;color:#8a8680;font-style:italic;"
            sit_id = f"sit-{urllib.parse.quote(s['topic'], safe='')}"
            remove_btn = f'<button onclick="removeSituation(\'{s["topic"].replace(chr(39), chr(92)+chr(39))}\')" title="Stop tracking" style="background:none;border:none;cursor:pointer;color:#333330;font-size:16px;padding:0;line-height:1;transition:color 0.15s;" onmouseover="this.style.color=\'#c0392b\'" onmouseout="this.style.color=\'#333330\'">&#215;</button>'
            items_html += f'''<div id="{sit_id}" style="background:#161614;border:1px solid rgba(255,255,255,0.05);border-radius:10px;padding:16px 18px;margin-bottom:8px;transition:opacity 0.4s;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
    <div style="font-size:14px;font-weight:500;color:#f0ece4;">{s["topic"].replace("<","&lt;").replace(">","&gt;")}{badge}</div>
    {remove_btn}
  </div>
  <div style="{update_style}">{s.get("update","No updates today.").replace("<","&lt;").replace(">","&gt;")}</div>
  {f'<div style="margin-top:10px;">{art_html}</div>' if art_html else ""}
</div>'''

        return f'''<div>
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:10px;">
      <div style="width:3px;height:24px;border-radius:2px;background:{ac};flex-shrink:0;"></div>
      <div style="font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:500;letter-spacing:-0.01em;">Developing situations</div>
    </div>
  </div>
  {items_html}
</div>'''

    # Breaking news full width
    breaking_html = ""
    ac_b = ACCENTS["breaking"]
    breaking_stories = all_data.get("breaking", [])
    if not breaking_stories:
        breaking_stories_html = '<p style="padding:1.5rem 0.5rem;color:#333330;font-size:13px;">Nothing significant right now.</p>'
    else:
        breaking_stories_html = '<div style="display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:10px;">'
        for i, story in enumerate(breaking_stories[:3]):
            is_top = (i == 0)
            hl_size = "16px" if is_top else "13px"
            card_bg = "#1e1816" if is_top else "#1c1c1a"
            card_border = "rgba(192,57,43,0.25)" if is_top else "rgba(255,255,255,0.07)"
            dot = f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{ac_b};margin-right:8px;margin-bottom:1px;vertical-align:middle;"></span>' if is_top else ""
            arts = story.get("articles", [])
            source = arts[0].get("source","") if arts else ""
            sources_count = f" · {len(arts)} sources" if len(arts) > 1 else ""
            ts = story.get("timestamp","")
            meta = " · ".join(filter(None, [ts, source])) + sources_count
            headline_escaped = story["headline"].replace("'", "\\'").replace('"', '&quot;')
            summary_escaped = story.get("summary","").replace("'", "\\'").replace('"', '&quot;').replace("\n", " ")
            articles_json = json.dumps(story.get("articles", [])).replace('"', '&quot;')
            breaking_stories_html += f'''<div onclick="openModal('{headline_escaped}','{summary_escaped}',{articles_json},'{story.get('image','')}')" style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:16px 18px;cursor:pointer;transition:border-color 0.2s;" onmouseover="this.style.borderColor='rgba(255,255,255,0.15)'" onmouseout="this.style.borderColor='{card_border}'">
  <div style="font-size:{hl_size};line-height:1.5;color:#f0ece4;font-weight:400;margin-bottom:8px;">{dot}{story["headline"].replace("<","&lt;").replace(">","&gt;")}</div>
  <div style="font-size:11px;color:#555550;">{meta}</div>
</div>'''
        breaking_stories_html += '</div>'
    yesterday_breaking = yesterday_data.get("breaking",[])
    yest_b_html = ""
    if yesterday_breaking:
        prev_cards = ""
        for i, s in enumerate(yesterday_breaking[:3]):
            hl = s["headline"].replace("<","&lt;").replace(">","&gt;")
            ts = s.get("timestamp","")
            headline_esc = s["headline"].replace("'", "\\'").replace('"', '&quot;')
            summary_esc = s.get("summary","").replace("'", "\\'").replace('"', '&quot;').replace("\n", " ")
            arts_json = json.dumps(s.get("articles", [])).replace('"', '&quot;')
            img = s.get("image","")
            prev_cards += f'''<div onclick="openModal('{headline_esc}','{summary_esc}',{arts_json},'{img}')" style="background:#161614;border:1px solid rgba(255,255,255,0.05);border-radius:10px;padding:14px 16px;cursor:pointer;transition:border-color 0.2s;" onmouseover="this.style.borderColor='rgba(255,255,255,0.15)'" onmouseout="this.style.borderColor='rgba(255,255,255,0.05)'">
  <div style="font-size:12px;line-height:1.5;color:#c8c4bc;font-weight:400;margin-bottom:6px;">{hl}</div>
  <div style="font-size:11px;color:#444440;">{ts}</div>
</div>'''
        yest_b_html = f'''<div style="margin-top:1.5rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,0.05);opacity:0.45;">
  <p style="font-size:11px;color:#555550;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;text-align:center;">Previously</p>
  <div style="display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:10px;">{prev_cards}</div>
</div>'''

    breaking_html = f'''<div style="margin-bottom:3.5rem;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:10px;">
      <div style="width:3px;height:24px;border-radius:2px;background:{ac_b};flex-shrink:0;"></div>
      <div style="font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:500;letter-spacing:-0.01em;">Breaking News</div>
      <button id="refresh-breaking" onclick="triggerCategoryRefresh('breaking')" title="Refresh Breaking News" style="background:none;border:none;cursor:pointer;padding:4px;color:#2a2a28;font-size:13px;flex-shrink:0;line-height:1;transition:color 0.15s;display:none;" onmouseover="this.style.color='#6e6b64'" onmouseout="this.style.color='#2a2a28'">↻</button>
    </div>
    <span style="font-size:11px;color:#2a2a28;">Updated {updated_str}</span>
  </div>
  {breaking_stories_html}
  {yest_b_html}
</div>'''

    # 3-column grid
    col_categories = [
        {"id": "australia", "label": "Australia", "data": all_data["australia"], "yesterday": yesterday_data.get("australia",[])},
        {"id": "archaeology", "label": "Archaeology & Palaeoanthropology", "data": all_data["archaeology"], "yesterday": yesterday_data.get("archaeology",[])},
        {"id": "football", "label": "Football", "data": all_data["football"], "yesterday": yesterday_data.get("football",[])}
    ]

    cols_html = ""
    for cat in col_categories:
        ac = ACCENTS[cat["id"]]
        stories = cat["data"]
        if not stories:
            s_html = '<p style="padding:1rem 0.5rem;color:#333330;font-size:13px;">Nothing significant right now.</p>'
        else:
            s_html = ""
            for i, story in enumerate(stories):
                s_html += render_story(story, i, ac, is_top=(i==0))
        yest_html = ""
        if cat["yesterday"]:
            yest_html = f'<div style="margin-top:1rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,0.05);"><p style="font-size:11px;color:#333330;letter-spacing:0.05em;text-transform:uppercase;margin-bottom:8px;">Previously</p>'
            for i, s in enumerate(cat["yesterday"]):
                yest_html += render_story(s, i, ac, is_yesterday=True)
            yest_html += "</div>"
        refresh_btn = f'<button id="refresh-{cat["id"]}" onclick="triggerCategoryRefresh(\'{cat["id"]}\')" title="Refresh {cat["label"]}" style="background:none;border:none;cursor:pointer;padding:4px;color:#2a2a28;font-size:12px;flex-shrink:0;line-height:1;transition:color 0.15s;display:none;" onmouseover="this.style.color=\'#6e6b64\'" onmouseout="this.style.color=\'#2a2a28\'">↻</button>'
        count_badge = f'<span style="font-size:10px;color:#333330;background:rgba(255,255,255,0.05);padding:2px 8px;border-radius:999px;">{len(stories)}</span>'
        cols_html += f'''<div style="min-width:0;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="width:3px;height:22px;border-radius:2px;background:{ac};flex-shrink:0;"></div>
    <div style="font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:500;letter-spacing:-0.01em;">{cat["label"]}</div>
    {count_badge}
    {refresh_btn}
  </div>
  {s_html}
  {yest_html}
</div>'''

    grid_html = f'<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:2rem;margin-bottom:3.5rem;">{cols_html}</div>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Daily Briefing</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,500;0,700;1,500&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body{{background:#111110;color:#f0ece4;font-family:'Inter',sans-serif;font-size:15px;line-height:1.6;min-height:100vh;}}
.story-header:hover{{background:rgba(255,255,255,0.02);}}
.story-header:hover .chev{{color:#6e6b64;}}
.health-dot-wrap:hover .health-tooltip{{display:block !important;opacity:1 !important;}}
@media(max-width:768px){{
  .grid-3{{grid-template-columns:1fr!important;}}
}}
</style>
</head>
<body>
<div style="max-width:1600px;margin:0 auto;padding:3rem 3rem 6rem;">

  <div style="margin-bottom:3rem;padding-bottom:1.5rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#333330;margin-bottom:12px;">{date_str}</div>
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:12px;">
      {logo_html}
      <div style="display:flex;align-items:center;gap:10px;padding-bottom:6px;">
        {gh_setup_btn}
        <span style="font-size:11px;color:#2a2a28;" id="refresh-status">{health_dot}Refreshes automatically</span>
      </div>
    </div>
  </div>

  {breaking_html}
  <div style="display:grid;grid-template-columns:3fr 2fr;gap:2rem;margin-bottom:3.5rem;">
    {render_world_topics()}
    {render_developing()}
  </div>
  <div class="grid-3" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:2rem;margin-bottom:3.5rem;">{cols_html}</div>

</div>

<!-- Story modal -->
<div id="modal-overlay" onclick="closeModal()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:2000;align-items:center;justify-content:center;padding:2rem;">
  <div id="modal-box" onclick="event.stopPropagation()" style="background:#1c1c1a;border:1px solid rgba(255,255,255,0.12);border-radius:16px;width:min(780px,90vw);max-height:85vh;overflow-y:auto;transform:translateY(20px);opacity:0;transition:transform 0.25s ease,opacity 0.25s ease;">
    <div style="padding:2rem 2rem 0;">
      <div id="modal-image" style="display:none;width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:10px;margin-bottom:1.5rem;"><img id="modal-img-el" style="width:100%;height:100%;object-fit:cover;" onerror="document.getElementById('modal-image').style.display='none'"/></div>
      <div id="modal-headline" style="font-family:'Playfair Display',serif;font-size:1.4rem;font-weight:500;line-height:1.4;color:#f0ece4;margin-bottom:1.25rem;"></div>
      <div id="modal-summary" style="font-size:14px;line-height:1.8;color:#8a8680;border-left:2px solid #555550;padding:12px 16px;border-radius:0 8px 8px 0;background:#111110;margin-bottom:1.5rem;"></div>
    </div>
    <div id="modal-articles" style="padding:0 2rem 2rem;display:flex;flex-direction:column;gap:6px;"></div>
    <button onclick="closeModal()" style="position:sticky;bottom:0;display:block;width:100%;padding:14px;background:#111110;border:none;border-top:1px solid rgba(255,255,255,0.07);color:#555550;font-size:13px;cursor:pointer;border-radius:0 0 16px 16px;font-family:Inter,sans-serif;transition:color 0.15s;" onmouseover="this.style.color='#f0ece4'" onmouseout="this.style.color='#555550'">Close</button>
  </div>
</div>

<!-- Star popup overlay -->
<div id="star-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
  <div style="background:#1c1c1a;border:1px solid rgba(255,255,255,0.12);border-radius:16px;padding:1.5rem;width:min(440px,90vw);">
    <div style="font-size:14px;font-weight:500;color:#f0ece4;margin-bottom:6px;">Track this situation</div>
    <div id="star-headline-preview" style="font-size:12px;color:#555550;margin-bottom:14px;font-style:italic;line-height:1.4;"></div>
    <div id="star-pills" style="margin-bottom:10px;"></div>
    <div style="font-size:12px;color:#6e6b64;margin-bottom:6px;">Or type a custom topic:</div>
    <input id="star-input" type="text" style="width:100%;padding:10px 14px;border-radius:8px;border:1px solid rgba(255,255,255,0.12);background:#111110;color:#f0ece4;font-size:14px;font-family:Inter,sans-serif;outline:none;margin-bottom:10px;" placeholder="e.g. Arsenal title race" onkeydown="if(event.key==='Enter')confirmStar();if(event.key==='Escape')closeStarPopup();"/>
    <div id="star-status" style="font-size:12px;color:#4aaa99;margin-bottom:12px;min-height:18px;"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;">
      <button onclick="closeStarPopup()" style="padding:7px 16px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#6e6b64;cursor:pointer;font-family:Inter,sans-serif;font-size:13px;">Cancel</button>
      <button onclick="confirmStar()" style="padding:7px 16px;border-radius:8px;border:none;background:#c9a96e;color:#000;cursor:pointer;font-family:Inter,sans-serif;font-size:13px;font-weight:500;">Track</button>
    </div>
  </div>
</div>

<script>
var BUILD_TS = {build_ts};
var GITHUB_REPO = "chravis999888/Daily-Briefing";
var PINNED_FILE_PATH = "pinned.txt";
var ghToken = localStorage.getItem("gh_token");

// ── GitHub token setup ──
function ensureGhToken(cb) {{
  if (ghToken) {{ cb(ghToken); return; }}
  var tok = prompt("Enter your GitHub Personal Access Token to enable story tracking:\\n(One-time setup — stored locally in your browser)");
  if (!tok) return;
  ghToken = tok.trim();
  localStorage.setItem("gh_token", ghToken);
  cb(ghToken);
}}

// ── Read pinned.txt from GitHub ──
function getPinned(token, cb) {{
  fetch("https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + PINNED_FILE_PATH + "?t=" + Date.now(), {{
    headers: {{ "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Cache-Control": "no-cache" }}
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    var content = data.content ? atob(data.content.replace(/\\n/g,"")) : "";
    var sha = data.sha || "";
    var topics = content.split("\\n").map(function(l){{return l.trim();}}).filter(Boolean);
    cb(topics, sha, content);
  }})
  .catch(function() {{ cb([], "", ""); }});
}}

// ── Write pinned.txt to GitHub ──
function writePinned(token, topics, sha, cb) {{
  var content = btoa(topics.join("\\n") + (topics.length ? "\\n" : ""));
  var body = {{ message: "Update pinned situations", content: content }};
  if (sha) body.sha = sha;
  fetch("https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + PINNED_FILE_PATH, {{
    method: "PUT",
    headers: {{ "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": "application/json" }},
    body: JSON.stringify(body)
  }})
  .then(function(r) {{ if (cb) cb(r.ok); }})
  .catch(function() {{ if (cb) cb(false); }});
}}

// ── GitHub token setup ──
function setupGhToken() {{
  var tok = prompt("Enter your GitHub Personal Access Token:\\n(One-time setup — stored locally in your browser)\\n\\nNeeds 'repo' scope at github.com/settings/tokens");
  if (!tok) return;
  ghToken = tok.trim();
  localStorage.setItem("gh_token", ghToken);
  updateGhButtons();
}}

function updateGhButtons() {{
  var connectBtn = document.getElementById('gh-connect-btn');
  var connectedBtn = document.getElementById('gh-connected-btn');
  if (ghToken) {{
    if (connectBtn) connectBtn.style.display = 'none';
    if (connectedBtn) connectedBtn.style.display = 'inline-block';
    document.querySelectorAll('[id^="refresh-"]').forEach(function(btn) {{ btn.style.display = 'inline-block'; }});
  }} else {{
    if (connectBtn) connectBtn.style.display = 'inline-block';
    if (connectedBtn) connectedBtn.style.display = 'none';
  }}
}}
// Run on page load
updateGhButtons();

// ── Star popup ──
function showStarPopup(headline, suggestions) {{
  var overlay = document.getElementById("star-overlay");
  var input = document.getElementById("star-input");
  var status = document.getElementById("star-status");
  var pillsContainer = document.getElementById("star-pills");
  overlay.style.display = "flex";
  input.value = (suggestions && suggestions[0]) ? suggestions[0] : "";
  status.textContent = "";
  if (pillsContainer) {{
    pillsContainer.innerHTML = "";
    (suggestions || []).forEach(function(s) {{
      var pill = document.createElement("button");
      pill.textContent = s;
      pill.style.cssText = "padding:5px 12px;border-radius:999px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#8a8680;cursor:pointer;font-size:12px;font-family:Inter,sans-serif;margin:3px;transition:all 0.15s;";
      pill.onmouseover = function(){{this.style.background='rgba(201,169,110,0.15)';this.style.color='#c9a96e';this.style.borderColor='rgba(201,169,110,0.3)';}};
      pill.onmouseout = function(){{this.style.background='transparent';this.style.color='#8a8680';this.style.borderColor='rgba(255,255,255,0.1)';}};
      pill.onclick = function(){{input.value=s;}};
      pillsContainer.appendChild(pill);
    }});
  }}
  document.getElementById("star-headline-preview").textContent = '"' + headline.substring(0,80) + (headline.length>80?"...":'"');
  input.focus();
  input.select();
}}

function closeStarPopup() {{
  document.getElementById("star-overlay").style.display = "none";
}}

function confirmStar() {{
  var topic = document.getElementById("star-input").value.trim();
  if (!topic) return;
  var status = document.getElementById("star-status");
  status.textContent = "Saving...";
  ensureGhToken(function(token) {{
    getPinned(token, function(topics, sha) {{
      if (topics.indexOf(topic) === -1) topics.push(topic);
      writePinned(token, topics, sha, function(ok) {{
        if (ok) {{
          status.textContent = "✓ Now tracking: " + topic;
          fetch('https://api.github.com/repos/' + GITHUB_REPO + '/actions/workflows/briefing.yml/dispatches', {{
            method: 'POST',
            headers: {{ 'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ ref: 'main', inputs: {{ mode: 'deploy_only', category: '' }} }})
          }}).then(function() {{
            status.textContent = "✓ Tracked — briefing updating...";
            setTimeout(closeStarPopup, 2000);
          }});
        }} else {{
          status.textContent = "Failed — check your token";
        }}
      }});
    }});
  }});
}}

function removeSituation(topic) {{
  ensureGhToken(function(token) {{
    getPinned(token, function(topics, sha) {{
      var updated = topics.filter(function(t) {{ return t !== topic; }});
      writePinned(token, updated, sha, function(ok) {{
        if (ok) {{
          var el = document.getElementById("sit-" + encodeURIComponent(topic));
          if (el) el.style.opacity = "0.3";
          setTimeout(function() {{ if(el) el.remove(); }}, 400);
        }}
      }});
    }});
  }});
}}

// ── Story modal ──
function openModal(headline, summary, articles, image) {{
  document.getElementById('modal-headline').textContent = headline;
  document.getElementById('modal-summary').textContent = summary;
  var imgWrap = document.getElementById('modal-image');
  var imgEl = document.getElementById('modal-img-el');
  if (image && image.startsWith('http')) {{
    imgEl.src = image;
    imgWrap.style.display = 'block';
  }} else {{
    imgWrap.style.display = 'none';
  }}
  var artContainer = document.getElementById('modal-articles');
  artContainer.innerHTML = '';
  if (articles && articles.length) {{
    articles.forEach(function(a) {{
      if (!a.url || !a.url.startsWith('http')) return;
      var el = document.createElement('a');
      el.href = a.url;
      el.target = '_blank';
      el.rel = 'noreferrer noopener';
      el.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;border-radius:8px;background:#111110;text-decoration:none;border:1px solid rgba(255,255,255,0.05);transition:border-color 0.15s;';
      el.onmouseover = function(){{this.style.borderColor='rgba(255,255,255,0.12)';}};
      el.onmouseout = function(){{this.style.borderColor='rgba(255,255,255,0.05)';}};
      el.innerHTML = '<span style="font-size:13px;color:#c8c4bc;line-height:1.4;flex:1;font-weight:300;">' + (a.title||'').replace(/</g,'&lt;') + '</span><span style="font-size:11px;color:#444440;white-space:nowrap;flex-shrink:0;margin-left:8px;">' + (a.source||'') + '</span>';
      artContainer.appendChild(el);
    }});
  }}
  var overlay = document.getElementById('modal-overlay');
  var box = document.getElementById('modal-box');
  overlay.style.display = 'flex';
  box.scrollTop = 0;
  setTimeout(function() {{
    box.style.transform = 'translateY(0)';
    box.style.opacity = '1';
  }}, 10);
}}

function closeModal() {{
  var overlay = document.getElementById('modal-overlay');
  var box = document.getElementById('modal-box');
  box.style.transform = 'translateY(20px)';
  box.style.opacity = '0';
  setTimeout(function() {{ overlay.style.display = 'none'; }}, 250);
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeModal();
}});

// ── World tabs ──
function switchTab(label){{
  ['today','week','month'].forEach(function(l){{
    var panel=document.getElementById('panel-'+l);
    var tab=document.getElementById('tab-'+l);
    if(panel) panel.style.display = l===label?'block':'none';
    if(tab){{
      tab.style.background = l===label?'rgba(123,104,200,0.2)':'transparent';
      tab.style.color = l===label?'#b8b0e8':'#555550';
      tab.style.fontWeight = l===label?'500':'400';
    }}
  }});
}}

// ── Category refresh ──
function triggerCategoryRefresh(category) {{
  if (!ghToken) {{
    setupGhToken();
    if (!ghToken) return;
  }}
  var btn = document.getElementById("refresh-" + category);
  if (btn) btn.textContent = "…";
  fetch("https://api.github.com/repos/" + GITHUB_REPO + "/actions/workflows/briefing.yml/dispatches", {{
    method: "POST",
    headers: {{ "Authorization": "Bearer " + ghToken, "Accept": "application/vnd.github+json", "Content-Type": "application/json" }},
    body: JSON.stringify({{ ref: "main", inputs: {{ mode: "category", category: category }} }})
  }})
  .then(function(r) {{
    if (btn) btn.textContent = r.ok ? "✓" : "✗";
    setTimeout(function() {{ if (btn) btn.textContent = "↻"; }}, 3000);
  }})
  .catch(function() {{ if (btn) {{ btn.textContent = "✗"; setTimeout(function() {{ btn.textContent = "↻"; }}, 3000); }} }});
}}

// (refresh button visibility handled by updateGhButtons above)

// ── Auto-refresh ──
setInterval(function(){{
  fetch(window.location.href+'?ts='+Date.now())
    .then(function(r){{return r.text();}})
    .then(function(html){{
      var match = html.match(/var BUILD_TS = (\\d+)/);
      if(match && parseInt(match[1]) > BUILD_TS){{
        document.getElementById('refresh-status').textContent = 'New update — reloading...';
        setTimeout(function(){{ window.location.reload(); }}, 2000);
      }}
    }}).catch(function(){{}});
}}, 5 * 60 * 1000);</script>
</body>
</html>'''

# ── Mock Mode ─────────────────────────────────────────────────────────────────

def mock_data():
    all_data = {
        "breaking": [
            {
                "headline": "Russian forces launch largest missile barrage of the war, striking Kyiv and 6 other cities simultaneously with 180 drones and 40 cruise missiles",
                "score": 9, "timestamp": "2 hrs ago",
                "summary": "Russia launched its largest coordinated missile attack of the conflict overnight, firing 180 Shahed drones and 40 cruise missiles at Ukrainian cities. Ukrainian air defences intercepted around 130 projectiles but at least 40 struck targets in Kyiv, Kharkiv, Zaporizhzhia, Dnipro and three other cities. At least 23 civilians were killed and 91 injured. The strikes targeted energy infrastructure, knocking out power to 1.4 million homes. The attack came hours after peace talks in Istanbul were suspended without agreement.",
                "image": "https://picsum.photos/seed/war/780/440",
                "articles": [
                    {"title": "Russia fires record 180 drones and 40 cruise missiles at Ukraine overnight", "source": "The Guardian", "url": "https://theguardian.com"},
                    {"title": "Ukraine says 23 dead after Russia's largest ever missile attack", "source": "Reuters", "url": "https://reuters.com"},
                    {"title": "Istanbul peace talks collapse hours before missile barrage", "source": "BBC News", "url": "https://bbc.com"},
                ]
            },
            {
                "headline": "7.8-magnitude earthquake strikes southern Turkey near Syrian border, 340 confirmed dead as rescuers search collapsed buildings",
                "score": 8, "timestamp": "4 hrs ago",
                "summary": "A powerful 7.8-magnitude earthquake struck Hatay province in southern Turkey at 3:14 AM local time, collapsing hundreds of buildings and killing at least 340 people. The quake was felt across Lebanon, Syria and Cyprus. Turkish emergency services and international rescue teams have deployed to the region. The same area was devastated by a catastrophic earthquake in February 2023 that killed over 50,000 people.",
                "image": "https://picsum.photos/seed/quake/780/440",
                "articles": [
                    {"title": "Earthquake kills 340 in Turkey's Hatay province, thousands missing", "source": "AP", "url": "https://apnews.com"},
                    {"title": "Turkey earthquake: rescuers race to pull survivors from rubble", "source": "BBC News", "url": "https://bbc.com"},
                    {"title": "Same region hit by 2023 disaster faces renewed catastrophe", "source": "Al Jazeera", "url": "https://aljazeera.com"},
                ]
            },
            {
                "headline": "North Korea fires three ballistic missiles into Sea of Japan, US and South Korea scramble jets in response",
                "score": 7, "timestamp": "6 hrs ago",
                "summary": "North Korea launched three short-range ballistic missiles from the Sunan area near Pyongyang early Saturday morning, all landing in the Sea of Japan within Japan's exclusive economic zone. The launches came one day after the US and South Korea concluded joint naval exercises in the region. South Korea's Joint Chiefs of Staff condemned the launches and the US Indo-Pacific Command issued a statement calling the launches destabilising.",
                "image": "",
                "articles": [
                    {"title": "North Korea fires three ballistic missiles toward Japan", "source": "Reuters", "url": "https://reuters.com"},
                    {"title": "UN Security Council to hold emergency session over DPRK launches", "source": "The Guardian", "url": "https://theguardian.com"},
                ]
            },
        ],
        "australia": [
            {
                "headline": "Senate passes $14.6bn housing bill after Greens withdraw opposition in exchange for social housing funding boost",
                "score": 8, "timestamp": "3 hrs ago",
                "summary": "The Albanese government's flagship housing legislation passed the Senate 36-34 after the Greens agreed to support the bill following a last-minute deal that increases social housing funding by $1.2 billion. The Help to Buy scheme will allow 40,000 Australians per year to purchase homes with a government equity contribution of up to 40 percent. The Coalition opposed the bill, arguing it will inflate house prices. Housing Minister Clare O'Neil called it the most significant federal housing intervention in a generation.",
                "image": "",
                "articles": [
                    {"title": "Help to Buy housing bill passes Senate after Greens do deal", "source": "ABC News", "url": "https://abc.net.au"},
                    {"title": "Greens secure $1.2bn social housing boost in exchange for housing vote", "source": "SMH", "url": "https://smh.com.au"},
                    {"title": "Opposition slams housing scheme as inflationary after Senate defeat", "source": "The Australian", "url": "https://theaustralian.com.au"},
                ]
            },
            {
                "headline": "High Court rules NSW government's koala protection policy unconstitutional, opening 2.3 million hectares to logging",
                "score": 7, "timestamp": "5 hrs ago",
                "summary": "Australia's High Court voted 5-2 to strike down New South Wales' koala habitat protection overlays, finding they exceeded state environmental planning powers. The ruling potentially reopens 2.3 million hectares of coastal forest to logging that had been protected since 2021. Environmental groups called it a catastrophic setback while the timber industry welcomed the decision. The Minns government said it would introduce new legislation within 60 days to restore protections.",
                "image": "",
                "articles": [
                    {"title": "High Court strikes down NSW koala habitat protections in 5-2 ruling", "source": "SMH", "url": "https://smh.com.au"},
                    {"title": "Minns government promises new koala legislation within 60 days", "source": "ABC News", "url": "https://abc.net.au"},
                ]
            },
        ],
        "archaeology": [
            {
                "headline": "750,000-year-old stone tools found in Philippines challenge theory that only Homo erectus reached island Southeast Asia this early",
                "score": 9, "timestamp": "1 day ago",
                "summary": "Archaeologists excavating Luzon's Cagayan Valley have uncovered 754 stone tools dated to approximately 750,000 years ago using argon-argon and paleomagnetic dating methods. The tools pre-date the oldest known fossils of Homo luzonensis by 600,000 years and are far too early to be attributed to modern humans or Denisovans. The find suggests an unknown hominin species capable of crossing open water reached the Philippine archipelago during the Early Pleistocene, upending current models of early human dispersal in Southeast Asia.",
                "image": "",
                "articles": [
                    {"title": "Stone tools push back human presence in Philippines by 200,000 years", "source": "Nature", "url": "https://nature.com"},
                    {"title": "Mystery hominin crossed open ocean to reach Philippines 750,000 years ago", "source": "New Scientist", "url": "https://newscientist.com"},
                    {"title": "Cagayan Valley dig upends Southeast Asian prehistory", "source": "Science", "url": "https://science.org"},
                ]
            },
            {
                "headline": "Ancient DNA from 6,000-year-old Irish megalith reveals first-cousin marriage among Neolithic elites and a distinct genetic lineage that vanished",
                "score": 7, "timestamp": "2 days ago",
                "summary": "Genomic analysis of 36 individuals buried in the Newgrange passage tomb between 3200 and 2900 BCE shows that the central burial belonged to a man whose parents were first-degree relatives — most likely a brother and sister — indicating deliberate elite inbreeding similar to later Egyptian pharaohs and Inca rulers. The study also identified a distinct Neolithic genetic lineage with no detectable ancestry in modern Europeans, suggesting this population was largely replaced during the Bronze Age Steppe migration.",
                "image": "",
                "articles": [
                    {"title": "Newgrange tomb DNA reveals incest and a lost European lineage", "source": "Science", "url": "https://science.org"},
                    {"title": "Ireland's Neolithic elites practised deliberate sibling marriage, genome study finds", "source": "Nature", "url": "https://nature.com"},
                ]
            },
        ],
        "football": [
            {
                "headline": "Arsenal beat Manchester City 2-1 at the Etihad to go top of Premier League on goal difference with 4 games remaining",
                "score": 9, "timestamp": "yesterday",
                "summary": "Arsenal claimed a crucial victory at the Etihad Stadium, with Bukayo Saka scoring an 87th-minute winner after Martin Odegaard's opener was cancelled out by Erling Haaland's equaliser. The result puts Arsenal level on points with City at the top of the Premier League table but ahead on goal difference with four matches remaining. It is Arsenal's first win at the Etihad in 9 attempts across all competitions.",
                "image": "",
                "articles": [
                    {"title": "Saka 87th-minute winner sends Arsenal top as City suffer title blow", "source": "The Guardian", "url": "https://theguardian.com/football"},
                    {"title": "Manchester City 1-2 Arsenal: Haaland equaliser not enough as Saka clinches it", "source": "BBC Sport", "url": "https://bbc.com/sport"},
                    {"title": "Arsenal go top on goal difference with four games to play", "source": "Sky Sports", "url": "https://skysports.com"},
                ]
            },
            {
                "headline": "Real Madrid eliminate Bayern Munich 3-2 on aggregate to reach Champions League final, Vinicius Jr scores twice in second leg",
                "score": 8, "timestamp": "yesterday",
                "summary": "Real Madrid reached their fifth Champions League final in ten years after Vinicius Jr scored twice in a 2-1 second-leg win over Bayern Munich at the Bernabeu. Harry Kane pulled one back for Bayern in the 78th minute to set up a tense finish but Madrid held on. They will face Inter Milan in the final in Istanbul on June 1st. It is the 18th Champions League final in Real Madrid's history.",
                "image": "",
                "articles": [
                    {"title": "Vinicius double sends Real Madrid to Istanbul final", "source": "Marca", "url": "https://marca.com"},
                    {"title": "Real Madrid 2-1 Bayern Munich (3-2 agg): player ratings", "source": "The Guardian", "url": "https://theguardian.com/football"},
                    {"title": "Real Madrid vs Inter Milan: Champions League final preview", "source": "BBC Sport", "url": "https://bbc.com/sport"},
                ]
            },
            {
                "headline": "Lamine Yamal becomes youngest player in La Liga history to reach 20 assists in a season at age 17",
                "score": 7, "timestamp": "8 hrs ago",
                "summary": "Barcelona's Lamine Yamal set up two goals in Saturday's 3-0 win over Getafe to take his La Liga assist tally to 20 for the season, breaking the record previously held by Lionel Messi set in 2009. The 17-year-old has also scored 16 goals this campaign. Barcelona manager Hansi Flick called it a historic achievement for the youngest player to ever represent Spain at a major tournament.",
                "image": "",
                "articles": [
                    {"title": "Yamal breaks Messi's La Liga assist record at 17", "source": "Marca", "url": "https://marca.com"},
                    {"title": "Barcelona 3-0 Getafe: Yamal two assists as Barca cruise", "source": "ESPN", "url": "https://espn.com"},
                ]
            },
            {
                "headline": "Nottingham Forest relegated from Premier League after 1-0 defeat to Everton leaves them 18th with one match left",
                "score": 7, "timestamp": "3 hrs ago",
                "summary": "Nottingham Forest were relegated from the Premier League after a 1-0 home defeat to Everton. Dominic Calvert-Lewin's 54th-minute header proved decisive. Forest remain 18th with 31 points and cannot mathematically escape the bottom three. It ends a three-year stay in the top flight for the club.",
                "image": "",
                "articles": [
                    {"title": "Nottingham Forest relegated as Everton win at the City Ground", "source": "Sky Sports", "url": "https://skysports.com"},
                    {"title": "Calvert-Lewin header condemns Forest to Championship", "source": "BBC Sport", "url": "https://bbc.com/sport"},
                ]
            },
            {
                "headline": "PSG win Ligue 1 title for 12th time despite drawing 1-1 with Rennes; Monaco's win elsewhere not enough",
                "score": 6, "timestamp": "2 hrs ago",
                "summary": "Paris Saint-Germain were confirmed as Ligue 1 champions for the twelfth time after drawing 1-1 at Rennes while Monaco beat Lyon 2-0 but could not close the four-point gap. It is PSG's first title without Kylian Mbappe, who left for Real Madrid last summer. Manager Luis Enrique praised the squad's resilience following a difficult transitional season.",
                "image": "",
                "articles": [
                    {"title": "PSG crowned Ligue 1 champions for record 12th time", "source": "L'Equipe", "url": "https://lequipe.fr"},
                    {"title": "First title without Mbappe caps Luis Enrique's debut season in Paris", "source": "The Guardian", "url": "https://theguardian.com/football"},
                ]
            },
        ]
    }

    world_topics = {
        "today": [
            {"headline": "Ukraine-Russia peace talks collapse in Istanbul", "why": "Negotiations broke down after Russia refused to withdraw from occupied territories, raising fears of further escalation.", "signal": "both sources"},
            {"headline": "US tariffs on Chinese goods raised to 145%", "why": "The White House announced a new round of tariff increases, sending global markets into sharp decline.", "signal": "both sources"},
            {"headline": "Turkey earthquake rescue operations ongoing", "why": "Hundreds confirmed dead after a 7.8-magnitude quake near the Syrian border with thousands still missing.", "signal": "reddit only"},
            {"headline": "North Korea missile launches condemned by G7", "why": "Three ballistic missiles fired into the Sea of Japan triggered an emergency UN Security Council session.", "signal": "trends only"},
            {"headline": "OpenAI releases GPT-5 to general public", "why": "The new model scores above human level on all major benchmarks, sparking widespread debate about AI timelines.", "signal": "both sources"},
        ],
        "week": [
            {"headline": "Global ceasefire negotiations in multiple conflicts", "why": "Simultaneous diplomatic pushes in Ukraine, Gaza and Sudan dominated international headlines all week.", "signal": "trending for 6 days"},
            {"headline": "US Federal Reserve holds rates amid inflation data", "why": "Markets were volatile as the Fed signalled no cuts before Q3, disappointing investors expecting relief.", "signal": "trending for 5 days"},
            {"headline": "Apple WWDC announcements", "why": "Apple revealed sweeping AI integration across all platforms, with on-device models replacing Siri.", "signal": "trending for 4 days"},
            {"headline": "Champions League semi-finals", "why": "High-drama second legs across all four ties kept football dominating social media throughout the week.", "signal": "trending for 7 days"},
            {"headline": "Measles outbreak spreads across US states", "why": "CDC declared a public health emergency as cases reached a 30-year high following vaccine hesitancy campaigns.", "signal": "trending for 3 days"},
        ],
        "month": [
            {"headline": "US-China trade war escalation", "why": "The tariff spiral dominated economic coverage for the entire month as recession fears grew globally.", "signal": "trending for 28 days"},
            {"headline": "Gaza ceasefire negotiations", "why": "Multiple rounds of talks mediated by Qatar and Egypt kept the conflict at the top of global news agendas.", "signal": "trending for 25 days"},
            {"headline": "AI regulation bills advancing in US and EU", "why": "Landmark legislation moving through both US Congress and the European Parliament attracted sustained attention.", "signal": "trending for 18 days"},
            {"headline": "Premier League title race", "why": "The tightest title race in a decade between Arsenal and Manchester City ran across every week of the month.", "signal": "trending for 30 days"},
            {"headline": "Climate records shattered globally", "why": "April 2026 became the hottest April ever recorded, extending a 13-month streak of record-breaking temperatures.", "signal": "trending for 22 days"},
        ]
    }

    developing_situations = [
        {
            "topic": "Ukraine war",
            "type": "pinned",
            "update": "Russia's overnight missile barrage was the largest of the conflict, striking 7 cities with 180 drones and 40 cruise missiles. 23 civilians confirmed dead. Peace talks in Istanbul suspended without agreement earlier the same day.",
            "has_update": True,
            "articles": [
                {"title": "Russia launches record missile barrage at Ukraine", "source": "The Guardian", "url": "https://theguardian.com"},
                {"title": "Istanbul talks collapse as Russia rejects withdrawal terms", "source": "Reuters", "url": "https://reuters.com"},
                {"title": "Ukraine air defences intercept 130 of 220 projectiles", "source": "BBC", "url": "https://bbc.com"},
            ]
        },
        {
            "topic": "Gaza ceasefire talks",
            "type": "pinned",
            "update": "Qatar-mediated negotiations continue in Doha with both sides represented. A new framework proposal involving a 60-day pause and hostage release is reportedly on the table but Hamas has not yet formally responded.",
            "has_update": True,
            "articles": [
                {"title": "Qatar hosts new round of Gaza ceasefire talks", "source": "Al Jazeera", "url": "https://aljazeera.com"},
                {"title": "60-day pause proposal outline published", "source": "Haaretz", "url": "https://haaretz.com"},
            ]
        },
        {
            "topic": "US-China trade war",
            "type": "auto",
            "update": "No significant updates today beyond market reactions to the new 145% tariff announcement. Beijing has scheduled a press conference for Monday.",
            "has_update": False,
            "articles": []
        },
    ]

    yesterday_data = {
        "breaking": [
            {"headline": "Israeli airstrike on Rafah kills 34, Palestinian health ministry reports", "score": 8, "timestamp": "yesterday"},
        ],
        "australia": [
            {"headline": "RBA holds cash rate at 4.1% for fifth consecutive meeting despite falling inflation", "score": 7, "timestamp": "yesterday"},
        ],
        "archaeology": [],
        "football": [
            {"headline": "Manchester United sack Ruben Amorim after 5 consecutive Premier League defeats, club 14th", "score": 8, "timestamp": "yesterday"},
        ],
    }

    return all_data, yesterday_data, world_topics, developing_situations


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import shutil
    if MOCK_MODE:
        print("MOCK_MODE enabled — skipping all API calls.")
        all_data, yesterday_data, world_topics, developing_situations = mock_data()
        Path("dist").mkdir(exist_ok=True)
        favicon_files = ["favicon.ico", "favicon.svg", "favicon-32.png", "favicon-16.png", "apple-touch-icon.png", "logo.svg"]
        for fname in favicon_files:
            src = Path(fname)
            if src.exists():
                shutil.copy(src, Path("dist") / fname)
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations))
        Path("dist/.deploy_needed").touch()
        print("Done. dist/index.html written.")
        return

    memory = load_memory()
    pinned = load_pinned()
    health = load_health()

    if RUN_MODE == "deploy_only":
        print("Deploy-only run — rebuilding HTML from cache, zero API calls.")
        errors = []
        all_data = {cat: get_cached_category(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        yesterday_data = {cat: get_previous_stories(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        world_topics = memory.get("world_topics_cache", {"today": [], "week": [], "month": []})
        developing_situations = process_developing_situations(pinned, [], [])
        health = log_run(health, "deploy_only", errors)
        save_health(health)
        Path("dist").mkdir(exist_ok=True)
        favicon_files = ["favicon.ico", "favicon.svg", "favicon-32.png", "favicon-16.png", "apple-touch-icon.png", "logo.svg"]
        for fname in favicon_files:
            src = Path(fname)
            if src.exists():
                shutil.copy(src, Path("dist") / fname)
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
        Path("dist/.deploy_needed").touch()
        print("Done. dist/index.html written from cache.")
        return

    if RUN_MODE == "breaking_only":
        print("Breaking-only run...")
        errors = []
        content_changed = False
        gdelt_breaking, gdelt_err, memory = fetch_gdelt_articles("war attack disaster killed", timespan="1h", max_records=25, memory=memory)
        if not isinstance(memory, dict):
            print(f"ERROR: memory corrupted after GDELT call (got {type(memory)}), reloading from disk")
            memory = load_memory()
        if gdelt_err:
            print(f"GDELT: {gdelt_err}")
            if "skipped" not in gdelt_err:
                errors.append(gdelt_err)
        guardian_breaking = fetch_guardian("world war attack disaster crisis killed invasion", page_size=15)
        reuters_rss = fetch_rss("https://feeds.reuters.com/reuters/topNews", "Reuters")
        ap_rss = fetch_rss("https://rsshub.app/apnews/topics/apf-topnews", "AP News")
        bbc_rss = fetch_rss("https://feeds.bbci.co.uk/news/rss.xml", "BBC News")
        aljazeera_rss = fetch_rss("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera")
        all_breaking = gdelt_breaking + guardian_breaking + reuters_rss + ap_rss + bbc_rss + aljazeera_rss

        if category_has_changed(memory, "breaking", all_breaking):
            new_breaking, memory = process_breaking_news([], all_breaking, memory)
            if new_breaking:
                breaking = new_breaking
                content_changed = True
            else:
                print("Breaking news: articles changed but nothing passed the bar, keeping existing")
                breaking = get_cached_category(memory, "breaking")
            memory = save_article_hash(memory, "breaking", all_breaking)
        else:
            print("Breaking news: no new articles since last check, skipping Sonnet call")
            breaking = get_cached_category(memory, "breaking")

        memory = save_today_stories(memory, "breaking", breaking)

        all_data = {
            "breaking": breaking,
            "australia": get_cached_category(memory, "australia"),
            "archaeology": get_cached_category(memory, "archaeology"),
            "football": get_cached_category(memory, "football")
        }
        # World topics: use cached value — only refresh in full runs
        world_topics = memory.get("world_topics_cache", {"today": [], "week": [], "month": []})
        yesterday_data = {cat: get_previous_stories(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        # Developing situations: only process if topics are being tracked; skip Haiku call otherwise
        developing_situations = process_developing_situations(pinned, [], all_breaking) if pinned else []

        save_memory(memory)
        health = log_run(health, "breaking_only", errors)
        save_health(health)

        Path("dist").mkdir(exist_ok=True)
        favicon_files = ["favicon.ico", "favicon.svg", "favicon-32.png", "favicon-16.png", "apple-touch-icon.png", "logo.svg"]
        for fname in favicon_files:
            src = Path(fname)
            if src.exists():
                shutil.copy(src, Path("dist") / fname)
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
        if content_changed:
            Path("dist/.deploy_needed").touch()
            print("Done. dist/index.html written — deploy triggered.")
        else:
            print("Done. dist/index.html written — no new content, deploy skipped.")
        return

    elif RUN_MODE == "category" and RUN_CATEGORY:
        print(f"Category-only run: {RUN_CATEGORY}...")
        errors = []
        content_changed = False

        if RUN_CATEGORY == "football":
            guardian_football = fetch_guardian("premier league OR la liga OR serie a OR bundesliga OR ligue 1 OR champions league", page_size=15, section="football")
            marca_rss = fetch_rss("https://e00-marca.uecdn.es/rss/futbol/primera-division.xml", "Marca")
            kicker_rss = fetch_rss("https://newsfeed.kicker.de/news/fussball", "Kicker")
            lequipe_rss = fetch_rss("https://www.lequipe.fr/rss/actu_rss_Football.xml", "L'Equipe")
            gazzetta_rss = fetch_rss("https://www.gazzetta.it/rss/home.xml", "Gazzetta dello Sport")
            sky_rss = fetch_rss("https://www.skysports.com/rss/12040", "Sky Sports")
            espn_rss = fetch_rss("https://www.espn.com/espn/rss/soccer/news", "ESPN FC")
            bbc_football_rss = fetch_rss("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport")
            football_italia_rss = fetch_rss("https://www.football-italia.net/rss.xml", "Football Italia")
            bundesliga_rss = fetch_rss("https://www.bundesliga.com/api/rss/news/en", "Bundesliga")
            uefa_rss = fetch_rss("https://www.uefa.com/rss.xml", "UEFA")
            goal_rss = fetch_rss("https://www.goal.com/feeds/en/news", "Goal.com")
            articles = (guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss +
                        espn_rss + bbc_football_rss + football_italia_rss + bundesliga_rss + uefa_rss + goal_rss)[:40]
            if category_has_changed(memory, "football", articles):
                result, memory = process_football(articles, memory)
                memory = save_article_hash(memory, "football", articles)
                content_changed = True
            else:
                print("Football: no new articles, skipping")
                result = get_cached_category(memory, "football")
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": get_cached_category(memory, "australia"),
                "archaeology": get_cached_category(memory, "archaeology"),
                "football": result
            }

        elif RUN_CATEGORY == "australia":
            abc_rss = fetch_rss("https://www.abc.net.au/news/feed/51120/rss.xml", "ABC News")
            smh_rss = fetch_rss("https://www.smh.com.au/rss/feed.xml", "SMH")
            age_rss = fetch_rss("https://www.theage.com.au/rss/feed.xml", "The Age")
            newsdata_aus = fetch_newsdata("australia parliament senate election albanese budget policy", country="au")
            articles = abc_rss + smh_rss + age_rss + newsdata_aus
            if category_has_changed(memory, "australia", articles):
                result, memory = process_australia(abc_rss + smh_rss + age_rss, newsdata_aus, memory)
                memory = save_article_hash(memory, "australia", articles)
                content_changed = True
            else:
                print("Australia: no new articles, skipping")
                result = get_cached_category(memory, "australia")
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": result,
                "archaeology": get_cached_category(memory, "archaeology"),
                "football": get_cached_category(memory, "football")
            }

        elif RUN_CATEGORY == "archaeology":
            nature_rss = fetch_rss("https://www.nature.com/nature.rss", "Nature")
            newscientist_rss = fetch_rss("https://www.newscientist.com/subject/humans/feed/", "New Scientist")
            science_rss = fetch_rss("https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science", "Science")
            newsdata_arch = fetch_newsdata("paleoanthropology fossil hominin ancient DNA homo sapiens neanderthal discovery")
            physorg_rss = fetch_rss("https://phys.org/rss-feed/biology-news/evolution/", "PhysOrg")
            eurekalert_rss = fetch_rss("https://www.eurekalert.org/rss/all.xml", "EurekAlert")
            sciencedaily_rss = fetch_rss("https://www.sciencedaily.com/rss/fossils_ruins/human_evolution.xml", "ScienceDaily")
            conversation_rss = fetch_rss("https://theconversation.com/us/science/rss", "The Conversation")
            articles = (nature_rss + newscientist_rss + science_rss + newsdata_arch +
                        physorg_rss + eurekalert_rss + sciencedaily_rss + conversation_rss)
            if category_has_changed(memory, "archaeology", articles):
                result, memory = process_archaeology(articles, memory)
                memory = save_article_hash(memory, "archaeology", articles)
                content_changed = True
            else:
                print("Archaeology: no new articles, skipping")
                result = get_cached_category(memory, "archaeology")
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": get_cached_category(memory, "australia"),
                "archaeology": result,
                "football": get_cached_category(memory, "football")
            }

        elif RUN_CATEGORY == "world_topics":
            world_topics, memory = process_world_topics(memory)
            content_changed = True
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": get_cached_category(memory, "australia"),
                "archaeology": get_cached_category(memory, "archaeology"),
                "football": get_cached_category(memory, "football")
            }
        else:
            print(f"Unknown category: {RUN_CATEGORY}, aborting.")
            return

        world_topics = memory.get("world_topics_cache", {"today": [], "week": [], "month": []}) if RUN_CATEGORY != "world_topics" else world_topics
        yesterday_data = {cat: get_previous_stories(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        developing_situations = process_developing_situations(pinned, [], [])
        if RUN_CATEGORY in ("breaking", "australia", "archaeology", "football"):
            memory = save_today_stories(memory, RUN_CATEGORY, result)
        save_memory(memory)
        health = log_run(health, f"category:{RUN_CATEGORY}", errors)
        save_health(health)
        Path("dist").mkdir(exist_ok=True)
        favicon_files = ["favicon.ico", "favicon.svg", "favicon-32.png", "favicon-16.png", "apple-touch-icon.png", "logo.svg"]
        for fname in favicon_files:
            src = Path(fname)
            if src.exists():
                shutil.copy(src, Path("dist") / fname)
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
        if content_changed:
            Path("dist/.deploy_needed").touch()
            print(f"Done. Category-only run for {RUN_CATEGORY} complete — deploy triggered.")
        else:
            print(f"Done. Category-only run for {RUN_CATEGORY} complete — no new content, deploy skipped.")
        return

    # Full run continues below...
    errors = []
    content_changed = False

    print("Fetching world topics...")
    world_topics, memory = process_world_topics(memory)

    print("Fetching Breaking News...")
    gdelt_breaking, gdelt_err, memory = fetch_gdelt_articles("war killed attack invasion disaster explosion casualties", timespan="1h", max_records=25, memory=memory)
    if not isinstance(memory, dict):
        print(f"ERROR: memory corrupted after GDELT call (got {type(memory)}), reloading from disk")
        memory = load_memory()
    if gdelt_err:
        print(f"GDELT: {gdelt_err}")
        if "skipped" not in gdelt_err:
            errors.append(gdelt_err)
    guardian_breaking = fetch_guardian("world war attack disaster crisis killed invasion", page_size=15)
    reuters_rss = fetch_rss("https://feeds.reuters.com/reuters/topNews", "Reuters")
    ap_rss = fetch_rss("https://rsshub.app/apnews/topics/apf-topnews", "AP News")
    bbc_rss = fetch_rss("https://feeds.bbci.co.uk/news/rss.xml", "BBC News")
    aljazeera_rss = fetch_rss("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera")
    all_breaking = gdelt_breaking + guardian_breaking + reuters_rss + ap_rss + bbc_rss + aljazeera_rss
    new_breaking, memory = process_breaking_news([], all_breaking, memory)
    if new_breaking:
        breaking = new_breaking
        memory = save_article_hash(memory, "breaking", all_breaking)
    else:
        print("Breaking news: no new stories passed the bar, keeping existing")
        breaking = get_cached_category(memory, "breaking")

    time.sleep(60)
    print("Fetching Australia news...")
    abc_rss = fetch_rss("https://www.abc.net.au/news/feed/51120/rss.xml", "ABC News")
    smh_rss = fetch_rss("https://www.smh.com.au/rss/feed.xml", "SMH")
    age_rss = fetch_rss("https://www.theage.com.au/rss/feed.xml", "The Age")
    newsdata_aus = fetch_newsdata("australia parliament senate election albanese budget policy", country="au")
    australia, memory = process_australia(abc_rss + smh_rss + age_rss, newsdata_aus, memory)

    time.sleep(60)
    print("Fetching Archaeology news...")
    nature_rss = fetch_rss("https://www.nature.com/nature.rss", "Nature")
    newscientist_rss = fetch_rss("https://www.newscientist.com/subject/humans/feed/", "New Scientist")
    science_rss = fetch_rss("https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science", "Science")
    newsdata_arch = fetch_newsdata("paleoanthropology fossil hominin ancient DNA homo sapiens neanderthal discovery")
    physorg_rss = fetch_rss("https://phys.org/rss-feed/biology-news/evolution/", "PhysOrg")
    eurekalert_rss = fetch_rss("https://www.eurekalert.org/rss/all.xml", "EurekAlert")
    sciencedaily_rss = fetch_rss("https://www.sciencedaily.com/rss/fossils_ruins/human_evolution.xml", "ScienceDaily")
    conversation_rss = fetch_rss("https://theconversation.com/us/science/rss", "The Conversation")
    archaeology, memory = process_archaeology(
        nature_rss + newscientist_rss + science_rss + newsdata_arch +
        physorg_rss + eurekalert_rss + sciencedaily_rss + conversation_rss, memory)

    time.sleep(60)
    print("Fetching Football news...")
    guardian_football = fetch_guardian(
        "premier league OR la liga OR serie a OR bundesliga OR ligue 1 OR champions league",
        page_size=15, section="football"
    )
    marca_rss = fetch_rss("https://e00-marca.uecdn.es/rss/futbol/primera-division.xml", "Marca")
    kicker_rss = fetch_rss("https://newsfeed.kicker.de/news/fussball", "Kicker")
    lequipe_rss = fetch_rss("https://www.lequipe.fr/rss/actu_rss_Football.xml", "L'Equipe")
    gazzetta_rss = fetch_rss("https://www.gazzetta.it/rss/home.xml", "Gazzetta dello Sport")
    sky_rss = fetch_rss("https://www.skysports.com/rss/12040", "Sky Sports")
    espn_rss = fetch_rss("https://www.espn.com/espn/rss/soccer/news", "ESPN FC")
    bbc_football_rss = fetch_rss("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport")
    football_italia_rss = fetch_rss("https://www.football-italia.net/rss.xml", "Football Italia")
    bundesliga_rss = fetch_rss("https://www.bundesliga.com/api/rss/news/en", "Bundesliga")
    uefa_rss = fetch_rss("https://www.uefa.com/rss.xml", "UEFA")
    goal_rss = fetch_rss("https://www.goal.com/feeds/en/news", "Goal.com")
    football, memory = process_football(
        (guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss +
        espn_rss + bbc_football_rss + football_italia_rss + bundesliga_rss + uefa_rss + goal_rss)[:40],
        memory)

    all_data = {
        "breaking": breaking,
        "australia": australia,
        "archaeology": archaeology,
        "football": football
    }

    print("Processing developing situations...")
    all_fetched = (all_breaking + abc_rss + smh_rss + age_rss +
                   newsdata_aus + nature_rss + newscientist_rss + science_rss + newsdata_arch +
                   physorg_rss + eurekalert_rss + sciencedaily_rss + conversation_rss +
                   guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss +
                   espn_rss + bbc_football_rss + football_italia_rss + bundesliga_rss + uefa_rss + goal_rss)
    auto_detected = detect_developing_situations(memory, all_data)
    developing_situations = process_developing_situations(pinned, auto_detected, all_fetched)

    yesterday_data = {
        "breaking": get_previous_stories(memory, "breaking"),
        "australia": get_previous_stories(memory, "australia"),
        "archaeology": get_previous_stories(memory, "archaeology"),
        "football": get_previous_stories(memory, "football")
    }

    for cat in ["breaking", "australia", "archaeology", "football"]:
        memory = save_today_stories(memory, cat, all_data[cat])
    content_changed = any(all_data[cat] for cat in ["breaking", "australia", "archaeology", "football"])
    save_memory(memory)
    health = log_run(health, "full", errors)
    save_health(health)

    Path("dist").mkdir(exist_ok=True)
    favicon_files = ["favicon.ico", "favicon.svg", "favicon-32.png", "favicon-16.png", "apple-touch-icon.png", "logo.svg"]
    for fname in favicon_files:
        src = Path(fname)
        if src.exists():
            shutil.copy(src, Path("dist") / fname)
    with open("dist/index.html", "w", encoding="utf-8") as f:
        f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
    if content_changed:
        Path("dist/.deploy_needed").touch()
        print("Done. dist/index.html written — deploy triggered.")
    else:
        print("Done. dist/index.html written — no new content, deploy skipped.")

if __name__ == "__main__":
    main()
