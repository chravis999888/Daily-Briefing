import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from safety import STALE_AFTER_HOURS, is_stale, parse_ts

AEST = timezone(timedelta(hours=10))

ACCENTS = {
    "breaking": "#c0392b",
    "australia": "#2e7bbf",
    "archaeology": "#b07d2a",
    "football": "#2a7a52",
    "world": "#7b68c8",
    "developing": "#2a7a6e"
}


def safe_url(url):
    """Return url only if it is an absolute http(s) URL, else ''."""
    url = str(url or "").strip()
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    return url if parsed.scheme in ("http", "https") and parsed.netloc else ""


def _clean_articles(articles):
    return [{**a, "url": safe_url(a.get("url"))} for a in articles or []]


def _clean_stories(stories):
    """Restrict story image and article URLs to http(s) before rendering."""
    cleaned = []
    for s in stories or []:
        s = dict(s)
        if "image" in s:
            s["image"] = safe_url(s["image"])
        if "url" in s:
            s["url"] = safe_url(s["url"])
        if "articles" in s:
            s["articles"] = _clean_articles(s["articles"])
        cleaned.append(s)
    return cleaned


def freshness(health, now):
    """(updated_str, stale_warning) from health['last_successful_data_update'] - never the build time."""
    last = (health or {}).get("last_successful_data_update")
    dt = parse_ts(last)
    if dt is None:
        return "unknown", "Data freshness unknown: no successful data update has been recorded."
    dt = dt.astimezone(AEST)
    text = dt.strftime("%I:%M %p AEST").lstrip("0")
    if dt.date() != now.date():
        text += dt.strftime(", %d %b")
    if not is_stale(last, now):
        return text, ""
    hours = int((now - dt).total_seconds() // 3600)
    return text, (f"Stale data: the last successful update was {hours} hours ago ({text}). "
                  f"Updates are failing, so the stories below may be out of date (warning after {STALE_AFTER_HOURS}h).")


def build_html(all_data, yesterday_data, developing_situations, health=None, now=None):
    now = now or datetime.now(AEST)
    date_str = now.strftime("%A %d %B %Y").upper()
    updated_str, stale_warning = freshness(health, now)
    build_ts = int(datetime.now(timezone.utc).timestamp())

    last_run = health["runs"][-1] if health and health.get("runs") else None
    if last_run:
        health_status = {
            "color": "#e67e22" if last_run.get("errors") else "#2ecc71",
            "tooltip": ("Issues: " + "; ".join(last_run["errors"][:3])) if last_run.get("errors") else "All sources OK",
        }
    else:
        health_status = None

    col_categories = [
        {
            "id": "australia",
            "label": "Australia",
            "ac": ACCENTS["australia"],
            "data": _clean_stories(all_data.get("australia", [])),
            "yesterday": _clean_stories(yesterday_data.get("australia", [])),
        },
        {
            "id": "archaeology",
            "label": "Archaeology & Palaeoanthropology",
            "ac": ACCENTS["archaeology"],
            "data": _clean_stories(all_data.get("archaeology", [])),
            "yesterday": _clean_stories(yesterday_data.get("archaeology", [])),
        },
        {
            "id": "football",
            "label": "Football",
            "ac": ACCENTS["football"],
            "data": _clean_stories(all_data.get("football", [])),
            "yesterday": _clean_stories(yesterday_data.get("football", [])),
        },
    ]

    env = Environment(loader=FileSystemLoader(Path(__file__).parent), autoescape=True)
    env.filters["urlencode_component"] = lambda s: urllib.parse.quote(str(s), safe="")

    template = env.get_template("template.html")
    return template.render(
        date_str=date_str,
        updated_str=updated_str,
        stale_warning=stale_warning,
        build_ts=build_ts,
        health_status=health_status,
        breaking=_clean_stories(all_data.get("breaking", [])),
        yesterday_breaking=_clean_stories(yesterday_data.get("breaking", [])),
        col_categories=col_categories,
        developing_situations=_clean_stories(developing_situations),
        accents=ACCENTS,
    )
