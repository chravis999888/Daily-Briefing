"""Article identity and "what's new": permanent IDs, the article store in memory.json, near-duplicate titles.
Pure functions over plain dicts; standard library only. No network, no Claude."""
import hashlib
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from safety import AEST, parse_ts

STORE_KEY = "article_store"
STORE_RETENTION_DAYS = 7
DUPLICATE_WINDOW_HOURS = 48
DUPLICATE_THRESHOLD = 0.8      # token-set Jaccard on normalised titles; conservative on purpose
MIN_DUPLICATE_TOKENS = 4       # shorter titles are never merged (too easy to collide)

TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "ref", "ref_src", "ref_url", "cmpid",
                   "cmp", "mc_cid", "mc_eid", "ocid", "ito", "_ga", "spm", "at_medium", "at_campaign",
                   "ns_mchannel", "ns_source", "ns_campaign", "ns_linkname"}
TRACKING_PREFIXES = ("utm_",)

STOP_WORDS = {"a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for", "from", "by", "with", "as",
              "is", "are", "was", "were", "be", "been", "it", "its", "that", "this", "after", "over", "into",
              "says", "say", "said"}


def canonical_url(url):
    """Lowercase scheme+host, no fragment, no tracking params, remaining params sorted, no trailing slash."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES))
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def article_id(url, fallback=""):
    """sha256 of the canonical URL, first 16 hex chars. `fallback` (title+source) is used only if there is no URL."""
    key = canonical_url(url) or (fallback or "").strip().lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_published(value):
    """Best-effort parse of a feed/API time string to ISO-8601 UTC; '' if it cannot be parsed."""
    if not value:
        return ""
    value = str(value).strip()
    dt = None
    for parse in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
                  parsedate_to_datetime,
                  lambda s: datetime.strptime(s[:15], "%Y%m%dT%H%M%S")):   # GDELT seendate
        try:
            dt = parse(value)
            break
        except Exception:
            continue
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def finalize_articles(articles):
    """The one shared place every fetcher goes through: give each article an id and a parsed published time."""
    for a in articles:
        if not a.get("published"):
            a["published"] = parse_published(a.get("time", ""))
        a["id"] = article_id(a.get("url", ""), f"{a.get('title', '')}|{a.get('source', '')}")
    return articles


# ---- near-duplicate titles -------------------------------------------------------------------

_SUFFIX = re.compile(r"\s+[-–—|]\s+([^-–—|]+)$")


def normalise_title(title):
    """Token set: lowercase, trailing ' - Outlet' / ' | Outlet' removed, punctuation stripped, stop-words dropped."""
    t = (title or "").lower().strip()
    m = _SUFFIX.search(t)
    if m and len(m.group(1).split()) <= 4:
        t = t[:m.start()]
    t = re.sub(r"[^\w\s]", " ", t)
    return frozenset(w for w in t.split() if w not in STOP_WORDS)


def titles_match(tokens_a, tokens_b, threshold=DUPLICATE_THRESHOLD):
    """Same story? Needs enough words, matching numbers (2-1 is not 3-1) and Jaccard >= threshold."""
    if len(tokens_a) < MIN_DUPLICATE_TOKENS or len(tokens_b) < MIN_DUPLICATE_TOKENS:
        return False
    if {w for w in tokens_a if any(c.isdigit() for c in w)} != {w for w in tokens_b if any(c.isdigit() for c in w)}:
        return False
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b) >= threshold


# ---- the article store -----------------------------------------------------------------------

def _in_category(rec, category):
    return rec.get("category") == category or category in rec.get("also_in", ())


def prune_store(memory, now=None, days=STORE_RETENTION_DAYS):
    """Drop records first seen more than `days` ago (or with no usable first_seen)."""
    now = now or datetime.now(AEST)
    store = memory.get(STORE_KEY)
    if not isinstance(store, dict):
        memory[STORE_KEY] = {}
        return memory
    cutoff = now - timedelta(days=days)
    memory[STORE_KEY] = {i: r for i, r in store.items()
                         if isinstance(r, dict) and (parse_ts(r.get("first_seen")) or cutoff - timedelta(1)) >= cutoff}
    return memory


def classify_articles(memory, category, articles, now=None):
    """Split this category's fetched articles into new and old, update the store, flag near-duplicates.

    Annotates each article dict in place: id, is_new (bool), duplicate_of (id, if a duplicate).
    Returns {"fetched", "new", "duplicates", "has_new", "new_by_source"}. Nothing is removed from `articles`.
    A record already in the store under another category counts as new for this one (a story may belong to two).
    """
    now = now or datetime.now(AEST)
    iso = now.isoformat()
    store = memory.setdefault(STORE_KEY, {})
    window_start = now - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    # duplicate records are not anchors: a chain of near-matches must not stretch the 48h window
    stored_tokens = [(i, normalise_title(r.get("title", ""))) for i, r in store.items()
                     if _in_category(r, category) and not r.get("duplicate_of") and (parse_ts(r.get("first_seen")) or now) >= window_start]

    for a in articles:
        a["id"] = a.get("id") or article_id(a.get("url", ""), f"{a.get('title', '')}|{a.get('source', '')}")
        a["is_new"] = False
        a.pop("duplicate_of", None)

    # earliest published first, so the earliest article is the one kept; unknown times go last, in fetch order
    order = sorted(range(len(articles)), key=lambda i: (articles[i].get("published") or "9999", i))
    kept_tokens, seen_ids, new, dups = [], set(), [], 0
    for idx in order:
        a = articles[idx]
        if a["id"] in seen_ids:
            dups += 1
            continue
        seen_ids.add(a["id"])
        rec = store.get(a["id"])
        if rec is not None and _in_category(rec, category):
            continue                                            # already seen: old
        tokens = normalise_title(a.get("title", ""))
        original = next((i for i, t in stored_tokens + kept_tokens if titles_match(tokens, t)), None)
        record = {"source": a.get("source", ""), "title": (a.get("title") or "")[:200],
                  "published": a.get("published", ""), "first_seen": iso, "category": category}
        if original:
            a["duplicate_of"] = original
            record["duplicate_of"] = original
            dups += 1
        else:
            a["is_new"] = True
            new.append(a)
            kept_tokens.append((a["id"], tokens))
        if rec is not None:                                     # seen under another category: share the record
            rec.setdefault("also_in", []).append(category)
        else:
            store[a["id"]] = record
    by_source = {}
    for a in new:
        by_source[a.get("source") or "?"] = by_source.get(a.get("source") or "?", 0) + 1
    return {"fetched": len(articles), "new": len(new), "duplicates": dups, "has_new": bool(new),
            "new_by_source": dict(sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0])))}


def restore_store_category(memory, original, category):
    """Undo this run's store additions for one category (its Claude step failed) so it counts as new next run."""
    before = (original or {}).get(STORE_KEY) or {}
    store = memory.setdefault(STORE_KEY, {})
    for i in list(store):
        rec = store[i]
        if i in before and _in_category(before[i], category):
            continue
        if rec.get("category") == category:
            if rec.get("also_in"):
                rec["category"] = rec["also_in"].pop(0)
            else:
                del store[i]
        elif category in rec.get("also_in", ()):
            rec["also_in"] = [c for c in rec["also_in"] if c != category]
            if not rec["also_in"]:
                rec.pop("also_in")
    return memory
