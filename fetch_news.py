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
CRITICAL HEADLINE RULES — these are non-negotiable:
- The headline must state the ACTUAL FACT. It is a one-sentence summary that fully informs the reader without them needing to click.
- NEVER use vague phrases like "faces critical moment", "races against time", "sparks debate", "raises concerns", "under pressure", "amid tensions", "could impact", "warns of", "signals", "eyes", "targets", "mulls".
- NEVER write a headline that teases without informing. If you can't tell what actually happened from the headline alone, rewrite it.
- BAD: "Prime Minister faces critical moment as government races against time on key policy"
- GOOD: "Albanese's Help to Buy housing scheme passes Senate after Greens back amended bill"
- BAD: "Scientists make breakthrough discovery that could rewrite human history"  
- GOOD: "450,000-year-old skull fragment found in Israel identified as new Homo species distinct from Neanderthals"
- BAD: "Club faces uncertain future amid managerial uncertainty"
- GOOD: "Thomas Tuchel sacked by Bayern Munich after 3 consecutive Bundesliga losses"
The headline must read like a factual summary, not a news article title designed to get clicks.
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

def get_ai_summary(headline, context=""):
    prompt = f"""In 3-4 sentences, explain this news story clearly and factually:
"{headline}"
{f'Additional context: {context}' if context else ''}

Cover: what happened, why it matters, any important background context.
Write in plain English. Be direct and informative. No fluff, no "it remains to be seen", no speculation."""
    return call_claude(prompt, 400)

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
    params = {"apikey": NEWSDATA_KEY, "q": query, "language": "en"}
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
    prompt = f"""You are a world news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent headlines from The Guardian:
{headlines}

Select ONLY stories that are historic in scale — active major wars significantly escalating with large casualties, world leader deaths, terrorist attacks killing hundreds+, catastrophic natural disasters with mass casualties, nuclear threats, geopolitical shocks that will be studied in history books. 

DO NOT include: diplomatic talks, ceasefire negotiations, court cases, political scandals, economic news, warnings or concerns from officials, institutional crises. Only include something if it involves large-scale violence, death, or an irreversible world-changing event that is actively happening right now.

If nothing meets this bar, return [].

{HEADLINE_RULES}

Also estimate how long ago this broke and whether it needs a deeper multi-source search.

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
        summary = get_ai_summary(story["headline"])
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
    headlines = "\n".join([f"- {a['title']} ({a['source']})" for a in all_articles[:30]])
    prompt = f"""You are an Australian political news editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent headlines:
{headlines}

Select ONLY stories about Australian domestic politics and significant national affairs:
- Federal or state parliament votes, bills passed or failed, budget decisions
- Federal or state election news, polling shifts, party leadership changes
- Major Australian court decisions with national significance (like High Court rulings)
- Significant national policy changes that affect most Australians
- Major national cultural or social events with political significance

DO NOT include: international news (even if Australians are involved), road accidents, local crimes, industry news, sport, weather, human interest stories, anything a tabloid would run. If a story isn't about Australian governance or national politics, exclude it.

Aim for 2-4 stories. If nothing meets the bar return [].

{HEADLINE_RULES}

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
        summary = get_ai_summary(story["headline"])
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "summary": summary,
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

Select ONLY significant archaeological or palaeoanthropological discoveries:
- New hominin species or subspecies identified
- Fossil finds that push back dates of human evolution or migration
- Ancient DNA findings that change our understanding of human lineage
- Discoveries that directly contradict or significantly update existing models of human evolution
- Major finds related to Homo sapiens, Neanderthals, Denisovans, Homo erectus, or other Homo lineage species

DO NOT include: general archaeology not related to human evolution, dinosaur finds, ancient civilisation discoveries, cultural artefacts, unless they directly relate to hominin evolution.

Aim for 2-4 stories ordered by significance. If nothing meets the bar return [].

{HEADLINE_RULES}

Return ONLY a JSON array:
[{{"headline":"...","timestamp":"...","articles":[{{"title":"...","source":"...","url":"..."}}]}}]
Raw JSON only, no markdown."""

    text = call_claude(prompt)
    try:
        stories = json.loads(text.replace("```json","").replace("```","").strip())
    except:
        return []

    results = []
    for story in stories:
        summary = get_ai_summary(story["headline"])
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "summary": summary,
            "articles": story.get("articles", [])
        })
    return results

def process_football(articles):
    if not articles:
        return []
    headlines = "\n".join([f"- {a['title']} ({a['source']})" for a in articles[:30]])
    prompt = f"""You are a football editor. Today is {datetime.now(AEST).strftime('%A %d %B %Y')}.

Here are recent headlines from BBC Sport:
{headlines}

Select ONLY significant stories from Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League:
- Match results with actual scorelines, especially upsets or title-race implications
- Key player injuries affecting a team's season
- Manager sackings or confirmed appointments
- Confirmed major transfers
- Extraordinary individual performances with stats
- Significant title race developments, relegation battles, European qualification

DO NOT include: transfer rumours, minor injuries, press conference opinions, preview articles, fantasy football content.

Aim for 4-6 stories. If nothing meets the bar return [].

{HEADLINE_RULES}

Also flag if a story connects to a broader narrative worth exploring (e.g. a performance connecting to a Golden Boot race, a result changing the title standings).

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
        summary = get_ai_summary(story["headline"])
        results.append({
            "headline": story["headline"],
            "timestamp": story.get("timestamp",""),
            "summary": summary,
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
                summary = story.get("summary", "").replace("'", "&#39;").replace('"', "&quot;")
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
    <div style="font-size:13px;line-height:1.7;color:#8a8680;background:#1a1a18;border-left:2px solid {ac};padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:14px;font-style:italic;">{story.get("summary","").replace("<","&lt;").replace(">","&gt;")}</div>
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
    guardian_articles = fetch_guardian("world war attack disaster crisis killed invasion", 20)
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
