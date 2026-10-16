#!/usr/bin/env python3
"""
Tests that an incomplete or failed briefing call is never presented as a whole briefing.

Covers flightweather.analyze() with a stubbed Anthropic client (no network, no API cost)
and the --from-cache render path, which must lift the truncation warning to the top of
the page. Deterministic: fixed dates, fixed model output.

Run:  .venv/bin/python -m pytest test_briefing_completion.py
  or: .venv/bin/python test_briefing_completion.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

import anthropic
import httpx

import flightweather as fw

DEP = datetime(2026, 3, 30, 17, 0, tzinfo=timezone.utc)
BRIEFING = (
    'SIGNIFICANT_CHARTS: ["Surface Prog"]\n'
    "<h2>Executive Summary</h2><p>GO.</p>"
    "<h2>Route Hazards</h2><p>None.</p>"
    "<h2>The Ride</h2><p>Smooth.</p>"
)


class FakeClient:
    """Stands in for anthropic.Anthropic; replays fixed output and records each call."""
    chunks = [BRIEFING]
    stop_reason = "end_turn"
    error = None
    calls = []

    def __init__(self, *args, **kwargs):
        self.messages = self

    def stream(self, **kwargs):
        FakeClient.calls.append(kwargs)
        if FakeClient.error is not None:
            raise FakeClient.error
        stream = types.SimpleNamespace(
            text_stream=iter(FakeClient.chunks),
            get_final_message=lambda: types.SimpleNamespace(stop_reason=FakeClient.stop_reason),
        )
        manager = mock.MagicMock()
        manager.__enter__.return_value = stream
        manager.__exit__.return_value = False
        return manager


def run_analyze(chunks=None, stop_reason="end_turn", error=None):
    FakeClient.chunks = [BRIEFING] if chunks is None else chunks
    FakeClient.stop_reason = stop_reason
    FakeClient.error = error
    FakeClient.calls = []
    with mock.patch.object(fw.anthropic, "Anthropic", FakeClient), \
            mock.patch.object(fw, "fetch_afd_for_airports", return_value=""), \
            mock.patch.object(fw.time, "sleep"):
        return fw.analyze("KSQL", "KSMO", DEP, 9500, [], model="claude-sonnet-4-6")


class CheckCompletionTests(unittest.TestCase):
    def test_normal_stops_are_complete(self):
        for reason in ("end_turn", "stop_sequence", "stop"):
            self.assertEqual(fw.check_completion(reason), "complete", reason)

    def test_cut_off_and_unknown_stops_are_truncated(self):
        for reason in ("max_tokens", "length", None, "pause_turn"):
            self.assertEqual(fw.check_completion(reason), "truncated", reason)

    def test_refusal_raises(self):
        with self.assertRaises(fw.BriefingRefused):
            fw.check_completion("refusal")
        self.assertTrue(issubclass(fw.BriefingRefused, fw.BriefingFailed))

    def test_lift_banner(self):
        banner, rest = fw.lift_truncation_banner(fw.TRUNCATION_BANNER + "<h2>A</h2>")
        self.assertEqual(banner, fw.TRUNCATION_BANNER)
        self.assertEqual(rest, "<h2>A</h2>")
        self.assertEqual(fw.lift_truncation_banner("<h2>A</h2>"), ("", "<h2>A</h2>"))


class AnalyzeTests(unittest.TestCase):
    def test_complete_briefing_has_no_banner(self):
        html, sig, prompts = run_analyze(stop_reason="end_turn")
        self.assertNotIn("INCOMPLETE BRIEFING", html)
        self.assertTrue(html.startswith("<h2>Executive Summary</h2>"))
        self.assertEqual(sig, {"Surface Prog"})
        self.assertEqual(prompts["stop_reason"], "end_turn")

    def test_requests_the_raised_output_cap(self):
        run_analyze()
        self.assertEqual(FakeClient.calls[0]["max_tokens"], fw.BRIEFING_MAX_TOKENS)
        self.assertGreater(fw.BRIEFING_MAX_TOKENS, 6144)

    def test_truncated_briefing_is_flagged(self):
        cut = 'SIGNIFICANT_CHARTS: []\n<h2>Executive Summary</h2><p>Ceilings lowering (BKN030 by'
        html, _, prompts = run_analyze(chunks=[cut], stop_reason="max_tokens")
        self.assertTrue(html.startswith(fw.TRUNCATION_BANNER))
        self.assertIn("BKN030 by", html)              # the partial text is kept, not dropped
        self.assertEqual(prompts["stop_reason"], "max_tokens")

    def test_refusal_raises(self):
        with self.assertRaises(fw.BriefingRefused):
            run_analyze(chunks=[""], stop_reason="refusal")

    def test_empty_output_raises(self):
        with self.assertRaises(fw.BriefingFailed):
            run_analyze(chunks=["  \n"], stop_reason="end_turn")

    def test_exhausted_retries_raise_instead_of_returning_empty(self):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        err = anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=request), body=None)
        with self.assertRaises(fw.BriefingFailed) as ctx:
            run_analyze(error=err)
        self.assertIn("re-run", str(ctx.exception))
        self.assertEqual(len(FakeClient.calls), 3)


class RenderTests(unittest.TestCase):
    """Run the real CLI on a synthetic cache from a temp copy, so output never lands in the repo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        here = os.path.dirname(os.path.abspath(__file__))
        shutil.copy(os.path.join(here, "flightweather.py"), self.tmp)
        shutil.copytree(os.path.join(here, "prompts"), os.path.join(self.tmp, "prompts"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def render(self, briefing_html):
        prefix = os.path.join(self.tmp, "cache_KSQL_KSMO_2026-03-30_1700Z")
        json.dump({"chart_data": []}, open(prefix + "_charts.json", "w"))
        json.dump({"origin": "KSQL", "destination": "KSMO", "departure": "2026-03-30 17:00Z",
                   "altitude_ft": 9500, "taf_data": [], "winds_text": "", "afd_data": [],
                   "prompts": {}, "briefing_html": briefing_html, "significant_labels": []},
                  open(prefix + "_llm.json", "w"))
        proc = subprocess.run(
            [sys.executable, "flightweather.py", "KSQL", "KSMO", "2026-03-30", "17:00", "9500",
             "--from-cache", prefix, "--no-route", "--no-open"],
            cwd=self.tmp, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        out = [f for f in os.listdir(self.tmp) if f.startswith("briefing_") and f.endswith(".html")]
        self.assertEqual(len(out), 1)
        return open(os.path.join(self.tmp, out[0]), encoding="utf-8").read()

    def test_warning_sits_above_the_executive_summary(self):
        body = BRIEFING.split("\n", 1)[1]
        page = self.render(fw.TRUNCATION_BANNER + body)
        self.assertEqual(page.count("INCOMPLETE BRIEFING"), 1)
        self.assertLess(page.index("INCOMPLETE BRIEFING"), page.index("<h2>Executive Summary</h2>"))

    def test_complete_briefing_renders_without_warning(self):
        page = self.render(BRIEFING.split("\n", 1)[1])
        self.assertNotIn("INCOMPLETE BRIEFING", page)
        self.assertIn("<h2>Executive Summary</h2>", page)


if __name__ == "__main__":
    unittest.main()
