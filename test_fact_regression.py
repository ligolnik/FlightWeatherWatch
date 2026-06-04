#!/usr/bin/env python3
"""
Regression test for factual-value handling in briefings: route distance and
tower hours. Guards against the model inventing a distance (confusing the ETA
minutes with nautical miles) or mis-converting tower hours between Zulu and
local time.

Re-runs flightweather.analyze() on a cached flight with the CURRENT code/prompts,
then asks a focused boolean judge. Exits non-zero on any FAIL.

Usage:
    python3 test_fact_regression.py                       # both default flights
    python3 test_fact_regression.py cache_KTVL_KSQL_2026-06-04_2000Z
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
import flightweather as fw

DEFAULT_PREFIXES = [
    "cache_KSQL_KTVL_2026-06-04_1630Z",
    "cache_KTVL_KSQL_2026-06-04_2000Z",
]

def _checks(expected_nm):
    return [
        ("route_distance",
         f"The actual great-circle route distance for this flight is {expected_nm} nm. Does every "
         f"FULL-route distance figure the briefing states (block time, fuel, leg discussion) agree "
         f"with ~{expected_nm} nm (within rounding), with NO contradictory or invented number and no "
         f"confusion of the ETA minutes with nautical miles? Pass if all stated route distances are "
         f"~{expected_nm} nm."),
        ("tower_hours",
         "Does the briefing AVOID asserting specific control-tower OPERATING HOURS or a tower "
         "open/closed-at-ETA conclusion as fact (e.g. 'tower closes at 1800 local', 'tower is open at "
         "your ETA')? Note: stating that an airport simply IS towered or non-towered is a correct "
         "structural fact and is completely FINE — do not fail on that. The dataset has only airport "
         "ATTENDANCE hours (not tower hours); citing attendance or advising to verify tower status via "
         "Chart Supplement/NOTAM/ATIS is fine. FAIL ONLY if definite tower operating hours or an "
         "open/closed-at-ETA conclusion is stated as fact."),
    ]


def _expected_nm(llm):
    cum = [e.get("nm") for e in (llm.get("taf_data") or []) if isinstance(e.get("nm"), (int, float))]
    return round(max(cum)) if cum and max(cum) > 0 else "the computed"

JUDGE_SYS = (
    "You are auditing an aviation weather briefing for FACTUAL errors in route distance and tower "
    "hours only. You get the briefing plus yes/no questions. Answer strictly from the briefing text. "
    "Return ONLY a JSON object mapping each key to {\"pass\": true|false, \"why\": \"<one sentence>\"}. "
    "true = handled correctly."
)


def regenerate(prefix):
    p = os.path.join(_SCRIPT_DIR, prefix)
    charts = json.load(open(p + "_charts.json"))
    llm = json.load(open(p + "_llm.json"))
    dep = datetime.strptime(llm["departure"].replace("Z", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    html, _, _ = fw.analyze(
        llm["origin"], llm["destination"], dep, llm["altitude_ft"],
        [tuple(c) for c in charts["chart_data"]], llm.get("taf_data"),
        llm.get("winds_text", ""), llm.get("airport_names", {}),
        llm.get("afd_data", []), "claude-sonnet-4-6",
    )
    return html, llm


def judge(html, checks):
    q = "\n".join(f"- {k}: {t}" for k, t in checks)
    r = anthropic.Anthropic().messages.create(
        model="claude-sonnet-4-6", max_tokens=1200, system=JUDGE_SYS,
        messages=[{"role": "user", "content": f"QUESTIONS (keys):\n{q}\n\n---\nBRIEFING:\n\n{html}"}],
    )
    text = r.content[0].text
    if "```" in text:
        import re
        m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            text = m.group(1)
    return json.loads(text)


def main():
    prefixes = sys.argv[1:] or DEFAULT_PREFIXES
    failed = 0
    for prefix in prefixes:
        print(f"\n=== {prefix} ===")
        html, llm = regenerate(prefix)
        checks = _checks(_expected_nm(llm))
        verdict = judge(html, checks)
        for key, _ in checks:
            v = verdict.get(key, {})
            ok = bool(v.get("pass"))
            print(f"  [{'PASS' if ok else 'FAIL'}] {key}: {v.get('why', '?')}")
            failed += 0 if ok else 1
    print(f"\n{'ALL PASS' if not failed else str(failed) + ' FAILED'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
