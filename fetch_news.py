import os
import json
import time
import requests
import feedparser
import anthropic
from datetime import datetime, timezone, timedelta

AEST = timezone(timedelta(hours=10))
from pathlib import Path

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NEWSDATA_KEY = os.environ["NEWSDATA_API_KEY"]
GUARDIAN_KEY = os.environ["GUARDIAN_API_KEY"]

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

ACCENTS = {
    "breaking": "#c0392b",
    "australia": "#2e7bbf",
    "archaeology": "#b07d2a",
    "football": "#2a7a52"
}

def aest_now():
    return datetime.now(AEST).strftime("%I:%M %p AEST").lstrip("0")


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

def fetch_guardian(query, page_size=10):
    url = "https://content.guardianapis.com/search"
    params = {
        "q": query,
        "api-key": GUARDIAN_KEY,
        "page-size": page_size,
        "order-by": "newest",
        "show-fields": "headline,trailText,shortUrl"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        results = data.get("response", {}).get("results", [])
        return [{"title": a.get("webTitle", ""), "url": a.get("webUrl", ""), "source": "The Guardian", "time": a.get("webPublicationDate", "")} for a in results]
    except Exception as e:
        print(f"Guardian fetch error: {e}")
        return []

def fetch_rss(url, source_name):
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:20]:
            articles.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": source_name,
                "time": entry.get("published", "")
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
        "language": "en"
    }
    if country:
        params["country"] = country
    if category:
        params["category"] = category
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        results = data.get("results", [])
        return [{"title": a.get("title", ""), "url": a.get("link", ""), "source": a.get("source_id", ""), "time": a.get("pubDate", "")} for a in results]
    except Exception as e:
        print(f"NewsData fetch error: {e}")
        return []

def process_breaking_news(articles):
    if not articles:
        return []
    headlines = "\n".join([f"- {a['title']} ({a['source']})" for a in articles[:20]])
    prompt = f"""You are a world news editor. Today is {datetime.now(timezone.utc).strftime('%A %d %B %Y')}.

Here are recent headlines from The Guardian:
{headlines}

Your job:
1. Identify only stories that are truly world-altering — major wars escalating, world leader deaths, large scale attacks, catastrophic natural disasters with mass casualties, massive geopolitical shocks. If nothing meets this bar, return an empty array.
2. For each significant story, write a plain English headline that explains what actually happened and why it matters — not clickbait, the actual substance.
3. Estimate how long ago this broke (e.g. "2 hours ago", "yesterday").
4. Flag if the story warrants a deeper multi-source search (true/false).

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","deeper_search":true,"guardian_title":"...","url":"..."}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        articles_list = [{"title": story.get("guardian_title",""), "source": "The Guardian", "url": story.get("url","")}]
        if story.get("deeper_search"):
            search_prompt = f"""Search for the latest news about: "{story['headline']}"
Find articles from multiple major sources (Reuters, BBC, AP, Al Jazeera etc).
Return ONLY a JSON array of up to 5 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_claude_with_search(search_prompt, 800)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                articles_list = extra
            except:
                pass
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "articles": articles_list
        })
    return results

def process_australia(rss_articles, newsdata_articles):
    all_articles = rss_articles + newsdata_articles
    if not all_articles:
        return []
    headlines = "\n".join([f"- {a['title']} ({a['source']})" for a in all_articles[:30]])
    prompt = f"""You are an Australian political and domestic news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent headlines:
{headlines}

Your job:
1. Select ONLY stories that are specifically about Australian domestic affairs — federal or state parliament decisions, major Australian policy changes, elections and polling, significant Australian political events, major cultural or social shifts within Australia, significant Australian court decisions. 
2. DO NOT include: international news even if it involves Australians, crime stories, local accidents, industry stories unless they involve major national policy, anything that isn't directly about Australian politics or significant domestic affairs.
3. For each story write a plain English headline that states the actual fact — not a teaser. Tell me exactly what happened. E.g. "Albanese government's housing bill fails in Senate after Greens withdraw support" not "Albanese faces critical moment on housing". Be specific and factual.
4. Estimate how long ago (e.g. "3 hours ago", "yesterday", "2 days ago").
5. Aim for 2-4 stories. If nothing genuinely meets the bar, return [].

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","deeper_context":false,"context_angle":"","articles":[{{"title":"...","source":"...","url":"..."}}]}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        if story.get("deeper_context") and story.get("context_angle"):
            search_prompt = f"""Search for context on this Australian news story: "{story['context_angle']}"
Return ONLY a JSON array of up to 3 relevant articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_claude_with_search(search_prompt, 600)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                story["articles"] = story.get("articles", []) + extra
            except:
                pass
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "articles": story.get("articles", [])
        })
    return results

def process_archaeology(articles):
    if not articles:
        return []
    headlines = "\n".join([f"- {a['title']} ({a['source']})" for a in articles[:20]])
    prompt = f"""You are a science editor specialising in human origins. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent headlines:
{headlines}

Your job:
1. Select only significant archaeological or palaeoanthropological discoveries — homo lineage finds, ancient DNA breakthroughs, new fossil species, findings that challenge or rewrite human evolution. Aim for 2-4 stories ordered by significance.
2. For each story write a plain English headline explaining what was actually discovered and what it means for our understanding of human evolution — not the journal title, the actual finding and its significance.
3. Estimate how long ago announced (e.g. "2 weeks ago", "last month", "3 days ago").

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","articles":[{{"title":"...","source":"...","url":"..."}}]}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt)
    try:
        return json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

def process_football(articles):
    if not articles:
        return []
    headlines = "\n".join([f"- {a['title']} ({a['source']})" for a in articles[:30]])
    prompt = f"""You are a football editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent headlines from BBC Sport:
{headlines}

Your job:
1. Select only significant stories from Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League — significant match results and upsets, key player injuries, manager sackings, confirmed transfers, extraordinary performances. Aim for 4-6 stories.
2. For each story write a plain English headline explaining what happened — actual scorelines, actual player names, actual significance.
3. Estimate how long ago (e.g. "2 hours ago", "yesterday").
4. Flag if the story connects to a broader narrative worth exploring (true/false) and if so what angle (e.g. "Mbappe performance connects to Golden Boot race").

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","deeper_context":false,"context_angle":"","articles":[{{"title":"...","source":"...","url":"..."}}]}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        if story.get("deeper_context") and story.get("context_angle"):
            search_prompt = f"""Search for context on this football story: "{story['context_angle']}"
Return ONLY a JSON array of up to 3 relevant articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
            search_text = call_claude_with_search(search_prompt, 600)
            try:
                extra = json.loads(search_text.replace("```json","").replace("```","").strip())
                story["articles"] = story.get("articles", []) + extra
            except:
                pass
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "articles": story.get("articles", [])
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
                art_html = "".join([
                    f'<a href="{a.get("url","#")}" target="_blank" rel="noreferrer noopener" style="display:flex;align-items:baseline;justify-content:space-between;gap:10px;padding:8px 11px;border-radius:8px;background:#1a1a18;text-decoration:none;margin-bottom:3px;">'
                    f'<span style="font-size:13px;color:#f0ece4;line-height:1.4;flex:1;font-weight:300;">{a.get("title","").replace("<","&lt;").replace(">","&gt;")}</span>'
                    f'<span style="font-size:11px;color:#333330;white-space:nowrap;flex-shrink:0;">{a.get("source","")}</span>'
                    f'</a>'
                    for a in arts if a.get("url","").startswith("http")
                ])
                stories_html += f'''
<div style="border-bottom:1px solid rgba(255,255,255,0.07);">
  <div onclick="this.parentElement.classList.toggle('open');this.querySelector('.bar').style.background=this.parentElement.classList.contains('open')?'{ac}':'rgba(255,255,255,0.12)';this.querySelector('.chev').style.transform=this.parentElement.classList.contains('open')?'rotate(180deg)':'none';"
    style="display:flex;align-items:flex-start;gap:14px;padding:16px 0;cursor:pointer;">
    <span style="font-size:11px;color:#333330;min-width:18px;margin-top:4px;">{num}</span>
    <div style="flex:1;">
      <div style="font-size:15px;font-weight:400;line-height:1.5;color:#f0ece4;margin-bottom:5px;letter-spacing:-0.01em;">{story["headline"]}</div>
      <div style="font-size:11px;color:#6e6b64;">
        {f'<span style="color:{ac};font-weight:500;">{story.get("timestamp","")}</span>' if story.get("timestamp") else ""}
        {f'<span style="display:inline-block;width:3px;height:3px;border-radius:50%;background:#333330;margin:0 6px 1px;"></span>' if story.get("timestamp") and arts else ""}
        {arts[0].get("source","") if arts else ""}
        {f'<span style="display:inline-block;width:3px;height:3px;border-radius:50%;background:#333330;margin:0 6px 1px;"></span><span>{len(arts)} sources</span>' if len(arts) > 1 else ""}
      </div>
    </div>
    <div class="chev" style="font-size:10px;color:#333330;margin-top:5px;flex-shrink:0;transition:transform 0.2s;">&#9660;</div>
    <div class="bar" style="position:absolute;width:3px;height:20px;border-radius:2px;background:rgba(255,255,255,0.12);margin-top:3px;display:none;"></div>
  </div>
  <div style="display:none;padding:0 0 18px 32px;" class="story-body">
    <div style="font-size:11px;color:#6e6b64;margin-bottom:12px;">{story.get("timestamp","")}</div>
    {art_html}
  </div>
</div>'''

        sections_html += f'''
<div style="margin-bottom:4rem;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.25rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.07);">
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="width:3px;height:22px;border-radius:2px;background:{ac};flex-shrink:0;"></div>
      <div style="font-family:'Playfair Display',serif;font-size:1.2rem;font-weight:500;">{cat["label"]}</div>
    </div>
    <span style="font-size:11px;color:#333330;">Updated {updated_str}</span>
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
.open .story-body{{display:block!important;}}
</style>
</head>
<body>
<div style="max-width:860px;margin:0 auto;padding:3rem 2rem 6rem;">
  <div style="margin-bottom:3.5rem;">
    <div style="font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#6e6b64;margin-bottom:10px;">{date_str}</div>
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:12px;">
      <h1 style="font-family:'Playfair Display',serif;font-size:2.8rem;font-weight:700;letter-spacing:-0.02em;line-height:1.1;">Your briefing</h1>
      <span style="font-size:11px;color:#333330;padding-bottom:8px;">Auto-updates every 30–60 min</span>
    </div>
  </div>
  {sections_html}
</div>
<script>
document.querySelectorAll('[onclick]').forEach(el=>{{
  el.addEventListener('click',function(){{
    const body=this.parentElement.querySelector('.story-body');
    const bar=this.querySelector('.bar');
    if(body)body.style.display=body.style.display==='block'?'none':'block';
  }});
}});
</script>
</body>
</html>'''

def main():
    print("Fetching Breaking News from Guardian...")
    guardian_articles = fetch_guardian("world news war crisis disaster attack geopolitical", 20)
    breaking = process_breaking_news(guardian_articles)
    time.sleep(60)

    print("Fetching Australia news...")
    abc_rss = fetch_rss("https://www.abc.net.au/news/feed/51120/rss.xml", "ABC News")
    newsdata_aus = fetch_newsdata("australia politics government election", country="au")
    australia = process_australia(abc_rss, newsdata_aus)
    time.sleep(60)

    print("Fetching Archaeology news...")
    newsdata_arch = fetch_newsdata("archaeology paleoanthropology fossil discovery human evolution ancient DNA homo")
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
