import os
import re
import json
import time
import anthropic
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime

AEST = timezone(timedelta(hours=10))
RUN_MODE = os.environ.get("RUN_MODE", "full")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None


def log_api_call(label: str, model: str, input_tokens: int, output_tokens: int):
    """Append one API call record to cost_log.json. Keeps last 1000 entries."""
    HAIKU_IN   = 0.80  / 1_000_000
    HAIKU_OUT  = 4.00  / 1_000_000
    SONNET_IN  = 3.00  / 1_000_000
    SONNET_OUT = 15.00 / 1_000_000

    if "haiku" in model.lower():
        cost_usd = (input_tokens * HAIKU_IN) + (output_tokens * HAIKU_OUT)
    else:
        cost_usd = (input_tokens * SONNET_IN) + (output_tokens * SONNET_OUT)

    record = {
        "timestamp": datetime.now(AEST).isoformat(),
        "run_type": RUN_MODE,
        "label": label,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6)
    }

    log_path = Path("cost_log.json")
    try:
        existing = json.loads(log_path.read_text()) if log_path.exists() else []
    except Exception:
        existing = []

    existing.append(record)
    if len(existing) > 1000:
        existing = existing[-1000:]

    log_path.write_text(json.dumps(existing, indent=2))


def relative_time(date_str):
    if not date_str:
        return ""
    try:
        dt = None
        for parser in [
            lambda s: datetime.fromisoformat(s),
            lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
            lambda s: parsedate_to_datetime(s),
            lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z"),
            lambda s: datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %z"),
            lambda s: datetime.strptime(s, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc),
            lambda s: datetime.strptime(s[:25], "%Y-%m-%dT%H:%M:%S+00:00").replace(tzinfo=timezone.utc),
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


def call_haiku(prompt, max_tokens=500, label="haiku"):
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
    return msg.content[0].text


def call_sonnet(prompt, max_tokens=1000, retries=3, label="sonnet"):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
            return msg.content[0].text
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"Sonnet rate limit, waiting {wait}s (attempt {attempt+1}/{retries})...")
            time.sleep(wait)
        except Exception as e:
            print(f"Sonnet error: {e}")
            break
    print("Falling back to Haiku...")
    return call_haiku(prompt, max_tokens, label="sonnet_haiku_fallback")


def call_sonnet_with_search(prompt, max_tokens=1500, retries=3, label="context_search_sonnet"):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": prompt}]
            )
            log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
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


def call_haiku_with_search(prompt, max_tokens=500, label="context_search"):
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        log_api_call(label, msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
        for block in msg.content:
            if block.type == "text":
                return block.text
        return ""
    except Exception as e:
        print(f"Haiku search error: {e}")
        return ""


def get_ai_summary(headline, content="", context=""):
    prompt = f"""In 3-4 sentences, explain this news story clearly and factually.
Headline: "{headline}"
{f'Article content: {content[:1200]}' if content else ''}
{f'Additional context: {context}' if context else ''}
Cover what happened, why it matters, and any important background or broader significance. Plain English, no fluff.

Also suggest 3-4 short trackable topic labels for this story, from specific to broad (2-5 words each).

Return a JSON object:
{{"summary": "3-4 sentence explanation...", "tracking_suggestions": ["specific topic", "broader topic", "wider context"]}}
Raw JSON only, no markdown."""
    text = call_sonnet(prompt, 400, label="story_summary")
    try:
        data = json.loads(text.replace("```json", "").replace("```", "").strip())
        summary = re.sub(r'^#+\s*\w*\s*', '', str(data.get("summary", ""))).strip()
        suggestions = data.get("tracking_suggestions", [])
        if not isinstance(suggestions, list):
            suggestions = []
        return summary, suggestions
    except Exception:
        summary = re.sub(r'^#+\s*\w*\s*', '', text).strip()
        return summary, []


def format_articles_for_prompt(articles, limit=25, titles_only=False):
    parts = []
    for a in articles[:limit]:
        rel = relative_time(a.get("time", ""))
        time_str = f"PUBLISHED: {rel}\n" if rel else ""
        if titles_only:
            parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}URL: {a['url']}")
        else:
            content = a.get("content", "").strip()
            if content:
                parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}CONTENT: {content[:600]}\nURL: {a['url']}")
            else:
                parts.append(f"SOURCE: {a['source']}\nTITLE: {a['title']}\n{time_str}URL: {a['url']}")
    return "\n---\n".join(parts)
