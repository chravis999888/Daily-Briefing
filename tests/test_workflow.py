"""Evaluates the RUN_MODE expression text from briefing.yml with a tiny evaluator for the subset of the
Actions expression language it uses (|| && == ( ) string literals, dotted contexts). GitHub semantics:
'' / null are falsy; `a || b` -> a if truthy else b; `a && b` -> a if falsy else b.
Run: python -m unittest tests.test_workflow"""
import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "briefing.yml"


def run_mode_expr():
    m = re.search(r"RUN_MODE: \$\{\{ (.*) \}\}", WORKFLOW.read_text(encoding="utf-8"))
    return m.group(1)


def crons():
    return re.findall(r"- cron: '([^']+)'", WORKFLOW.read_text(encoding="utf-8"))


def evaluate(expr, ctx):
    tokens = re.findall(r"'[^']*'|\|\||&&|==|\(|\)|[\w.]+", expr)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def eat():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def truthy(v):
        return v not in (None, "", False, 0)

    def primary():
        t = eat()
        if t == "(":
            v = or_expr()
            assert eat() == ")"
            return v
        if t.startswith("'"):
            return t[1:-1]
        return ctx.get(t)

    def eq():
        v = primary()
        while peek() == "==":
            eat()
            v = (str(v or "").lower() == str(primary() or "").lower())
        return v

    def and_expr():
        v = eq()
        while peek() == "&&":
            eat()
            r = eq()
            v = v if not truthy(v) else r
        return v

    def or_expr():
        v = and_expr()
        while peek() == "||":
            eat()
            r = and_expr()
            v = v if truthy(v) else r
        return v

    result = or_expr()
    assert pos == len(tokens), "unparsed tokens"
    return result


def mode_for(event_name, schedule=None, mode=None):
    return evaluate(run_mode_expr(), {"github.event_name": event_name, "github.event.schedule": schedule,
                                      "github.event.inputs.mode": mode})


class RunMode(unittest.TestCase):
    def test_each_cron_maps_to_the_right_mode_exact_match(self):
        full, *breaking = crons()
        self.assertEqual(mode_for("schedule", full), "full")
        for c in breaking:
            self.assertEqual(mode_for("schedule", c), "breaking_only", c)
        self.assertEqual(mode_for("schedule", full + " "), "breaking_only")   # exact match only

    def test_explicit_dispatch_modes_unchanged(self):
        for m in ("full", "breaking_only", "category", "deploy_only"):
            self.assertEqual(mode_for("workflow_dispatch", mode=m), m)

    def test_blank_or_missing_dispatch_mode_runs_full(self):
        self.assertEqual(mode_for("workflow_dispatch", mode=""), "full")
        self.assertEqual(mode_for("workflow_dispatch", mode=None), "full")

    def test_evaluator_matches_the_old_expression_for_known_cases(self):
        old = "github.event.inputs.mode || (github.event.schedule == '*/30 * * * *' && 'breaking_only' || 'full')"
        ctx = lambda s, m: {"github.event.schedule": s, "github.event.inputs.mode": m}
        self.assertEqual(evaluate(old, ctx("*/30 * * * *", None)), "breaking_only")
        self.assertEqual(evaluate(old, ctx("0 */2 * * *", None)), "full")
        self.assertEqual(evaluate(old, ctx(None, "")), "full")            # the behaviour to preserve
        self.assertEqual(evaluate(old, ctx(None, "deploy_only")), "deploy_only")


if __name__ == "__main__":
    unittest.main()
