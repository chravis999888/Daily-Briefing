"""Cost ledger: append-only cost_log.json rows plus daily / month-to-date rollups."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pricing import compute_cost, USD_TO_AUD

AEST = timezone(timedelta(hours=10))
LEDGER_FILE = Path("cost_log.json")
SUMMARY_FILE = Path("COST_SUMMARY.md")
RUN_MODE = os.environ.get("RUN_MODE", "full")
RUN_ID = os.environ.get("GITHUB_RUN_ID") or datetime.now(AEST).strftime("%Y%m%dT%H%M%S")
WASTED_OUTCOMES = ("truncated", "empty")

_CATEGORY_HINTS = [("breaking", "breaking"), ("australia", "australia"), ("archaeology", "archaeology"),
                   ("football", "football"), ("world_topics", "world_topics"),
                   ("developing", "developing"), ("story_summary", "summary")]


def infer_category(label):
    for hint, cat in _CATEGORY_HINTS:
        if hint in label:
            return cat
    return "other"


def _usage_fields(usage):
    stu = getattr(usage, "server_tool_use", None)
    searches = getattr(stu, "web_search_requests", 0) if stu else 0
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "web_search_requests": searches or 0,
    }


def _append(record):
    try:
        existing = json.loads(LEDGER_FILE.read_text()) if LEDGER_FILE.exists() else []
    except Exception:
        existing = []
    existing.append(record)
    LEDGER_FILE.write_text(json.dumps(existing, indent=2))


def log_call(label, model, msg=None, error=None, category=None, has_text=None):
    """Write exactly one ledger row for one API attempt.

    Success: pass the response `msg`; tokens/stop_reason come from msg.usage / msg.stop_reason,
    cost from pricing.py (raises UnknownModelError for an unpriced model).
    Failure: pass `error`; the row has zero cost.
    has_text: whether the caller got usable text out of the response (False -> outcome "empty").
    """
    record = {
        "timestamp": datetime.now(AEST).isoformat(),
        "run_id": RUN_ID,
        "run_type": RUN_MODE,
        "label": label,
        "category": category or infer_category(label),
        "model": model,
    }
    if error is not None:
        record.update({
            "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0, "web_search_requests": 0,
            "stop_reason": None, "outcome": "error",
            "error": f"{type(error).__name__}: {error}"[:300], "cost_usd": 0.0,
        })
    else:
        record["model"] = msg.model
        fields = _usage_fields(msg.usage)
        stop = getattr(msg, "stop_reason", None)
        if stop == "max_tokens":
            outcome = "truncated"
        elif not has_text:
            outcome = "empty"
        else:
            outcome = "ok"
        record.update(fields)
        record.update({
            "stop_reason": stop, "outcome": outcome, "error": None,
            "cost_usd": round(compute_cost(msg.model, **fields), 8),
        })
    _append(record)
    return record


def _bucket(rows, key):
    out = {}
    for r in rows:
        b = out.setdefault(str(r.get(key)), {"calls": 0, "usd": 0.0})
        b["calls"] += 1
        b["usd"] += r["cost_usd"]
    return {k: {"calls": v["calls"], "usd": round(v["usd"], 6)} for k, v in sorted(out.items())}


def _summarise(rows):
    total = sum(r["cost_usd"] for r in rows)
    wasted = sum(r["cost_usd"] for r in rows if r["outcome"] in WASTED_OUTCOMES)
    return {
        "calls": len(rows),
        "total_usd": round(total, 6), "total_aud": round(total * USD_TO_AUD, 6),
        "wasted_usd": round(wasted, 6), "wasted_aud": round(wasted * USD_TO_AUD, 6),
        "by_step": _bucket(rows, "label"), "by_category": _bucket(rows, "category"),
        "by_model": _bucket(rows, "model"), "by_outcome": _bucket(rows, "outcome"),
    }


def load_rows():
    try:
        rows = json.loads(LEDGER_FILE.read_text()) if LEDGER_FILE.exists() else []
    except Exception:
        rows = []
    return rows


def build_rollup(now=None):
    """Daily + month-to-date rollup (AEST). Pre-ledger rows (no `outcome`) are excluded:
    they were priced with the wrong Haiku rate and carry no search/stop_reason data."""
    now = now or datetime.now(AEST)
    rows = load_rows()
    ledger = [r for r in rows if "outcome" in r]
    today, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    return {
        "as_of": now.isoformat(),
        "aud_rate_display_only": USD_TO_AUD,
        "legacy_rows_excluded": len(rows) - len(ledger),
        "today": {"date": today, **_summarise([r for r in ledger if r["timestamp"][:10] == today])},
        "month_to_date": {"month": month, **_summarise([r for r in ledger if r["timestamp"][:7] == month])},
    }, ledger


def write_summary_md(rollup, ledger):
    t, m = rollup["today"], rollup["month_to_date"]
    fx = rollup["aud_rate_display_only"]
    lines = [
        "# API Cost Summary",
        f"_Updated {rollup['as_of']} (AEST). USD is exact; AUD = USD x {fx} (conversion only)._",
        "",
        f"- **Today ({t['date']}):** ${t['total_usd']:.4f} USD (A${t['total_aud']:.4f}) over {t['calls']} calls",
        f"- **Month to date ({m['month']}):** ${m['total_usd']:.4f} USD (A${m['total_aud']:.4f}) over {m['calls']} calls",
        f"- **Wasted spend (truncated/empty) today:** ${t['wasted_usd']:.4f} USD (A${t['wasted_aud']:.4f})",
        f"- **Wasted spend month to date:** ${m['wasted_usd']:.4f} USD (A${m['wasted_aud']:.4f})",
        "",
        "## Last 10 calls",
        "| Time | Step | Model | Cost (USD) | Outcome |",
        "|---|---|---|---|---|",
    ]
    for r in reversed(ledger[-10:]):
        lines.append(f"| {r['timestamp'][:19]} | {r['label']} | {r['model']} | ${r['cost_usd']:.6f} | {r['outcome']} |")
    if not ledger:
        lines.append("| — | no calls logged yet | | | |")
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_cost_outputs(health):
    """Call once per run before save_health: adds health['api_cost_ledger'], writes COST_SUMMARY.md."""
    rollup, ledger = build_rollup()
    health["api_cost_ledger"] = rollup
    write_summary_md(rollup, ledger)
    return health
