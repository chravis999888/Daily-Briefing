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

ACCENTS = {
    "breaking": "#c0392b",
    "australia": "#2e7bbf",
    "archaeology": "#b07d2a",
    "football": "#2a7a52"
}

MEMORY_FILE = "memory.json"

HEADLINE_RULES = """
CRITICAL HEADLINE RULES:
- Write a single sentence stating the actual specific fact. The reader must be fully informed without clicking.
- Include real names, real numbers, real outcomes.
- NEVER use: "faces", "races against time", "sparks debate", "raises concerns", "under pressure", "amid tensions", "could impact", "warns of", "signals", "eyes", "targets", "mulls", "critical moment", "decisive", "implications"
- BAD: "Manchester City and Arsenal face critical final month with title implications"
- GOOD: "Manchester City lead Arsenal by 2 points with 5 games remaining as Premier League title race enters final stretch"
- BAD: "Prime Minister faces critical moment as government races against time on key policy"
- GOOD: "Albanese's Help to Buy housing scheme passes Senate after Greens back amended bill"
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
    return {}

def save_memory(memory):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=2)
    except Exception as e:
        print(f"Memory save error: {e}")

def get_yesterday_stories(memory, category):
    yesterday = (datetime.now(AEST) - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    stories = []
    for date, cats in memory.items():
        if date == yesterday and date != today:
            stories.extend(cats.get(category, []))
    return stories

def save_today_stories(memory, category, stories):
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    if today not in memory:
        memory[today] = {}
    memory[today][category] = [{"headline": s["headline"], "timestamp": s.get("timestamp",""), "score": s.get("score", 5)} for s in stories]
    # Keep only last 3 days
    cutoff = (datetime.now(AEST) - timedelta(days=3)).strftime("%Y-%m-%d")
    memory = {k: v for k, v in memory.items() if k >= cutoff}
    return memory

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

def fetch_gdelt(query, timespan="2h", max_records=25):
    try:
        f = Filters(keyword=query, timespan=timespan, num_records=max_records)
        articles_df = gd.article_search(f)
        articles = []
        for _, row in articles_df.iterrows():
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

def fetch_guardian(query, page_size=15, section=None):
    url = "https://content.guardianapis.com/search"
    params = {
        "q": query,
        "api-key": GUARDIAN_KEY,
        "page-size": page_size,
        "order-by": "newest",
        "show-fields": "headline,trailText,bodyText"
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
                "content": body[:2000]
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
            articles.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": source_name,
                "time": entry.get("published", ""),
                "content": re.sub(r'<[^>]+>', '', summary)[:1000]
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
                "content": content[:2000]
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

# ── Processors ────────────────────────────────────────────────────────────────

def process_breaking_news(gdelt_articles, guardian_articles):
    guardian_urls = {a["url"] for a in guardian_articles}
    all_articles = guardian_articles + [a for a in gdelt_articles if a["url"] not in guardian_urls]
    if not all_articles:
        return []

    formatted = format_articles_for_prompt(all_articles, 25)
    prompt = f"""You are a world news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles from global sources including GDELT and The Guardian:
{formatted}

Select ONLY stories that are historic in scale — active major wars significantly escalating with large casualties, world leader deaths, terrorist attacks killing hundreds+, catastrophic natural disasters with mass casualties, nuclear threats. DO NOT include diplomatic talks, peace negotiations, ceasefire discussions, court cases, political scandals, or warnings. Only include something actively happening now involving large-scale violence, death, or irreversible world-changing events. If nothing meets this bar return [].

Prioritise stories confirmed by multiple sources. Guardian articles are higher quality than unknown domains.

For each story:
- Write a specific factual headline with real numbers, names, locations
- Assign an importance score 1-10 (10 = world-defining like 9/11, 7-8 = major war escalation, 5-6 = significant but not historic)
- Estimate timestamp
- Identify a "so_what" thread — the broader geopolitical/historical context this connects to (or leave blank if none)
- Flag if deeper multi-source search needed

ONLY include facts explicitly stated in the article content.

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","score":8,"timestamp":"...","deeper_search":false,"so_what":"...","url":"...","source":"..."}}]
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
            search_prompt = f"""Search for the latest news and context about: "{search_q}"
Find articles from Reuters, BBC, AP, Al Jazeera, major outlets.
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
- Write a specific factual headline with the actual bill name, vote result, policy detail, or court ruling
- Assign importance score 1-10
- Estimate timestamp
- Identify a "so_what" thread — what broader political narrative does this connect to? (election implications, cost of living, party dynamics)

ONLY include facts explicitly stated in the article content.

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

        context = ""
        if story.get("so_what"):
            search_prompt = f"""Search for context on this Australian political story: "{story['so_what']}"
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

Select ONLY significant palaeoanthropological discoveries: new hominin species, fossil finds pushing back human evolution dates, ancient DNA findings, discoveries contradicting existing models of Homo sapiens, Neanderthals, Denisovans, Homo erectus. NO general archaeology unless directly related to hominin evolution. Aim for 2-4 stories. If nothing meets the bar return [].

For each story:
- Write a specific factual headline with the discovery, location, age, species, significance
- Assign importance score 1-10
- Estimate timestamp
- Identify a "so_what" thread — which existing theory does this challenge or confirm?

ONLY include facts explicitly stated in the article.

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
            "articles": articles_list
        })
    return results

def process_football(articles):
    if not articles:
        return []

    formatted = format_articles_for_prompt(articles, 30)
    prompt = f"""You are a football editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles from The Guardian, Sky Sports and other sources:
{formatted}

Select ONLY significant stories from Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League. Cover all leagues equally. Only include: confirmed match results with scorelines, confirmed injuries affecting a team's season, confirmed manager sackings/appointments, confirmed transfers, extraordinary performances with stats, title race or relegation developments with actual standings. NO rumours, previews, or press conferences. Aim for 6-10 stories. If nothing meets the bar return [].

For each story:
- Write a specific factual headline with actual scores, player names, clubs, standings
- Assign importance score 1-10
- Estimate timestamp
- Identify a "so_what" thread — Golden Boot race, title race standings, relegation battle context (leave blank if routine)

ONLY include facts explicitly stated in the article.

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

        context = ""
        if story.get("so_what"):
            search_prompt = f"""Search for context on this football story: "{story['so_what']}"
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
            "articles": articles_list
        })
    return results

# ── HTML Builder ──────────────────────────────────────────────────────────────

def build_html(all_data, yesterday_data):
    date_str = datetime.now(AEST).strftime("%A %d %B %Y").upper()
    updated_str = datetime.now(AEST).strftime("%I:%M %p AEST").lstrip("0")

    categories = [
        {"id": "breaking", "label": "Breaking News", "data": all_data["breaking"], "yesterday": yesterday_data.get("breaking", [])},
        {"id": "australia", "label": "Australia", "data": all_data["australia"], "yesterday": yesterday_data.get("australia", [])},
        {"id": "archaeology", "label": "Archaeology & Palaeoanthropology", "data": all_data["archaeology"], "yesterday": yesterday_data.get("archaeology", [])},
        {"id": "football", "label": "Football", "data": all_data["football"], "yesterday": yesterday_data.get("football", [])}
    ]

    def render_story(story, i, ac, is_top=False, is_yesterday=False):
        num = f"0{i+1}" if i+1 < 10 else str(i+1)
        arts = story.get("articles", [])
        summary = story.get("summary", "").replace("<","&lt;").replace(">","&gt;")
        score = story.get("score", 5)

        art_html = "".join([
            f'<a href="{a.get("url","#")}" target="_blank" rel="noreferrer noopener" '
            f'style="display:flex;align-items:center;justify-content:space-between;gap:12px;'
            f'padding:9px 14px;border-radius:8px;background:#0d0d0c;text-decoration:none;'
            f'margin-bottom:4px;border:1px solid rgba(255,255,255,0.05);'
            f'transition:border-color 0.15s;">'
            f'<span style="font-size:13px;color:#c8c4bc;line-height:1.4;flex:1;font-weight:300;">'
            f'{a.get("title","").replace("<","&lt;").replace(">","&gt;")}</span>'
            f'<span style="font-size:11px;color:#444440;white-space:nowrap;flex-shrink:0;margin-left:8px;">'
            f'{a.get("source","")}</span>'
            f'</a>'
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
        headline_weight = "500" if is_top else "400"
        card_bg = "#1a1a18" if is_top else "#161614"
        card_border = f"1px solid rgba(255,255,255,0.08)" if is_top else "1px solid rgba(255,255,255,0.04)"
        score_dot = ""
        if score >= 8:
            score_dot = f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{ac};margin-right:8px;margin-bottom:1px;vertical-align:middle;"></span>'
        opacity = "0.6" if is_yesterday else "1"

        return f'''<div class="story" style="border-radius:10px;background:{card_bg};border:{card_border};margin-bottom:8px;opacity:{opacity};">
  <div class="story-header" style="display:flex;align-items:flex-start;gap:14px;padding:16px 18px;cursor:pointer;">
    <span style="font-size:11px;color:#2a2a28;min-width:20px;margin-top:3px;font-variant-numeric:tabular-nums;flex-shrink:0;">{num}</span>
    <div style="flex:1;min-width:0;">
      <div style="font-size:{headline_size};font-weight:{headline_weight};line-height:1.45;color:#f0ece4;margin-bottom:8px;letter-spacing:-0.01em;">{score_dot}{story["headline"].replace("<","&lt;").replace(">","&gt;")}</div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">{meta_html}</div>
    </div>
    <div class="chev" style="font-size:10px;color:#2a2a28;margin-top:4px;flex-shrink:0;transition:transform 0.2s;margin-left:8px;">&#9660;</div>
  </div>
  <div class="story-body" style="display:none;padding:0 18px 18px 52px;">
    <div style="font-size:13px;line-height:1.75;color:#8a8680;background:#111110;border-left:2px solid {ac};padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:12px;">{summary}</div>
    {art_html}
  </div>
</div>'''

    sections_html = ""
    for cat in categories:
        ac = ACCENTS[cat["id"]]
        stories = cat["data"]
        yesterday = cat["yesterday"]

        stories_html = ""
        if not stories:
            stories_html = '<p style="padding:1.5rem 0.5rem;color:#333330;font-size:13px;">Nothing significant right now.</p>'
        else:
            for i, story in enumerate(stories):
                stories_html += render_story(story, i, ac, is_top=(i==0))

        yesterday_html = ""
        if yesterday:
            yesterday_html = f'<div style="margin-top:1.5rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,0.05);">'
            yesterday_html += f'<p style="font-size:11px;color:#333330;letter-spacing:0.05em;text-transform:uppercase;margin-bottom:10px;">From yesterday</p>'
            for i, story in enumerate(yesterday):
                fake = {"headline": story["headline"], "timestamp": "yesterday", "score": story.get("score",5), "summary": "", "articles": []}
                yesterday_html += render_story(fake, i, ac, is_yesterday=True)
            yesterday_html += '</div>'

        sections_html += f'''<div style="margin-bottom:3.5rem;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:10px;">
      <div style="width:3px;height:24px;border-radius:2px;background:{ac};flex-shrink:0;"></div>
      <div style="font-family:\'Playfair Display\',serif;font-size:1.3rem;font-weight:500;letter-spacing:-0.01em;">{cat["label"]}</div>
    </div>
    <span style="font-size:11px;color:#2a2a28;">Updated {updated_str}</span>
  </div>
  {stories_html}
  {yesterday_html}
</div>'''

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
.story-header:hover{{background:rgba(255,255,255,0.02);border-radius:8px;}}
.story-header:hover .chev{{color:#6e6b64;}}
a[href]:hover span:first-child{{color:#f0ece4;}}
</style>
</head>
<body>
<div style="max-width:820px;margin:0 auto;padding:3rem 1.5rem 6rem;">

  <div style="margin-bottom:3rem;padding-bottom:1.5rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#333330;margin-bottom:12px;">{date_str}</div>
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:12px;">
      <h1 style="font-family:'Playfair Display',serif;font-size:3rem;font-weight:700;letter-spacing:-0.03em;line-height:1;">Your briefing</h1>
      <span style="font-size:11px;color:#2a2a28;padding-bottom:6px;letter-spacing:0.03em;">Refreshes automatically</span>
    </div>
  </div>

  {sections_html}

</div>
<script>
document.querySelectorAll('.story-header').forEach(function(header){{
  header.addEventListener('click',function(){{
    var body=this.parentElement.querySelector('.story-body');
    var chev=this.querySelector('.chev');
    var isOpen=body.style.display==='block';
    body.style.display=isOpen?'none':'block';
    chev.style.transform=isOpen?'none':'rotate(180deg)';
  }});
}});
</script>
</body>
</html>'''

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    memory = load_memory()

    print("Fetching Breaking News...")
    gdelt_breaking = fetch_gdelt("war killed attack invasion disaster explosion casualties", timespan="2h", max_records=25)
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
        f.write(build_html(all_data, yesterday_data))
    print("Done. dist/index.html written.")

if __name__ == "__main__":
    main()
