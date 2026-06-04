#!/usr/bin/env python3
"""
Regression test for temporal reference-frame handling in briefings.

Guards against the failure where the model maps a prior-day AFD's relative time
words ("this afternoon"/"tonight") onto a next-day flight, reads the wrong TAF
FM group for arrival, or overrides flight-time winds-aloft with AFD adjectives.

Re-runs flightweather.analyze() on a cached flight (default: the KSQL->KTVL
day-before case) using whatever code+prompts are CURRENTLY on disk, then asks a
focused LLM judge a set of yes/no questions. Exits non-zero on any FAIL.

Usage:
    python3 test_time_window_regression.py
    python3 test_time_window_regression.py cache_KSQL_KTVL_2026-06-04_1630Z
"""
import json
import os
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_SCRIPT_DIR, ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, _SCRIPT_DIR)
import anthropic
import flightweather

DEFAULT_PREFIX = "cache_KSQL_KTVL_2026-06-04_1630Z"

# Each check: (key, question). Judge answers true = GOOD (no bug).
CHECKS = [
    ("arrival_taf_group",
     "Does the briefing's ARRIVAL wind/condition description match the TAF group valid "
     "AT THE ARRIVAL ETA (the later FM group, e.g. light/calm ~190/04 SKC) rather than "
     "the initial TAF group (e.g. 190/12G20) that expired before arrival?"),
    ("afd_day_alignment",
     "When the briefing uses AFD language about strong winds 'this afternoon/evening', "
     "does it correctly treat that as the AFD's issuance day (the day BEFORE the flight) "
     "rather than asserting it is the flight's departure window?"),
    ("winds_aloft_priority",
     "Does the briefing rely on the winds-aloft data valid for the flight time (light, "
     "~13-20 kt) for the ride, instead of discarding it as 'understating' in favor of "
     "the AFD's stronger prior-day wind numbers?"),
    ("no_false_turbulence",
     "Does the briefing AVOID rating Sierra turbulence as 'likely' based mainly on "
     "prior-day AFD prose, given the flight-window winds aloft are light and the AFD "
     "itself notes winds DECREASE on the flight day?"),
]

JUDGE_SYS = (
    "You are auditing an aviation weather briefing for TEMPORAL REASONING errors only. "
    "You will get the briefing plus several yes/no questions. Answer each strictly from "
    "the briefing text. Return ONLY a JSON object mapping each key to {\"pass\": true|false, "
    "\"why\": \"<one sentence>\"}. true = the briefing handled it correctly."
)


def load_and_generate(prefix):
    p = os.path.join(_SCRIPT_DIR, prefix)
    with open(p + "_charts.json") as f:
        charts = json.load(f)
    with open(p + "_llm.json") as f:
        llm = json.load(f)
    dep_dt = datetime.strptime(
        llm["departure"].replace("Z", ""), "%Y-%m-%d %H:%M"
    ).replace(tzinfo=timezone.utc)
    html, _, _ = flightweather.analyze(
        llm["origin"], llm["destination"], dep_dt, llm["altitude_ft"],
        [tuple(c) for c in charts["chart_data"]], llm.get("taf_data"),
        llm.get("winds_text", ""), llm.get("airport_names", {}),
        llm.get("afd_data", []), "claude-sonnet-4-6",
    )
    return html


def judge(html):
    q = "\n".join(f'- {k}: {text}' for k, text in CHECKS)
    client = anthropic.Anthropic()
    r = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=1500, system=JUDGE_SYS,
        messages=[{"role": "user",
                   "content": f"QUESTIONS (keys):\n{q}\n\n---\nBRIEFING:\n\n{html}"}],
    )
    text = r.content[0].text
    if "```" in text:
        import re
        m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            text = m.group(1)
    return json.loads(text)


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PREFIX
    print(f"Regenerating + auditing: {prefix}")
    html = load_and_generate(prefix)
    verdict = judge(html)
    failed = 0
    for key, _ in CHECKS:
        v = verdict.get(key, {})
        ok = bool(v.get("pass"))
        print(f"  [{'PASS' if ok else 'FAIL'}] {key}: {v.get('why', '?')}")
        if not ok:
            failed += 1
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
