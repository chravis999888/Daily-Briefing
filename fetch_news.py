import os
import json
import time
import requests
import feedparser
import anthropic
from datetime import datetime, timezone, timedelta
from pathlib import Path

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NEWSDATA_KEY = os.environ["NEWSDATA_API_KEY"]
GUARDIAN_KEY = os.environ["GUARDIAN_API_KEY"]

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

AEST = timezone(timedelta(hours=10))

ACCENTS = {
    "breaking": "#c0392b",
    "australia": "#2e7bbf",
    "archaeology": "#b07d2a",
    "football": "#2a7a52"
}

HEADLINE_RULES = """
CRITICAL HEADLINE RULES:
- Write a single sentence that states the actual fact. The reader should be fully informed without needing to click.
- NEVER use: "faces", "races against time", "sparks debate", "raises concerns", "under pressure", "amid tensions", "could impact", "warns of", "signals", "eyes", "targets", "mulls", "critical moment"
- BAD: "Prime Minister faces critical moment as government races against time on key policy"
- GOOD: "Albanese's Help to Buy housing scheme passes Senate after Greens back amended bill"
- BAD: "Scientists make breakthrough discovery that could rewrite human history"
- GOOD: "450,000-year-old skull found in Israel identified as new Homo species distinct from Neanderthals"
The headline must be a factual summary, not a teaser.
"""

def aest_now():
    return datetime.now(AEST).strftime("%I:%M %p AEST").lstrip("0")

def call_claude(prompt, max_tokens=1000):
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

def call_claude_with_search(prompt, max_tokens=1500):
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )
    for block in msg.content:
        if block.type == "text":
            return block.text
    return ""

def get_ai_summary(headline, content=""):
    prompt = f"""In 3-4 sentences, explain this news story clearly and factually.
Headline: "{headline}"
{f'Article content: {content[:1500]}' if content else ''}
Cover what happened, why it matters, and any important background. Plain English, no fluff."""
    return call_claude(prompt, 400)

def fetch_guardian(query, page_size=10):
    url = "https://content.guardianapis.com/search"
    params = {
        "q": query,
        "api-key": GUARDIAN_KEY,
        "page-size": page_size,
        "order-by": "newest",
        "show-fields": "headline,trailText,bodyText"
    }
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
                "content": summary[:1000]
            })
        return articles
    except Exception as e:
        print(f"RSS fetch error {url}: {e}")
        return []

def fetch_newsdata(query, country=None, category=None):
    url = "https://newsdata.io/api/1/news"
    params = {
        "apikey": NEWSDATA_KEY,
        "q": query,
        "language": "en",
        "full_content": 1
    }
    if country:
        params["country"] = country
    if category:
        params["category"] = category
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        results = data.get("results", [])
        articles = []
        for a in results:
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

def format_articles_for_prompt(articles):
    parts = []
    for a in articles[:15]:
        content = a.get("content", "").strip()
        if content:
            parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\nCONTENT: {content[:800]}\nURL: {a['url']}\n")
        else:
            parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\nURL: {a['url']}\n")
    return "\n---\n".join(parts)

def process_breaking_news(articles):
    if not articles:
        return []
    formatted = format_articles_for_prompt(articles)
    prompt = f"""You are a world news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles with their content:
{formatted}

Select ONLY stories that are historic in scale — active major wars significantly escalating with large casualties, world leader deaths, terrorist attacks killing hundreds+, catastrophic natural disasters with mass casualties, nuclear threats. DO NOT include diplomatic talks, peace negotiations, ceasefire discussions, court cases, political scandals, or official warnings. Only include something if it involves large-scale violence, death, or an irreversible world-changing event actively happening now. If nothing meets this bar return [].

For each selected story, read the actual article content and write a headline that states the specific facts. Include numbers, names, locations. Estimate timestamp and whether deeper search is needed.

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","deeper_search":false,"url":"...","source":"..."}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt, 1000)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        articles_list = [{"title": story.get("headline",""), "source": story.get("source",""), "url": story.get("url","")}]
        if story.get("deeper_search"):
            search_prompt = f"""Search for the latest news about: "{story['headline']}"
Find articles from multiple major sources (Reuters, BBC, AP, Al Jazeera).
Return ONLY a JSON array of up to 5 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_claude_with_search(search_prompt, 800)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                articles_list = extra
            except:
                pass
        orig = next((a for a in articles if a["url"] == story.get("url","")), {})
        summary = get_ai_summary(story["headline"], orig.get("content",""))
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "summary": summary,
            "articles": articles_list
        })
    return results

def process_australia(rss_articles, newsdata_articles):
    all_articles = rss_articles + newsdata_articles
    if not all_articles:
        return []
    formatted = format_articles_for_prompt(all_articles)
    prompt = f"""You are an Australian political news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles with their content:
{formatted}

Select ONLY stories about Australian domestic politics: federal or state parliament votes, bills passed or failed, budget decisions, elections, party leadership changes, High Court rulings, major national policy changes. DO NOT include international news, road accidents, crime, industry news, sport, weather, or human interest stories. Aim for 2-4 stories. If nothing meets the bar return [].

For each selected story, read the actual article content and write a headline with the specific facts — what bill, what vote result, what policy, what court ruling. Be specific.

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","url":"...","source":"...","deeper_context":false,"context_angle":""}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt, 1000)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        orig = next((a for a in all_articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source",""), "url": story.get("url","")}]
        if story.get("deeper_context") and story.get("context_angle"):
            search_prompt = f"""Search for context on: "{story['context_angle']}"
Return ONLY a JSON array of up to 3 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_claude_with_search(search_prompt, 600)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                articles_list = articles_list + extra
            except:
                pass
        summary = get_ai_summary(story["headline"], orig.get("content",""))
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "summary": summary,
            "articles": articles_list
        })
    return results

def process_archaeology(articles):
    if not articles:
        return []
    formatted = format_articles_for_prompt(articles)
    prompt = f"""You are a science editor specialising in human origins. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles with their content:
{formatted}

Select ONLY significant palaeoanthropological discoveries: new hominin species, fossil finds pushing back human evolution dates, ancient DNA findings changing understanding of human lineage, discoveries contradicting existing models of Homo sapiens, Neanderthals, Denisovans, Homo erectus. DO NOT include general archaeology, dinosaurs, ancient civilisations unless directly related to hominin evolution. Aim for 2-4 stories ordered by significance. If nothing meets the bar return [].

Read the actual content and write headlines with the specific discovery, location, age, and significance.

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","url":"...","source":"..."}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt, 800)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        orig = next((a for a in articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source",""), "url": story.get("url","")}]
        summary = get_ai_summary(story["headline"], orig.get("content",""))
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "summary": summary,
            "articles": articles_list
        })
    return results

def process_football(articles):
    if not articles:
        return []
    formatted = format_articles_for_prompt(articles)
    prompt = f"""You are a football editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent articles with their content:
{formatted}

Select ONLY significant stories from Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League: match results with scorelines, key injuries, manager sackings, confirmed transfers, extraordinary performances. NO rumours, previews, or press conference opinions. Aim for 4-6 stories. If nothing meets the bar return [].

Read the actual content and write headlines with specific details — actual scores, player names, clubs, significance.

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","url":"...","source":"...","deeper_context":false,"context_angle":""}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt, 1000)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        orig = next((a for a in articles if a["url"] == story.get("url","")), {})
        articles_list = [{"title": orig.get("title",""), "source": story.get("source",""), "url": story.get("url","")}]
        if story.get("deeper_context") and story.get("context_angle"):
            search_prompt = f"""Search for context on this football story: "{story['context_angle']}"
Return ONLY a JSON array of up to 3 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_claude_with_search(search_prompt, 600)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                articles_list = articles_list + extra
            except:
                pass
        summary = get_ai_summary(story["headline"], orig.get("content",""))
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "summary": summary,
            "articles": articles_list
        })
    return results

def build_html(all_data):
    date_str = datetime.now(AEST).strftime("%A %d %B %Y").upper()
    updated_str = datetime.now(AEST).strftime("%I:%M %p AEST").lstrip("0")

    categories = [
        {"id": "breaking", "label": "Breaking News", "data": all_data["breaking"]},
        {"id": "australia", "label": "Australia", "data": all_data["australia"]},
        {"id": "archaeology", "label": "Archaeology & Palaeoanthropology", "data": all_data["archaeology"]},
        {"id": "football", "label": "Football", "data": all_data["football"]}
    ]

    sections_html = ""
    for cat in categories:
        ac = ACCENTS[cat["id"]]
        stories_html = ""
        if not cat["data"]:
            stories_html = '<p style="padding:1.5rem 0;color:#333330;font-size:13px;">Nothing significant right now.</p>'
        else:
            for i, story in enumerate(cat["data"]):
                num = f"0{i+1}" if i+1 < 10 else str(i+1)
                arts = story.get("articles", [])
                summary = story.get("summary", "")
                art_html = "".join([
                    f'<a href="{a.get("url","#")}" target="_blank" rel="noreferrer noopener" style="display:flex;align-items:baseline;justify-content:space-between;gap:10px;padding:8px 11px;border-radius:8px;background:#161614;text-decoration:none;margin-bottom:3px;">'
                    f'<span style="font-size:13px;color:#f0ece4;line-height:1.4;flex:1;font-weight:300;">{a.get("title","").replace("<","&lt;").replace(">","&gt;")}</span>'
                    f'<span style="font-size:11px;color:#333330;white-space:nowrap;flex-shrink:0;margin-left:8px;">{a.get("source","")}</span>'
                    f'</a>'
                    for a in arts if a.get("url","").startswith("http")
                ])
                meta_parts = []
                if story.get("timestamp"):
                    meta_parts.append(f'<span style="color:{ac};font-weight:500;">{story["timestamp"]}</span>')
                if arts:
                    meta_parts.append(f'<span style="color:#6e6b64;">{arts[0].get("source","")}</span>')
                if len(arts) > 1:
                    meta_parts.append(f'<span style="color:#444440;">{len(arts)} sources</span>')
                meta_html = ' <span style="color:#2a2a28;">·</span> '.join(meta_parts)

                stories_html += f'''<div class="story" id="story-{cat["id"]}-{i}" style="border-bottom:1px solid rgba(255,255,255,0.07);">
  <div class="story-header" style="display:flex;align-items:flex-start;gap:14px;padding:18px 0;cursor:pointer;">
    <span style="font-size:11px;color:#2a2a28;min-width:18px;margin-top:3px;font-variant-numeric:tabular-nums;">{num}</span>
    <div style="flex:1;">
      <div style="font-size:15px;font-weight:400;line-height:1.5;color:#f0ece4;margin-bottom:6px;letter-spacing:-0.01em;">{story["headline"].replace("<","&lt;").replace(">","&gt;")}</div>
      <div style="font-size:11px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;">{meta_html}</div>
    </div>
    <div class="chev" style="font-size:10px;color:#2a2a28;margin-top:4px;flex-shrink:0;transition:transform 0.2s;">&#9660;</div>
  </div>
  <div class="story-body" style="display:none;padding:0 0 20px 32px;">
    <div style="font-size:13px;line-height:1.7;color:#8a8680;background:#1a1a18;border-left:2px solid {ac};padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:14px;font-style:italic;">{summary.replace("<","&lt;").replace(">","&gt;")}</div>
    {art_html}
  </div>
</div>'''

        sections_html += f'''<div style="margin-bottom:4rem;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.25rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="width:3px;height:22px;border-radius:2px;background:{ac};flex-shrink:0;"></div>
      <div style="font-family:\'Playfair Display\',serif;font-size:1.2rem;font-weight:500;">{cat["label"]}</div>
    </div>
    <span style="font-size:11px;color:#2a2a28;">Updated {updated_str}</span>
  </div>
  {stories_html}
</div>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Daily Briefing</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;700&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body{{background:#111110;color:#f0ece4;font-family:'Inter',sans-serif;font-size:15px;line-height:1.6;min-height:100vh;}}
.story-header:hover .chev{{color:#6e6b64;}}
</style>
</head>
<body>
<div style="max-width:860px;margin:0 auto;padding:3rem 2rem 6rem;">
  <div style="margin-bottom:3.5rem;">
    <div style="font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#444440;margin-bottom:10px;">{date_str}</div>
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:12px;">
      <h1 style="font-family:'Playfair Display',serif;font-size:2.8rem;font-weight:700;letter-spacing:-0.02em;line-height:1.1;">Your briefing</h1>
      <span style="font-size:11px;color:#2a2a28;padding-bottom:8px;">Refreshes automatically</span>
    </div>
  </div>
  {sections_html}
</div>
<script>
document.querySelectorAll('.story-header').forEach(function(header) {{
  header.addEventListener('click', function() {{
    var story = this.parentElement;
    var body = story.querySelector('.story-body');
    var chev = this.querySelector('.chev');
    var isOpen = body.style.display === 'block';
    body.style.display = isOpen ? 'none' : 'block';
    chev.style.transform = isOpen ? 'none' : 'rotate(180deg)';
  }});
}});
</script>
</body>
</html>'''

def main():
    print("Fetching Breaking News from Guardian...")
    guardian_articles = fetch_guardian("world war attack disaster crisis killed invasion", 15)
    breaking = process_breaking_news(guardian_articles)
    time.sleep(60)

    print("Fetching Australia news...")
    abc_rss = fetch_rss("https://www.abc.net.au/news/feed/51120/rss.xml", "ABC News")
    newsdata_aus = fetch_newsdata("australia parliament senate election albanese budget policy", country="au")
    australia = process_australia(abc_rss, newsdata_aus)
    time.sleep(60)

    print("Fetching Archaeology news...")
    newsdata_arch = fetch_newsdata("paleoanthropology fossil hominin ancient DNA homo sapiens neanderthal discovery")
    archaeology = process_archaeology(newsdata_arch)
    time.sleep(60)

    print("Fetching Football news...")
    bbc_football = fetch_rss("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport")
    football = process_football(bbc_football)

    all_data = {
        "breaking": breaking,
        "australia": australia,
        "archaeology": archaeology,
        "football": football
    }

    Path("dist").mkdir(exist_ok=True)
    with open("dist/index.html", "w", encoding="utf-8") as f:
        f.write(build_html(all_data))
    print("Done. dist/index.html written.")

if __name__ == "__main__":
    main()
