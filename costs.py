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

# ---- monthly spend cap (AUD, via the one USD_TO_AUD rate in pricing.py) ----
DEFAULT_MONTHLY_CAP_AUD = 15.0
WARN_FRACTION = 0.8        # first crossing alerts once
HARD_STOP_FACTOR = 1.2     # at/over 120% of the cap even Breaking is blocked
SKIP_REASONS = ("category_off", "cap", "claude_disabled")   # old skipped rows have no reason = claude_disabled


def monthly_cap_aud():
    """MONTHLY_CAP_AUD env (read at call time), default 15. A bad value falls back to the default."""
    try:
        cap = float(os.environ.get("MONTHLY_CAP_AUD", "").strip() or DEFAULT_MONTHLY_CAP_AUD)
    except ValueError:
        return DEFAULT_MONTHLY_CAP_AUD
    return cap if cap > 0 else DEFAULT_MONTHLY_CAP_AUD


def _month_of(timestamp):
    """YYYY-MM in Brisbane time for a ledger timestamp (any offset; naive is taken as Brisbane)."""
    try:
        dt = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return ""
    return (dt.replace(tzinfo=AEST) if dt.tzinfo is None else dt.astimezone(AEST)).strftime("%Y-%m")

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


def log_skipped(label, category=None, candidates=None, model=None, reason="claude_disabled"):
    """Ledger row for a Claude call that WOULD have fired but was blocked (reason: claude_disabled,
    category_off or cap). Zero cost, no tokens."""
    record = {
        "timestamp": datetime.now(AEST).isoformat(),
        "run_id": RUN_ID,
        "run_type": RUN_MODE,
        "label": label,
        "category": category or infer_category(label),
        "model": model or "none",
        "candidates": candidates, "reason": reason,
        "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0, "web_search_requests": 0,
        "stop_reason": None, "outcome": "skipped", "error": None, "cost_usd": 0.0,
    }
    _append(record)
    return record


def _bucket(rows, key):
    out = {}
    for r in rows:
        b = out.setdefault(str(r.get(key)), {"calls": 0, "usd": 0.0})
        b["calls"] += 1
        b["usd"] += r["cost_usd"]
    return {k: {"calls": v["calls"], "usd": round(v["usd"], 6)} for k, v in sorted(out.items())}


def _summarise(all_rows):
    skipped = [r for r in all_rows if r.get("outcome") == "skipped"]
    rows = [r for r in all_rows if r.get("outcome") != "skipped"]   # skipped rows are not calls
    total = sum(r["cost_usd"] for r in rows)
    wasted = sum(r["cost_usd"] for r in rows if r["outcome"] in WASTED_OUTCOMES)
    return {
        "calls": len(rows),
        "calls_skipped": len(skipped), "skipped_by_step": _bucket(skipped, "label"),
        "skipped_by_reason": {k: v["calls"] for k, v in _bucket(
            [dict(r, reason=r.get("reason") or "claude_disabled") for r in skipped], "reason").items()},
        "total_usd": round(total, 6), "total_aud": round(total * USD_TO_AUD, 6),
        "wasted_usd": round(wasted, 6), "wasted_aud": round(wasted * USD_TO_AUD, 6),
        "by_step": _bucket(rows, "label"), "by_category": _bucket(rows, "category"),
        "by_model": _bucket(rows, "model"), "by_outcome": _bucket(rows, "outcome"),
    }


def month_to_date_aud(rows=None, now=None):
    """Spend so far this calendar month (Brisbane time): sum of cost_usd of ledger rows in the month,
    converted with USD_TO_AUD. Rows without `outcome` (pre-#52, mispriced) are ignored; skipped rows cost 0."""
    rows = load_rows() if rows is None else rows
    month = (now or datetime.now(AEST)).astimezone(AEST).strftime("%Y-%m")
    usd = sum(r.get("cost_usd", 0.0) for r in rows if "outcome" in r and _month_of(r.get("timestamp")) == month)
    return usd * USD_TO_AUD


def cap_state(spent_aud=None, cap=None):
    """'ok' | 'warn_80' (>=80%) | 'capped' (>=100%) | 'hard_stop' (>=120%)."""
    spent = month_to_date_aud() if spent_aud is None else spent_aud
    cap = monthly_cap_aud() if cap is None else cap
    if spent >= cap * HARD_STOP_FACTOR:
        return "hard_stop"
    if spent >= cap:
        return "capped"
    return "warn_80" if spent >= cap * WARN_FRACTION else "ok"


def cap_blocks(category):
    """True if the spend cap forbids a call in this category right now: everything but Breaking at the cap,
    everything (Breaking too) at the hard stop."""
    state = cap_state()
    return state == "hard_stop" or (state == "capped" and category != "breaking")


def load_rows():
    try:
        rows = json.loads(LEDGER_FILE.read_text()) if LEDGER_FILE.exists() else []
    except Exception:
        rows = []
    return rows


def _cap_info(ledger, now):
    spent, cap = month_to_date_aud(ledger, now), monthly_cap_aud()
    return {"month_to_date_aud": round(spent, 4), "monthly_cap_aud": cap,
            "percent_used": round(100 * spent / cap, 1), "state": cap_state(spent, cap)}


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
        "month_to_date": {"month": month, **_summarise([r for r in ledger if _month_of(r["timestamp"]) == month])},
        "cap": _cap_info(ledger, now),
    }, ledger


def _reasons(summary):
    by = summary["skipped_by_reason"]
    return ", ".join(f"{k} {by.get(k, 0)}" for k in SKIP_REASONS)


def write_summary_md(rollup, ledger):
    t, m, cap = rollup["today"], rollup["month_to_date"], rollup["cap"]
    fx = rollup["aud_rate_display_only"]
    lines = [
        "# API Cost Summary",
        f"_Updated {rollup['as_of']} (AEST). USD is exact; AUD = USD x {fx} (conversion only)._",
        "",
        f"- **Today ({t['date']}):** ${t['total_usd']:.4f} USD (A${t['total_aud']:.4f}) over {t['calls']} calls",
        f"- **Month to date ({m['month']}):** ${m['total_usd']:.4f} USD (A${m['total_aud']:.4f}) over {m['calls']} calls",
        f"- **Spend cap:** A${cap['month_to_date_aud']:.2f} of A${cap['monthly_cap_aud']:.2f} used this month "
        f"({cap['percent_used']:.1f}%), state: {cap['state']}",
        f"- **Calls skipped today:** {t['calls_skipped']} ({_reasons(t)}); month to date: {m['calls_skipped']} ({_reasons(m)})",
        f"- **Wasted spend (truncated/empty) today:** ${t['wasted_usd']:.4f} USD (A${t['wasted_aud']:.4f})",
        f"- **Wasted spend month to date:** ${m['wasted_usd']:.4f} USD (A${m['wasted_aud']:.4f})",
        "",
        "## Last 10 calls",
        "| Time | Step | Model | Cost (USD) | Outcome |",
        "|---|---|---|---|---|",
    ]
    for r in reversed([r for r in ledger if r["outcome"] != "skipped"][-10:]):
        lines.append(f"| {r['timestamp'][:19]} | {r['label']} | {r['model']} | ${r['cost_usd']:.6f} | {r['outcome']} |")
    if not [r for r in ledger if r["outcome"] != "skipped"]:
        lines.append("| — | no calls logged yet | | | |")
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_cost_outputs(health):
    """Call once per run before save_health: adds health['api_cost_ledger'], writes COST_SUMMARY.md, and does the
    cap bookkeeping: month_to_date_aud / monthly_cap_aud / cap_state, and the one-off 80% and 100% alerts
    (cap_alerted_80 / cap_alerted_100, reset when the Brisbane month rolls). A new alert marks this run's
    health entry alerted so the workflow run is failed once, like the stale and failure alerts."""
    rollup, ledger = build_rollup()
    health["api_cost_ledger"] = rollup
    write_summary_md(rollup, ledger)
    cap = rollup["cap"]
    health.update(month_to_date_aud=cap["month_to_date_aud"], monthly_cap_aud=cap["monthly_cap_aud"],
                  cap_state=cap["state"])
    month = rollup["month_to_date"]["month"]
    if health.get("cap_month") != month:
        health.update(cap_month=month, cap_alerted_80=False, cap_alerted_100=False)
    spent, limit = cap["month_to_date_aud"], cap["monthly_cap_aud"]
    crossed = []
    if spent >= limit * WARN_FRACTION and not health.get("cap_alerted_80"):
        health["cap_alerted_80"] = True
        crossed.append("80%")
    if spent >= limit and not health.get("cap_alerted_100"):
        health["cap_alerted_100"] = True
        crossed.append("100%")
    if crossed and health.get("runs"):
        health["runs"][-1]["alerted"] = True
        health["runs"][-1]["cap_alert"] = f"monthly spend reached {crossed[-1]} of the A${limit:.2f} cap "                                           f"(A${spent:.2f} so far)"
        print("ALERT: " + health["runs"][-1]["cap_alert"])
    return health
