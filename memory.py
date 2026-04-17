import json
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
        {"headline": s["headline"], "timestamp": s.get("timestamp", ""), "score": s.get("score", 5),
         "summary": s.get("summary", ""), "url": s.get("url", ""), "image": s.get("image", ""),
         "articles": s.get("articles", []), "tracking_suggestions": s.get("tracking_suggestions", [])}
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
    titles = sorted([a.get("title", "") for a in articles])
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
    return get_previous_stories(memory, category)


def get_cached_summary(memory, url):
    return memory.get("summaries", {}).get(url)


def save_summary(memory, url, summary):
    if "summaries" not in memory:
        memory["summaries"] = {}
    memory["summaries"][url] = summary
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
                    url = next((a.get("url", "") for a in s.get("articles", []) if a.get("url", "")), "")
                    if url:
                        sources.append({"title": headline, "source": "Previously covered", "url": url})
    return sources[:4] if sources else None


def save_trend_topics(memory, topics):
    today = datetime.now(AEST).strftime("%Y-%m-%d")
    if "world_trends" not in memory:
        memory["world_trends"] = {}
    memory["world_trends"][today] = topics
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
