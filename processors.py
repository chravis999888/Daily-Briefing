import json
import time
from datetime import datetime, timedelta

from api import (call_sonnet, call_haiku, call_haiku_with_search, call_sonnet_with_search,
                 format_articles_for_prompt, get_ai_summary, relative_time, AEST)
from memory import (get_cached_summary, save_summary, find_related_cached_stories, save_trend_topics)
from fetchers import fetch_world_topic_sources

HEADLINE_RULES = """
CRITICAL HEADLINE RULES:
- Write a single sentence stating the actual specific fact. Reader must be fully informed without clicking.
- Include real names, real numbers, real outcomes.
- NEVER use: "faces", "races against time", "sparks debate", "raises concerns", "under pressure", "amid tensions", "could impact", "warns of", "signals", "eyes", "targets", "mulls", "critical moment", "decisive", "implications"
- BAD: "Manchester City and Arsenal face critical final month with title implications"
- GOOD: "Manchester City lead Arsenal by 2 points with 5 games remaining as Premier League title race enters final stretch"
The headline must be a factual summary with specific details, not a news teaser.
"""


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
        stories = json.loads(text.replace("```json", "").replace("```", "").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in all_articles if a["url"] == story.get("url", "")), {})
        articles_list = [{"title": orig.get("title", "") or story.get("headline", ""), "source": story.get("source", ""), "url": story.get("url", "")}]
        context = ""
        if story.get("deeper_search") or story.get("so_what"):
            search_q = story.get("so_what") or story["headline"]
            cached_sources = find_related_cached_stories(memory, search_q)
            if cached_sources:
                print(f"Using cached context for: {search_q}")
                articles_list = articles_list + cached_sources
                context = story.get("so_what", "")
            else:
                search_prompt = f"""Search for latest news and context about: "{search_q}"
Return ONLY JSON array of up to 5 articles:
[{{"title":"...","source":"...","url":"https://..."}}]
Raw JSON only."""
                search_text = call_sonnet_with_search(search_prompt, 800)
                try:
                    extra = json.loads(search_text.replace("```json", "").replace("```", "").strip())
                    articles_list = extra
                    context = story.get("so_what", "")
                except:
                    pass
        ts = relative_time(orig.get("time", ""))
        if not ts:
            ts = relative_time(story.get("timestamp", ""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content", ""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image", ""),
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
        stories = json.loads(text.replace("```json", "").replace("```", "").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in all_articles if a["url"] == story.get("url", "")), {})
        articles_list = [{"title": orig.get("title", ""), "source": story.get("source", ""), "url": story.get("url", "")}]
        context = story.get("so_what", "")
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
                    extra = json.loads(search_text.replace("```json", "").replace("```", "").strip())
                    articles_list = articles_list + extra
                except:
                    pass
        ts = relative_time(orig.get("time", ""))
        if not ts:
            ts = relative_time(story.get("timestamp", ""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content", ""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image", ""),
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
        stories = json.loads(text.replace("```json", "").replace("```", "").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in articles if a["url"] == story.get("url", "")), {})
        articles_list = [{"title": orig.get("title", ""), "source": story.get("source", ""), "url": story.get("url", "")}]
        context = story.get("so_what", "")
        ts = relative_time(orig.get("time", ""))
        if not ts:
            ts = relative_time(story.get("timestamp", ""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content", ""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image", ""),
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
        stories = json.loads(text.replace("```json", "").replace("```", "").strip())
    except:
        return [], memory

    stories.sort(key=lambda x: x.get("score", 5), reverse=True)
    results = []
    for story in stories:
        time.sleep(3)
        orig = next((a for a in articles if a["url"] == story.get("url", "")), {})
        articles_list = [{"title": orig.get("title", ""), "source": story.get("source", "The Guardian"), "url": story.get("url", "")}]
        context = story.get("so_what", "")
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
                    extra = json.loads(search_text.replace("```json", "").replace("```", "").strip())
                    articles_list = articles_list + extra
                    context = story["so_what"]
                except:
                    pass
        ts = relative_time(orig.get("time", ""))
        if not ts:
            ts = relative_time(story.get("timestamp", ""))
        url = story.get("url", "")
        summary = get_cached_summary(memory, url)
        if not summary:
            summary, suggestions = get_ai_summary(story["headline"], orig.get("content", ""), context)
            memory = save_summary(memory, url, summary)
        else:
            suggestions = []
        results.append({
            "headline": story["headline"],
            "score": story.get("score", 5),
            "timestamp": ts,
            "summary": summary,
            "url": url,
            "image": orig.get("image", ""),
            "articles": articles_list,
            "tracking_suggestions": suggestions
        })
    return results, memory


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
            results["today"] = json.loads(text.replace("```json", "").replace("```", "").strip())
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
        return json.loads(text.replace("```json", "").replace("```", "").strip())
    except:
        return []


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
        updates = json.loads(text.replace("```json", "").replace("```", "").strip())
    except:
        updates = []

    # Index Claude's output by topic for lookup
    updates_by_topic = {u.get("topic", "").lower(): u for u in updates}

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
