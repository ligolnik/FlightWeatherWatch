#!/usr/bin/env python3
"""
Test a single prompt change across all test flights.

Usage:
    python3 test_one_change.py baseline
    python3 test_one_change.py A
    python3 test_one_change.py B
    ...

Writes results to change_result_<name>.json
"""

import argparse
import hashlib
import json
import os
import sys
import shutil
import tempfile
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
from score_briefing import score_briefing

_PROMPTS_DIR = os.path.join(_SCRIPT_DIR, "prompts")

# All flights with chart caches
ALL_FLIGHTS = [
    "cache_KSQL_KBDN_2026-03-23",
    "cache_KBDN_KSQL_2026-03-23",
    "cache_KSQL_KBDN_2026-03-25",
    "cache_KBDN_KSQL_2026-03-25",
    "cache_KSQL_KBDN_2026-03-30",
    "cache_KBDN_KSQL_2026-03-30",
    "cache_KBJC_KHCR_2026-03-21",
    "cache_KHCR_KSQL_2026-03-21",
    "cache_KMQY_KEDC_2026-03-16",
    "cache_KEDC_KBJC_2026-03-19",
    "cache_KSQL_KVGT_2026-03-13",
    "cache_KSQL_KCOS_2026-03-30",
]

CATEGORIES = [
    "executive_summary", "forecast_honesty", "operational_decision",
    "decision_triggers", "winds_turbulence", "terrain_route",
    "altitude_strategy", "data_integration", "clarity_signal_noise",
    "pilot_realism",
]

# ─── Change definitions ───────────────────────────────────────────────

CHANGES = {
    "A": {
        "name": "A: Decision Box",
        "target": "briefing.txt",
        "find": """<h2>Executive Summary</h2>
A single short paragraph (3-5 sentences max) containing exactly these four elements:""",
        "replace": """<h2>Decision Box</h2>
Present a quick-reference decision matrix with specific, testable conditions:

<strong>GO if:</strong> 2-4 concrete, observable conditions (e.g., "freezing level above 14,000 ft", "winds aloft under 30 kt over Cascades")
<strong>MARGINAL if:</strong> 2-4 conditions that make the flight workable but require active monitoring
<strong>NO-GO if:</strong> 2-4 hard-stop conditions (e.g., "moderate icing reported at cruise altitude", "ceilings below 3,000 ft at destination")

Make every condition specific and testable — a pilot should be able to check each one against current data.

<h2>Executive Summary</h2>
A single short paragraph (3-4 sentences max) containing exactly these four elements:""",
    },
    "B": {
        "name": "B: Route Segment Analysis",
        "target": "briefing.txt",
        "find": """OTHER HAZARDS:
- Any weather to dodge or plan around?
Use a table: Hazard | Risk | Leg | What to Expect
Incorporate findings from icing (FIP), turbulence (GTG), G-AIRMET, SIGMET, SigWx, and QPF charts.
Heavy QPF near the route means IMC, potential icing, and possible convection.""",
        "replace": """ROUTE SEGMENT RISK SUMMARY:
After the detailed ride analysis, include a segment-by-segment risk table:

Segment | Conditions | Risk (LOW/MOD/HIGH) | Pilot Implication
Break the route into logical segments: departure climb, terrain crossing(s), enroute plateau, descent/arrival.
Each row must have a one-sentence pilot implication (not just a weather description).

OTHER HAZARDS:
- Any weather to dodge or plan around?
Use a table: Hazard | Risk | Leg | What to Expect
Incorporate findings from icing (FIP), turbulence (GTG), G-AIRMET, SIGMET, SigWx, and QPF charts.
Heavy QPF near the route means IMC, potential icing, and possible convection.""",
    },
    "C": {
        "name": "C: Timing Strategy",
        "target": "briefing.txt",
        "find": """<h2>If You Go — Do This</h2>
Concrete action items. Departure time tweak, altitude change, specific alternates, fuel stop,
what forecast products to check the night before and morning of.""",
        "replace": """<h2>Timing Strategy</h2>
State explicitly:
- Best departure window and why (e.g., "before convective heating", "ahead of system arrival")
- Worst departure window and why
- Whether a 1-2 hour shift materially changes the risk picture
Keep to 3-4 bullets max. If timing is not a significant factor, say so in one line.

<h2>If You Go — Do This</h2>
Concrete action items. Altitude change, specific alternates, fuel stop,
what forecast products to check the night before and morning of.
Do NOT repeat timing advice already given in Timing Strategy.""",
    },
    "D": {
        "name": "D: Bullet-first style",
        "target": "briefing.txt",
        "find": """Respond with HTML using: h2, h3, h4, p, ul, ol, li, strong, em, blockquote, table, thead, tbody, tr, th, td, code, hr.
No html/head/body/style/script tags.""",
        "replace": """STYLE RULES:
- Prefer tight bullet lists over long paragraphs. A pilot scanning quickly should find the key point in each section within 5 seconds.
- Every section must contain at least one pilot action or implication — do not just describe weather without stating what it means operationally.
- Do not repeat the same information across sections. Each section adds new value or a different angle.

Respond with HTML using: h2, h3, h4, p, ul, ol, li, strong, em, blockquote, table, thead, tbody, tr, th, td, code, hr.
No html/head/body/style/script tags.""",
    },
    "E": {
        "name": "E: Controlling Variable",
        "target": "system.txt",
        "find": """PRIMARY RISK RULE:

Identify and explicitly state the single most important operational risk at the beginning of the briefing:

Primary Risk: <one sentence>

Structure the entire briefing around this risk.
Clearly label secondary risks as secondary or conditional.""",
        "replace": """PRIMARY RISK RULE:

Identify and explicitly state the single most important operational risk at the beginning of the briefing:

Primary Risk: <one sentence>

Structure the entire briefing around this risk.
Clearly label secondary risks as secondary or conditional.

CONTROLLING VARIABLE RULE:

Beyond the primary risk, identify the single controlling variable — the one
observable data point that most determines whether this flight is GO or NO-GO.
State it explicitly (e.g., "system timing", "freezing level height",
"marine layer depth at arrival"). This variable should thread through
the briefing: mentioned in the executive summary, checked in analysis,
and referenced in the decision.""",
    },
}


def load_flight(prefix):
    prefix_path = os.path.join(_SCRIPT_DIR, prefix)
    with open(prefix_path + "_charts.json") as f:
        charts = json.load(f)
    with open(prefix_path + "_llm.json") as f:
        llm = json.load(f)
    dep_str = llm["departure"]
    dep_dt = datetime.strptime(
        dep_str.replace("Z", ""), "%Y-%m-%d %H:%M"
    ).replace(tzinfo=timezone.utc)
    return {
        "origin": llm["origin"], "destination": llm["destination"],
        "departure_dt": dep_dt, "departure_str": dep_str,
        "altitude_ft": llm["altitude_ft"],
        "chart_data": [tuple(c) for c in charts["chart_data"]],
        "taf_data": llm.get("taf_data"),
        "winds_text": llm.get("winds_text", ""),
        "afd_data": llm.get("afd_data", []),
        "airport_names": llm.get("airport_names", {}),
    }


def apply_change_to_dir(prompts_dir, change):
    """Apply a find/replace to a prompt file in the given directory."""
    path = os.path.join(prompts_dir, change["target"])
    with open(path) as f:
        content = f.read()
    if change["find"] not in content:
        return False
    with open(path, "w") as f:
        f.write(content.replace(change["find"], change["replace"], 1))
    return True


_BRIEFING_CACHE_DIR = os.path.join(_SCRIPT_DIR, ".briefing_cache")


def _briefing_cache_key(prompts_dir, flight, model):
    """Hash prompt content + flight params for caching generated briefings."""
    h = hashlib.sha256()
    for name in ("system.txt", "briefing.txt"):
        path = os.path.join(prompts_dir, name)
        if os.path.exists(path):
            with open(path, "rb") as f:
                h.update(f.read())
    h.update(f"{flight['origin']}_{flight['destination']}_{flight['departure_str']}_{flight['altitude_ft']}_{model}".encode())
    return h.hexdigest()[:16]


def run_with_prompts(prompts_dir, flights, model="claude-sonnet-4-6", judge_model="claude-sonnet-4-6"):
    """Run analyze using prompts from a specific directory."""
    os.makedirs(_BRIEFING_CACHE_DIR, exist_ok=True)

    # Temporarily swap the prompts module's directory
    import flightweather
    orig_system = flightweather.SYSTEM_PROMPT

    with open(os.path.join(prompts_dir, "system.txt")) as f:
        flightweather.SYSTEM_PROMPT = f.read()

    # Also need to swap the briefing template loader
    orig_prompts_dir = flightweather._PROMPTS_DIR
    flightweather._PROMPTS_DIR = prompts_dir

    results = []
    try:
        for flight in flights:
            route = f"{flight['origin']}->{flight['destination']}"
            print(f"  {route} ...", end=" ", flush=True)

            # Check briefing cache
            cache_key = _briefing_cache_key(prompts_dir, flight, model)
            cache_path = os.path.join(_BRIEFING_CACHE_DIR, f"{cache_key}.json")

            if os.path.exists(cache_path):
                with open(cache_path) as f:
                    cached = json.load(f)
                html = cached["html"]
                prompts = cached["prompts"]
                print("[cached] ", end="", flush=True)
            else:
                html, _, prompts = flightweather.analyze(
                    flight["origin"], flight["destination"], flight["departure_dt"],
                    flight["altitude_ft"], flight["chart_data"], flight["taf_data"],
                    flight["winds_text"], flight["airport_names"], flight["afd_data"], model,
                )
                # Save to cache
                with open(cache_path, "w") as f:
                    json.dump({"html": html, "prompts": prompts}, f)

            meta = {
                "origin": flight["origin"], "destination": flight["destination"],
                "departure": flight["departure_str"], "altitude_ft": flight["altitude_ft"],
                "model": model, "sys_prompt_len": len(prompts.get("system", "")),
            }
            scores = score_briefing(html, meta, model=judge_model)
            avg = scores.get("computed_avg", 0)
            print(f"{avg}/10")
            results.append({"route": route, "scores": scores, "avg": avg})
    finally:
        flightweather.SYSTEM_PROMPT = orig_system
        flightweather._PROMPTS_DIR = orig_prompts_dir

    overall = sum(r["avg"] for r in results) / len(results) if results else 0
    return results, round(overall, 2)


def main():
    parser = argparse.ArgumentParser(description="Test a single prompt change across all test flights")
    parser.add_argument("change_id", help="Change to test: baseline, A, B, C, D, E")
    parser.add_argument("--judge-model", default="claude-sonnet-4-6",
                        help="Model for scoring (default: claude-sonnet-4-6)")
    parser.add_argument("--use-baseline", metavar="FILE",
                        help="Skip baseline run; load scores from this JSON file")
    args = parser.parse_args()

    change_id = args.change_id

    print(f"Loading {len(ALL_FLIGHTS)} test flights ...")
    flights = []
    for prefix in ALL_FLIGHTS:
        try:
            f = load_flight(prefix)
            route = f"{f['origin']}->{f['destination']}"
            print(f"  {route}")
            flights.append(f)
        except FileNotFoundError:
            print(f"  SKIP {prefix} — not found")

    # Copy prompts to temp dir so we can modify without affecting other parallel runs
    tmpdir = tempfile.mkdtemp(prefix="prompt_test_")
    for name in ("system.txt", "briefing.txt"):
        shutil.copy2(os.path.join(_PROMPTS_DIR, name), os.path.join(tmpdir, name))

    if change_id == "baseline":
        print(f"\nRunning BASELINE ({len(flights)} flights) ...")
    else:
        change = CHANGES.get(change_id)
        if not change:
            print(f"Unknown change: {change_id}. Options: baseline, A, B, C, D, E")
            sys.exit(1)
        print(f"\nApplying {change['name']} ...")
        if not apply_change_to_dir(tmpdir, change):
            print("ERROR: find text not found in prompt")
            sys.exit(1)

    results, avg = run_with_prompts(tmpdir, flights, judge_model=args.judge_model)
    print(f"\nAverage: {avg}/10")

    # Clean up
    shutil.rmtree(tmpdir)

    # Save results
    out = {
        "change": change_id,
        "name": CHANGES[change_id]["name"] if change_id != "baseline" else "baseline",
        "avg": avg,
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "judge_model": args.judge_model,
    }
    out_path = os.path.join(_SCRIPT_DIR, f"change_result_{change_id}.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
