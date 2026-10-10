"""Dry-run harness: runs fetch_news.main() in a temp dir with fake fetchers and a fake Claude client.
No network, no API key, no spend. Used by the pipeline tests."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _k in ("ANTHROPIC_API_KEY", "NEWSDATA_API_KEY", "GUARDIAN_API_KEY"):
    os.environ.setdefault(_k, "dry-run")

import api  # noqa: E402
import fetchers  # noqa: E402
import fetch_news  # noqa: E402

CREDIT_ERROR = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
                "'message': 'Your credit balance is too low to access the Anthropic API. "
                "Please go to Plans & Billing to upgrade or purchase credits.'}}")


class FakeClient:
    """mode 'ok': plausible responses. mode 'error': every call raises the real credit error."""

    def __init__(self, mode, fail_when=None):
        self.mode = mode
        self.fail_when = fail_when  # substring: calls whose prompt contains it raise the credit error
        self.messages = self
        self.calls = 0

    def create(self, model, max_tokens, messages, **kwargs):
        self.calls += 1
        if self.mode == "error" or (self.fail_when and self.fail_when in messages[0]["content"]):
            raise RuntimeError(CREDIT_ERROR)
        prompt = messages[0]["content"]
        if "In 3-4 sentences" in prompt:
            text = json.dumps({"summary": "A factual summary of the story.", "tracking_suggestions": []})
        elif "editor" in prompt and "Return ONLY a JSON array" in prompt and "tracking these ongoing" not in prompt:
            url = (re.search(r"URL: (\S+)", prompt) or [None, "https://example.com/x"])[1]
            text = json.dumps([{"headline": "Australia dry-run story: 12 killed in test event", "score": 8,
                                "timestamp": "1 hr ago", "so_what": "", "url": url, "source": "Test",
                                "deeper_search": False}])
        else:
            text = "[]"
        usage = NS(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0,
                   cache_read_input_tokens=0, server_tool_use=None)
        return NS(model=model, content=[NS(type="text", text=text)], usage=usage, stop_reason="end_turn")


def _fake_articles(name, sources):
    if sources == "down":
        fetchers.record_source(name, False, "ConnectionError: dry-run network down")
        return []
    fetchers.record_source(name, True)
    return [{"title": f"Australia {name} headline", "url": f"https://example.com/{name}", "source": "Test",
             "time": "", "content": "body", "image": ""}]


def run_pipeline(workdir, run_mode="full", claude="ok", category="", seed=None, sources="ok", fail_when=None):
    """Run fetch_news.main() inside workdir. Returns (stdout, exit_code).
    claude: 'ok' | 'error' (every call raises the real credit error). sources: 'ok' | 'down'.
    seed: optional {filename: json-able} written before the run."""
    old = Path.cwd()
    os.chdir(workdir)
    for name, content in (seed or {}).items():
        Path(name).write_text(json.dumps(content, indent=2), encoding="utf-8")
    buf = io.StringIO()
    rc = 0

    def gdelt(q, timespan="1h", max_records=25, memory=None):
        arts = _fake_articles("gdelt", sources)
        return arts, ("" if arts else "GDELT fetch failed: dry-run down"), memory

    patches = [
        mock.patch.object(api, "client", FakeClient(claude, fail_when)),
        mock.patch.object(fetch_news, "RUN_MODE", run_mode),
        mock.patch.object(fetch_news, "RUN_CATEGORY", category),
        mock.patch("time.sleep", lambda *_: None),
        mock.patch.object(fetch_news, "fetch_gdelt_articles", gdelt),
        mock.patch.object(fetch_news, "fetch_guardian", lambda q, page_size=15, section=None: _fake_articles("Guardian", sources)),
        mock.patch.object(fetch_news, "fetch_rss", lambda url, name: _fake_articles(name, sources)),
        mock.patch.object(fetch_news, "fetch_newsdata", lambda q, country=None: _fake_articles("NewsData", sources)),
    ]
    try:
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with contextlib.redirect_stdout(buf):
                try:
                    rc = fetch_news.main() or 0
                except SystemExit as e:
                    rc = e.code or 0
    finally:
        os.chdir(old)
    return buf.getvalue(), rc


def workdir():
    return tempfile.TemporaryDirectory()


def read_json(d, name):
    p = Path(d) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
