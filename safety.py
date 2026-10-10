"""Run-health rules: what counts as good data, stale-ness, and when to alert. Pure functions, no I/O."""
from datetime import datetime, timezone, timedelta

AEST = timezone(timedelta(hours=10))
STALE_AFTER_HOURS = 8   # single source of truth: page shows a stale warning past this age
CATEGORIES = ["breaking", "australia", "archaeology", "football"]
DATA_RUN_TYPES_NO_FETCH = ("deploy_only",)


def parse_ts(value):
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=AEST)


def is_stale(last_update, now=None, hours=STALE_AFTER_HOURS):
    """True if last_update is missing, unparseable, or older than `hours`."""
    dt = parse_ts(last_update)
    if dt is None:
        return True
    return ((now or datetime.now(AEST)) - dt) > timedelta(hours=hours)


def claude_stats(rows):
    """rows: cost-ledger rows for THIS run. A call is failed if its outcome is not 'ok'."""
    failed = [r for r in rows if r.get("outcome") != "ok"]
    return {"calls": len(rows), "failed": len(failed),
            "errored": sum(1 for r in rows if r.get("outcome") == "error"),
            "last_error": next((r.get("error") or r.get("outcome") for r in reversed(failed)), None)}


def failed_categories(rows):
    """Categories whose selection call(s) ran this run but none succeeded (incl. Haiku fallback)."""
    result = set()
    for cat in CATEGORIES:
        sel = [r for r in rows if str(r.get("label", "")).startswith(f"{cat}_selection")]
        if sel and not any(r.get("outcome") == "ok" for r in sel):
            result.add(cat)
    return result


def attempted_categories(rows):
    """Categories whose selection call ran this run."""
    return {c for c in CATEGORIES if any(str(r.get("label", "")).startswith(f"{c}_selection") for r in rows)}


def empty_summary_categories(data, attempted):
    """Attempted categories where any story has no summary (a summary call failed): not publishable."""
    return {c for c in attempted if any(not str(s.get("summary") or "").strip() for s in data.get(c) or [])}


def valid_stories(stories):
    return (isinstance(stories, list)
            and all(isinstance(s, dict) and isinstance(s.get("headline"), str) and s["headline"].strip()
                    for s in stories))


def evaluate_run(run_type, memory, rows, fetched_any, fresh=None, fallback_cats=()):
    """Decide whether this run's data is GOOD.

    fresh: newly produced {category: [stories]} for a full run (checked for >=1 pick);
           None for runs where an empty category is legitimate.
    Rules (all must hold):
      1. at least one source returned articles (skipped for deploy_only)
      2. not every Claude call failed (a run with no calls passes)
      3. full run: at least one category produced >=1 story
      4. produced stories are well-formed (non-empty headline) and memory['stories'] is a dict
      5. not every category that was refreshed fell back to its previous stories
    fallback_cats: categories keeping previous stories (selection failed, or a story had no summary).
    Outcome: failed = not good; degraded = good but a Claude call failed or a category fell back; ok otherwise.
    """
    stats = claude_stats(rows)
    reasons = []
    if run_type not in DATA_RUN_TYPES_NO_FETCH and not fetched_any:
        reasons.append("no source returned any articles")
    if stats["calls"] and stats["failed"] == stats["calls"]:
        reasons.append(f"all {stats['calls']} Claude calls failed ({stats['last_error']})")
    fallback = set(fallback_cats)
    attempted = attempted_categories(rows)
    if attempted and attempted <= fallback:
        reasons.append("every refreshed category fell back to its previous stories ("
                       + ", ".join(sorted(attempted)) + ")")
    if fresh is not None:
        if not any(fresh.get(c) for c in CATEGORIES):
            reasons.append("full run produced no stories in any category")
        if not all(valid_stories(fresh.get(c, [])) for c in CATEGORIES):
            reasons.append("produced stories failed validation (missing headline)")
    if not isinstance(memory, dict) or not isinstance(memory.get("stories", {}), dict):
        reasons.append("memory data is corrupted")
    good = not reasons
    outcome = "failed" if not good else ("degraded" if (stats["failed"] or fallback) else "ok")
    return {"good": good, "outcome": outcome, "reasons": reasons,
            "advance": good and run_type not in DATA_RUN_TYPES_NO_FETCH,
            "claude_calls": stats["calls"], "claude_calls_errored": stats["failed"],
            "claude_last_error": stats["last_error"]}


def decide_alert(health, outcome, consecutive_failures, now=None):
    """Fail the workflow ONLY for a failed run, and only (a) on the first failure of a streak, or
    (b) once, when the data first passes the stale threshold. Silent otherwise until a good run.
    Returns (alert, mark_stale_alerted)."""
    if outcome != "failed":
        return False, False
    stale = is_stale(health.get("last_successful_data_update"), now)
    first = consecutive_failures == 1
    stale_new = stale and not health.get("stale_alerted")
    return (first or stale_new), (stale and (first or stale_new))
