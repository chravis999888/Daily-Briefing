import os
import re
import json
import time
import requests
import feedparser
import anthropic
from datetime import datetime, timezone, timedelta
from pathlib import Path
from gdeltdoc import GdeltDoc, Filters

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NEWSDATA_KEY = os.environ["NEWSDATA_API_KEY"]
GUARDIAN_KEY = os.environ["GUARDIAN_API_KEY"]

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
gd = GdeltDoc()

AEST = timezone(timedelta(hours=10))
MEMORY_FILE = "memory.json"
PINNED_FILE = "pinned.txt"

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

def get_yesterday_stories(memory, category):
    yesterday = (datetime.now(AEST) - timedelta(days=1)).strftime("%Y-%m-%d")
    return memory.get("stories", {}).get(yesterday, {}).get(category, [])

def save_today_stories(memory, category, stories):
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    if "stories" not in memory:
        memory["stories"] = {}
    if today not in memory["stories"]:
        memory["stories"][today] = {}
    memory["stories"][today][category] = [
        {"headline": s["headline"], "timestamp": s.get("timestamp",""), "score": s.get("score", 5)}
        for s in stories
    ]
    cutoff = (datetime.now(AEST) - timedelta(days=3)).strftime("%Y-%m-%d")
    memory["stories"] = {k: v for k, v in memory["stories"].items() if k >= cutoff}
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
        from email.utils import parsedate_to_datetime
        for parser in [
            lambda s: parsedate_to_datetime(s),
            lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z"),
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

def call_haiku(prompt, max_tokens=500):
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

def call_sonnet(prompt, max_tokens=1000, retries=3):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            return msg.content[0].text
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"Sonnet rate limit, waiting {wait}s (attempt {attempt+1}/{retries})...")
            time.sleep(wait)
        except Exception as e:
            print(f"Sonnet error: {e}")
            break
    print("Falling back to Haiku...")
    return call_haiku(prompt, max_tokens)

def call_sonnet_with_search(prompt, max_tokens=1500, retries=3):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": prompt}]
            )
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

def get_ai_summary(headline, content="", context=""):
    prompt = f"""In 3-4 sentences, explain this news story clearly and factually.
Headline: "{headline}"
{f'Article content: {content[:1200]}' if content else ''}
{f'Additional context: {context}' if context else ''}
Cover what happened, why it matters, and any important background or broader significance. Plain English, no fluff. Start directly — no markdown, no # symbols."""
    text = call_haiku(prompt, 500)
    return re.sub(r'^#+\s*\w*\s*', '', text).strip()

# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_gdelt_articles(query, timespan="24h", max_records=25):
    try:
        f = Filters(keyword=query, timespan=timespan, num_records=max_records)
        df = gd.article_search(f)
        articles = []
        for _, row in df.iterrows():
            articles.append({
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "source": row.get("domain", ""),
                "time": str(row.get("seendate", "")),
                "content": ""
            })
        return articles
    except Exception as e:
        print(f"GDELT fetch error: {e}")
        return []

def fetch_gdelt_top_stories(timespan):
    try:
        f = Filters(timespan=timespan, num_records=50)
        df = gd.article_search(f)
        if df is None or df.empty:
            return []
        from collections import Counter
        domain_counts = Counter(df["domain"].tolist())
        title_groups = {}
        for _, row in df.iterrows():
            title = row.get("title","")
            if not title:
                continue
            key = title[:40].lower()
            if key not in title_groups:
                title_groups[key] = {"title": title, "url": row.get("url",""), "source": row.get("domain",""), "time": str(row.get("seendate","")), "count": 0}
            title_groups[key]["count"] += 1
        top = sorted(title_groups.values(), key=lambda x: x["count"], reverse=True)[:15]
        return top
    except Exception as e:
        print(f"GDELT top stories error: {e}")
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

def format_articles_for_prompt(articles, limit=25):
    parts = []
    for a in articles[:limit]:
        content = a.get("content", "").strip()
        rel = relative_time(a.get("time", ""))
        time_str = f"PUBLISHED: {rel}\n" if rel else ""
        if content:
            parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}CONTENT: {content[:600]}\nURL: {a['url']}")
        else:
            parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}URL: {a['url']}")
    return "\n---\n".join(parts)

# ── World Topics ──────────────────────────────────────────────────────────────

def process_world_topics(today_articles, week_articles, month_articles):
    results = {}
    for label, articles in [("today", today_articles), ("week", week_articles), ("month", month_articles)]:
        if not articles:
            results[label] = []
            continue
        formatted = "\n---\n".join([f"TITLE: {a['title']}\nSOURCE: {a['source']}\nCOVERAGE COUNT: {a.get('count',1)}\nURL: {a['url']}" for a in articles[:15]])
        prompt = f"""You are a global news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are the most globally covered stories based on GDELT data for the {label}:
{formatted}

Select the top 5 most significant stories the world is talking about. For each:
- Write a plain English headline stating what the story is actually about
- Write a single sentence explaining why the world is paying attention
- Note approximately how many outlets are covering it

Return ONLY a JSON array:
[{{"headline":"...","why":"...","coverage":"hundreds of outlets","url":"...","source":"..."}}]
Raw JSON only, no markdown."""
        text = call_haiku(prompt, 800)
        try:
            stories = json.loads(text.replace("```json","").replace("```","").strip())
            results[label] = stories
        except:
            results[label] = []
    return results

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

    text = call_haiku(prompt, 1000)
    try:
        updates = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    for u in updates:
        topic = u.get("topic","")
        situation_type = next((t["type"] for t in all_topics if t["topic"].lower() == topic.lower()), "auto")
        situations.append({
            "topic": topic,
            "type": situation_type,
            "update": u.get("update",""),
            "has_update": u.get("has_update", False),
            "articles": u.get("articles", [])
        })
    return situations

# ── Category Processors ───────────────────────────────────────────────────────

def process_breaking_news(gdelt_articles, guardian_articles):
    guardian_urls = {a["url"] for a in guardian_articles}
    all_articles = guardian_articles + [a for a in gdelt_articles if a["url"] not in guardian_urls]
    if not all_articles:
        return []

    formatted = format_articles_for_prompt(all_articles, 25)
    prompt = f"""You are a world news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles:
{formatted}

Select ONLY stories that are historic in scale — active major wars significantly escalating with large casualties, world leader deaths, terrorist attacks killing hundreds+, catastrophic natural disasters with mass casualties, nuclear threats. DO NOT include diplomatic talks, peace negotiations, ceasefire discussions, court cases, political scandals, or warnings. If nothing meets this bar return [].

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

    text = call_sonnet(prompt, 1200)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        orig = next((a for a in all_articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title","") or story.get("headline",""), "source": story.get("source",""), "url": story.get("url","")}]
        context = ""
        if story.get("deeper_search") or story.get("so_what"):
            search_q = story.get("so_what") or story["headline"]
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
        ts = relative_time(orig.get("time","")) or story.get("timestamp","")
        summary = get_ai_summary(story["headline"], orig.get("content",""), context)
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "image": orig.get("image",""),
            "articles": articles_list
        })
    return results

def process_australia(rss_articles, newsdata_articles):
    all_articles = rss_articles + newsdata_articles
    if not all_articles:
        return []

    formatted = format_articles_for_prompt(all_articles, 25)
    prompt = f"""You are an Australian political news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles:
{formatted}

Select ONLY stories about Australian domestic politics: federal or state parliament votes, bills passed or failed, budget decisions, elections, party leadership changes, High Court rulings, major national policy changes. NO international news, accidents, crime, sport, weather. Aim for 2-4 stories. If nothing meets the bar return [].

For each story:
- Write a specific factual headline
- Assign importance score 1-10
- Estimate timestamp
- Identify a "so_what" broader political context

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","score":7,"timestamp":"...","so_what":"...","url":"...","source":""}}]
Raw JSON only, no markdown."""

    text = call_sonnet(prompt, 1000)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        orig = next((a for a in all_articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source",""), "url": story.get("url","")}]
        context = story.get("so_what","")
        if context:
            search_prompt = f"""Search for context on: "{context}"
Return ONLY JSON array of up to 3 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_sonnet_with_search(search_prompt, 600)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                articles_list = articles_list + extra
            except:
                pass
        ts = relative_time(orig.get("time","")) or story.get("timestamp","")
        summary = get_ai_summary(story["headline"], orig.get("content",""), context)
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "image": orig.get("image",""),
            "articles": articles_list
        })
    return results

def process_archaeology(articles):
    if not articles:
        return []

    formatted = format_articles_for_prompt(articles, 20)
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

    text = call_sonnet(prompt, 800)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        orig = next((a for a in articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source",""), "url": story.get("url","")}]
        context = story.get("so_what","")
        ts = relative_time(orig.get("time","")) or story.get("timestamp","")
        summary = get_ai_summary(story["headline"], orig.get("content",""), context)
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "image": orig.get("image",""),
            "articles": articles_list
        })
    return results

def process_football(articles):
    if not articles:
        return []

    formatted = format_articles_for_prompt(articles, 30)
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
[{{"headline":"...","score":7,"timestamp":"...","so_what":"...","url":"...","source":""}}]
Raw JSON only, no markdown."""

    text = call_sonnet(prompt, 1200)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        orig = next((a for a in articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source","The Guardian"), "url": story.get("url","")}]
        context = story.get("so_what","")
        if context:
            search_prompt = f"""Search for context on: "{context}"
Return ONLY JSON array of up to 3 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_sonnet_with_search(search_prompt, 600)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                articles_list = articles_list + extra
                context = story["so_what"]
            except:
                pass
        ts = relative_time(orig.get("time","")) or story.get("timestamp","")
        summary = get_ai_summary(story["headline"], orig.get("content",""), context)
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "image": orig.get("image",""),
            "articles": articles_list
        })
    return results

# ── HTML Builder ──────────────────────────────────────────────────────────────

def build_html(all_data, yesterday_data, world_topics, developing_situations):
    date_str = datetime.now(AEST).strftime("%A %d %B %Y").upper()
    updated_str = datetime.now(AEST).strftime("%I:%M %p AEST").lstrip("0")
    build_ts = int(datetime.now(timezone.utc).timestamp())

    def render_story(story, i, ac, is_top=False, is_yesterday=False):
        num = f"0{i+1}" if i+1 < 10 else str(i+1)
        arts = story.get("articles", [])
        summary = story.get("summary","").replace("<","&lt;").replace(">","&gt;")
        score = story.get("score", 5)
        image = story.get("image","")
        headline_escaped = story["headline"].replace("'", "\\'").replace('"', '&quot;')
        # Suggest core topic by stripping match details to get the underlying story
        suggested = story["headline"][:60].rstrip(".,")

        art_html = "".join([
            f'<a href="{a.get("url","#")}" target="_blank" rel="noreferrer noopener" '
            f'style="display:flex;align-items:center;justify-content:space-between;gap:12px;'
            f'padding:9px 14px;border-radius:8px;background:#0d0d0c;text-decoration:none;'
            f'margin-bottom:4px;border:1px solid rgba(255,255,255,0.05);">'
            f'<span style="font-size:13px;color:#c8c4bc;line-height:1.4;flex:1;font-weight:300;">'
            f'{a.get("title","").replace("<","&lt;").replace(">","&gt;")}</span>'
            f'<span style="font-size:11px;color:#444440;white-space:nowrap;flex-shrink:0;margin-left:8px;">'
            f'{a.get("source","")}</span></a>'
            for a in arts if a.get("url","").startswith("http")
        ])

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

        image_html = ""
        if is_top and image and image.startswith("http"):
            image_html = f'<div style="width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:8px;margin-bottom:12px;"><img src="{image}" style="width:100%;height:100%;object-fit:cover;" loading="lazy" onerror="this.parentElement.style.display=\'none\'"/></div>'

        star_btn = "" if is_yesterday else f'<button class="star-btn" onclick="showStarPopup(\'{headline_escaped}\',\'{suggested.replace(chr(39), chr(92)+chr(39))}\');event.stopPropagation();" title="Track this story" style="background:none;border:none;cursor:pointer;padding:4px;color:#333330;font-size:14px;flex-shrink:0;line-height:1;margin-left:4px;transition:color 0.15s;" onmouseover="this.style.color=\'#c9a96e\'" onmouseout="this.style.color=\'#333330\'">&#9734;</button>'

        return f'''<div class="story" style="border-radius:10px;background:{card_bg};border:{card_border};margin-bottom:8px;opacity:{opacity};">
  <div class="story-header" style="display:flex;align-items:flex-start;gap:14px;padding:16px 18px;cursor:pointer;border-radius:10px;">
    <span style="font-size:11px;color:#2a2a28;min-width:20px;margin-top:3px;flex-shrink:0;">{num}</span>
    <div style="flex:1;min-width:0;">
      <div style="font-size:{headline_size};font-weight:400;line-height:1.45;color:#f0ece4;margin-bottom:8px;letter-spacing:-0.01em;display:flex;align-items:flex-start;">{score_dot}<span>{story["headline"].replace("<","&lt;").replace(">","&gt;")}</span></div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">{meta_html}</div>
    </div>
    {star_btn}
    <div class="chev" style="font-size:10px;color:#2a2a28;margin-top:4px;flex-shrink:0;transition:transform 0.2s;margin-left:4px;">&#9660;</div>
  </div>
  <div class="story-body" style="display:none;padding:0 18px 18px 52px;">
    {image_html}
    <div style="font-size:13px;line-height:1.75;color:#8a8680;background:#111110;border-left:2px solid {ac};padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:12px;">{summary}</div>
    {art_html}
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

        return f'''<div style="margin-bottom:3.5rem;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:10px;">
      <div style="width:3px;height:24px;border-radius:2px;background:#7b68c8;flex-shrink:0;"></div>
      <div style="font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:500;letter-spacing:-0.01em;">What the world is talking about</div>
    </div>
    <div style="display:flex;gap:4px;">{tabs_html}</div>
  </div>
  {panels_html}
</div>'''

    # Developing situations section
    def render_developing():
        if not developing_situations:
            return ""
        ac = ACCENTS["developing"]
        items_html = ""
        for s in developing_situations:
            badge = f'<span style="font-size:10px;padding:2px 8px;border-radius:999px;background:{"rgba(42,122,110,0.2)" if s["type"]=="auto" else "rgba(123,104,200,0.2)"};color:{"#4aaa99" if s["type"]=="auto" else "#b8b0e8"};margin-left:8px;vertical-align:middle;">{"auto" if s["type"]=="auto" else "pinned"}</span>'
            arts = s.get("articles",[])
            art_html = "".join([
                f'<a href="{a.get("url","#")}" target="_blank" rel="noreferrer noopener" style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 12px;border-radius:8px;background:#0d0d0c;text-decoration:none;margin-bottom:3px;border:1px solid rgba(255,255,255,0.04);">'
                f'<span style="font-size:13px;color:#c8c4bc;line-height:1.4;flex:1;font-weight:300;">{a.get("title","").replace("<","&lt;").replace(">","&gt;")}</span>'
                f'<span style="font-size:11px;color:#444440;white-space:nowrap;flex-shrink:0;margin-left:8px;">{a.get("source","")}</span></a>'
                for a in arts if a.get("url","").startswith("http")
            ]) if s.get("has_update") else ""

            update_style = "font-size:13px;line-height:1.7;color:#8a8680;" if s.get("has_update") else "font-size:13px;color:#333330;font-style:italic;"
        sit_id = f"sit-{hash(s['topic']) & 0xFFFFFF}"
        remove_btn = f'<button onclick="removeSituation(\'{s["topic"].replace(chr(39), chr(92)+chr(39))}\')" title="Stop tracking" style="background:none;border:none;cursor:pointer;color:#333330;font-size:16px;padding:0;line-height:1;transition:color 0.15s;" onmouseover="this.style.color=\'#c0392b\'" onmouseout="this.style.color=\'#333330\'">&#215;</button>'
        items_html += f'''<div id="{sit_id}" style="background:#161614;border:1px solid rgba(255,255,255,0.05);border-radius:10px;padding:16px 18px;margin-bottom:8px;transition:opacity 0.4s;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
    <div style="font-size:14px;font-weight:500;color:#f0ece4;">{s["topic"].replace("<","&lt;").replace(">","&gt;")}{badge}</div>
    {remove_btn}
  </div>
  <div style="{update_style}">{s.get("update","No updates today.").replace("<","&lt;").replace(">","&gt;")}</div>
  {f'<div style="margin-top:10px;">{art_html}</div>' if art_html else ""}
</div>'''

        return f'''<div style="margin-bottom:3.5rem;">
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
        breaking_stories_html = ""
        for i, story in enumerate(breaking_stories):
            breaking_stories_html += render_story(story, i, ac_b, is_top=(i==0))
    yesterday_breaking = yesterday_data.get("breaking",[])
    yest_b_html = ""
    if yesterday_breaking:
        yest_b_html = f'<div style="margin-top:1.5rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,0.05);"><p style="font-size:11px;color:#333330;letter-spacing:0.05em;text-transform:uppercase;margin-bottom:10px;">From yesterday</p>'
        for i, s in enumerate(yesterday_breaking):
            fake = {"headline": s["headline"], "timestamp": "yesterday", "score": s.get("score",5), "summary": "", "articles": []}
            yest_b_html += render_story(fake, i, ac_b, is_yesterday=True)
        yest_b_html += "</div>"

    breaking_html = f'''<div style="margin-bottom:3.5rem;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:10px;">
      <div style="width:3px;height:24px;border-radius:2px;background:{ac_b};flex-shrink:0;"></div>
      <div style="font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:500;letter-spacing:-0.01em;">Breaking News</div>
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
            yest_html = f'<div style="margin-top:1rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,0.05);"><p style="font-size:11px;color:#333330;letter-spacing:0.05em;text-transform:uppercase;margin-bottom:8px;">From yesterday</p>'
            for i, s in enumerate(cat["yesterday"]):
                fake = {"headline": s["headline"], "timestamp": "yesterday", "score": s.get("score",5), "summary": "", "articles": []}
                yest_html += render_story(fake, i, ac, is_yesterday=True)
            yest_html += "</div>"
        cols_html += f'''<div style="min-width:0;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="width:3px;height:22px;border-radius:2px;background:{ac};flex-shrink:0;"></div>
    <div style="font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:500;letter-spacing:-0.01em;">{cat["label"]}</div>
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
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body{{background:#111110;color:#f0ece4;font-family:'Inter',sans-serif;font-size:15px;line-height:1.6;min-height:100vh;}}
.story-header:hover{{background:rgba(255,255,255,0.02);}}
.story-header:hover .chev{{color:#6e6b64;}}
@media(max-width:768px){{
  .grid-3{{grid-template-columns:1fr!important;}}
}}
</style>
</head>
<body>
<div style="max-width:1100px;margin:0 auto;padding:3rem 2rem 6rem;">

  <div style="margin-bottom:3rem;padding-bottom:1.5rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#333330;margin-bottom:12px;">{date_str}</div>
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:12px;">
      <h1 style="font-family:'Playfair Display',serif;font-size:3rem;font-weight:700;letter-spacing:-0.03em;line-height:1;">Your briefing</h1>
      <span style="font-size:11px;color:#2a2a28;padding-bottom:6px;" id="refresh-status">Refreshes automatically</span>
    </div>
  </div>

  {render_world_topics()}
  {render_developing()}
  {breaking_html}
  <div class="grid-3" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:2rem;margin-bottom:3.5rem;">{cols_html}</div>

</div>

<!-- Star popup overlay -->
<div id="star-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
  <div style="background:#1c1c1a;border:1px solid rgba(255,255,255,0.12);border-radius:16px;padding:1.5rem;width:min(440px,90vw);">
    <div style="font-size:14px;font-weight:500;color:#f0ece4;margin-bottom:6px;">Track this situation</div>
    <div id="star-headline-preview" style="font-size:12px;color:#555550;margin-bottom:14px;font-style:italic;line-height:1.4;"></div>
    <div style="font-size:12px;color:#6e6b64;margin-bottom:6px;">Track as:</div>
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
  fetch("https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + PINNED_FILE_PATH, {{
    headers: {{ "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json" }}
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

// ── Star popup ──
function showStarPopup(headline, suggestedTopic) {{
  var overlay = document.getElementById("star-overlay");
  var input = document.getElementById("star-input");
  var status = document.getElementById("star-status");
  overlay.style.display = "flex";
  input.value = suggestedTopic;
  status.textContent = "";
  input.focus();
  input.select();
  document.getElementById("star-headline-preview").textContent = '"' + headline.substring(0,80) + (headline.length>80?"...":'"');
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
          setTimeout(closeStarPopup, 1500);
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
          var el = document.getElementById("sit-" + btoa(topic).replace(/[^a-zA-Z0-9]/g,"").substring(0,20));
          if (el) el.style.opacity = "0.3";
          setTimeout(function() {{ if(el) el.remove(); }}, 400);
        }}
      }});
    }});
  }});
}}

// ── Story expand ──
document.querySelectorAll('.story-header').forEach(function(h){{
  h.addEventListener('click',function(e){{
    if (e.target.closest('.star-btn')) return;
    var body=this.parentElement.querySelector('.story-body');
    var chev=this.querySelector('.chev');
    var isOpen=body.style.display==='block';
    body.style.display=isOpen?'none':'block';
    chev.style.transform=isOpen?'none':'rotate(180deg)';
  }});
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

// ── Auto-refresh ──
setInterval(function(){{
  fetch(window.location.href+'?ts='+Date.now())
    .then(function(r){{return r.text();}})
    .then(function(html){{
      var match = html.match(/var BUILD_TS = (\d+)/);
      if(match && parseInt(match[1]) > BUILD_TS){{
        document.getElementById('refresh-status').textContent = 'New update — reloading...';
        setTimeout(function(){{ window.location.reload(); }}, 2000);
      }}
    }}).catch(function(){{}});
}}, 5 * 60 * 1000);</script>
</body>
</html>'''

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    memory = load_memory()
    pinned = load_pinned()

    print("Fetching world topics from GDELT...")
    today_topics = fetch_gdelt_top_stories("24h")
    week_topics = fetch_gdelt_top_stories("7d")
    month_topics = fetch_gdelt_top_stories("30d")
    world_topics = process_world_topics(today_topics, week_topics, month_topics)
    time.sleep(30)

    print("Fetching Breaking News...")
    gdelt_breaking = fetch_gdelt_articles("war killed attack invasion disaster explosion casualties", timespan="2h", max_records=25)
    guardian_breaking = fetch_guardian("world war attack disaster crisis killed invasion", page_size=15)
    breaking = process_breaking_news(gdelt_breaking, guardian_breaking)
    time.sleep(60)

    print("Fetching Australia news...")
    abc_rss = fetch_rss("https://www.abc.net.au/news/feed/51120/rss.xml", "ABC News")
    smh_rss = fetch_rss("https://www.smh.com.au/rss/feed.xml", "SMH")
    age_rss = fetch_rss("https://www.theage.com.au/rss/feed.xml", "The Age")
    newsdata_aus = fetch_newsdata("australia parliament senate election albanese budget policy", country="au")
    australia = process_australia(abc_rss + smh_rss + age_rss, newsdata_aus)
    time.sleep(60)

    print("Fetching Archaeology news...")
    nature_rss = fetch_rss("https://www.nature.com/nature.rss", "Nature")
    newscientist_rss = fetch_rss("https://www.newscientist.com/subject/humans/feed/", "New Scientist")
    science_rss = fetch_rss("https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science", "Science")
    newsdata_arch = fetch_newsdata("paleoanthropology fossil hominin ancient DNA homo sapiens neanderthal discovery")
    archaeology = process_archaeology(nature_rss + newscientist_rss + science_rss + newsdata_arch)
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
    football = process_football(guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss)

    all_data = {
        "breaking": breaking,
        "australia": australia,
        "archaeology": archaeology,
        "football": football
    }

    print("Processing developing situations...")
    all_fetched = (gdelt_breaking + guardian_breaking + abc_rss + smh_rss + age_rss +
                   newsdata_aus + nature_rss + newscientist_rss + science_rss + newsdata_arch +
                   guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss)
    auto_detected = detect_developing_situations(memory, all_data)
    developing_situations = process_developing_situations(pinned, auto_detected, all_fetched)

    yesterday_data = {
        "breaking": get_yesterday_stories(memory, "breaking"),
        "australia": get_yesterday_stories(memory, "australia"),
        "archaeology": get_yesterday_stories(memory, "archaeology"),
        "football": get_yesterday_stories(memory, "football")
    }

    for cat in ["breaking", "australia", "archaeology", "football"]:
        memory = save_today_stories(memory, cat, all_data[cat])
    save_memory(memory)

    Path("dist").mkdir(exist_ok=True)
    with open("dist/index.html", "w", encoding="utf-8") as f:
        f.write(build_html(all_data, yesterday_data, world_topics, developing_situations))
    print("Done. dist/index.html written.")

if __name__ == "__main__":
    main()
