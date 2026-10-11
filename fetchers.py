import os
import re
import time
import urllib.parse
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from articles import finalize_articles, parse_published

NEWSDATA_KEY = os.environ.get("NEWSDATA_API_KEY", "")
GUARDIAN_KEY = os.environ.get("GUARDIAN_API_KEY", "")

FETCH_TIMEOUT = 10   # seconds, every outbound news fetch

# One (name, ok, message, count, seconds) event per fetch this run; fetch_news reads and clears it.
SOURCE_EVENTS = []


def record_source(name, ok, message="", count=None, seconds=None):
    SOURCE_EVENTS.append((name, bool(ok), message, count, seconds))


class FetchError(Exception):
    """A fetch problem with a ready-made message (logged as is, without the exception class name)."""


def timed_fetch(name, fn):
    """The one shared fetch routine: run fn() -> list of articles, time it, give every article its id and
    published time, and record success/count/seconds (or the error text). Never raises: a failing source
    returns [] so it cannot break the run."""
    start = time.monotonic()
    try:
        articles = finalize_articles(fn())
    except Exception as e:
        msg = str(e) if isinstance(e, FetchError) else f"{type(e).__name__}: {e}"
        print(f"{name} fetch error: {msg}")
        record_source(name, False, msg[:300], 0, round(time.monotonic() - start, 2))
        return []
    record_source(name, bool(articles), "" if articles else "0 articles returned", len(articles),
                  round(time.monotonic() - start, 2))
    return articles


def _get_feed(url):
    """Fetch a feed with a hard timeout (feedparser alone has none) and parse it."""
    r = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": feedparser.USER_AGENT})
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code} — {r.text[:200]}")
    return feedparser.parse(r.content)


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
        r = requests.get(base_url, params={**params, "format": "json"}, timeout=FETCH_TIMEOUT)
        snippet = (r.text or "")[:200]
        if r.status_code != 200:
            return [], f"GDELT fetch failed: HTTP {r.status_code} — {snippet}"
        try:
            data = r.json()
        except Exception as e:
            return [], f"GDELT fetch failed: HTTP {r.status_code}, JSON decode error ({e}) — {snippet}"
        return _parse_articles(data.get("articles", [])), ""

    def _attempt_rss():
        rss_params = {**params, "format": "rss"}
        feed = _get_feed(base_url + "?" + urllib.parse.urlencode(rss_params))
        articles = _parse_rss_articles(feed)
        return articles, "" if articles else "GDELT RSS returned empty"

    start = time.monotonic()

    def _done(articles, err):
        articles = finalize_articles(articles)
        record_source("GDELT", bool(articles), err or ("" if articles else "0 articles returned"),
                      len(articles), round(time.monotonic() - start, 2))
        return articles, err, memory

    # One JSON attempt (no retry loop), then the RSS fallback, each with the short timeout.
    err = "GDELT fetch failed: unknown"
    try:
        articles, err = _attempt_json()
        if articles:
            return _done(articles, "")
        print(f"GDELT JSON attempt: {err or 'empty'} — falling back to RSS")
    except Exception as e:
        err = f"GDELT fetch failed: {type(e).__name__}: {e}"
        print(f"GDELT JSON attempt exception: {e} — falling back to RSS")

    try:
        articles, rss_err = _attempt_rss()
        if articles:
            print(f"GDELT RSS fallback succeeded: {len(articles)} articles")
            return _done(articles, "")
        prefix = err if err else "GDELT fetch failed: empty response"
        print(f"GDELT RSS fallback: {rss_err}")
        return _done([], prefix + "; RSS fallback also empty")
    except Exception as e:
        print(f"GDELT RSS fallback exception: {e}")
        return _done([], err + f"; RSS fallback failed: {e}")


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

    def _fetch():
        r = requests.get(url, params=params, timeout=FETCH_TIMEOUT)
        data = r.json()
        articles = []
        for a in data.get("response", {}).get("results", []):
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
    return timed_fetch("Guardian", _fetch)


def _entry_published(entry):
    """ISO-UTC published time for a feed entry: published, else updated, else feedparser's parsed struct."""
    iso = parse_published(entry.get("published", "")) or parse_published(entry.get("updated", ""))
    if not iso:
        st = entry.get("published_parsed") or entry.get("updated_parsed")
        if st:
            iso = datetime(*st[:6], tzinfo=timezone.utc).isoformat()
    return iso


def fetch_rss(url, source_name):
    def _fetch():
        feed = _get_feed(url)
        articles = []
        for entry in feed.entries[:20]:
            summary = entry.get("summary", "") or entry.get("description", "")
            image = ""
            if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                image = entry.media_thumbnail[0].get("url", "")
            articles.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": source_name,
                "time": entry.get("published", ""),
                "published": _entry_published(entry),
                "content": re.sub(r'<[^>]+>', '', summary)[:1000],
                "image": image
            })
        return articles
    return timed_fetch(source_name, _fetch)


def fetch_newsdata(query, country=None):
    url = "https://newsdata.io/api/1/news"
    params = {"apikey": NEWSDATA_KEY, "q": query, "language": "en", "full_content": 1}
    if country:
        params["country"] = country

    def _fetch():
        r = requests.get(url, params=params, timeout=FETCH_TIMEOUT)
        results = r.json().get("results", [])
        if not isinstance(results, list):
            raise FetchError("unexpected response (no results list)")
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
    return timed_fetch("NewsData", _fetch)


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
