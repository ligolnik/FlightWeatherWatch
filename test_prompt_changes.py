#!/usr/bin/env python3
"""
Test specific prompt changes against baseline scores.

Applies each change to the prompt, re-runs analyze() on test flights,
scores the output, then reverts. Reports a comparison grid.
"""

import json
import os
import sys
import shutil
from datetime import datetime, timezone

# Load .env
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
from flightweather import analyze
from score_briefing import score_briefing

_PROMPTS_DIR = os.path.join(_SCRIPT_DIR, "prompts")

TEST_FLIGHTS = [
    "cache_KSQL_KBDN_2026-03-25",   # Cascades, 24hr data
    "cache_KBJC_KHCR_2026-03-21",   # High Rockies
    "cache_KMQY_KEDC_2026-03-16",   # Flat, frontal
    "cache_KBDN_KSQL_2026-03-25",   # Return, afternoon
]

CATEGORIES = [
    "executive_summary", "forecast_honesty", "operational_decision",
    "decision_triggers", "winds_turbulence", "terrain_route",
    "altitude_strategy", "data_integration", "clarity_signal_noise",
    "pilot_realism",
]

CAT_SHORT = [
    "ExecSum", "FcstHon", "OpDecis", "DecTrig", "Wind/Tb",
    "Terr/Rt", "AltStr", "DataInt", "Clar/SN", "PilotRl",
]


def load_flight(prefix):
    prefix = os.path.join(_SCRIPT_DIR, prefix)
    with open(prefix + "_charts.json") as f:
        charts = json.load(f)
    with open(prefix + "_llm.json") as f:
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


def run_and_score(flights, model="claude-sonnet-4-6"):
    results = []
    for flight in flights:
        route = f"{flight['origin']}->{flight['destination']}"
        print(f"      {route} ...", end=" ", flush=True)
        html, _, prompts = analyze(
            flight["origin"], flight["destination"], flight["departure_dt"],
            flight["altitude_ft"], flight["chart_data"], flight["taf_data"],
            flight["winds_text"], flight["airport_names"], flight["afd_data"], model,
        )
        meta = {
            "origin": flight["origin"], "destination": flight["destination"],
            "departure": flight["departure_str"], "altitude_ft": flight["altitude_ft"],
            "model": model, "sys_prompt_len": len(prompts.get("system", "")),
        }
        scores = score_briefing(html, meta)
        avg = scores.get("computed_avg", 0)
        print(f"{avg}/10")
        results.append({"route": route, "scores": scores, "avg": avg})
    overall = sum(r["avg"] for r in results) / len(results) if results else 0
    return results, round(overall, 2)


def backup_prompts():
    for name in ("system.txt", "briefing.txt"):
        src = os.path.join(_PROMPTS_DIR, name)
        dst = os.path.join(_PROMPTS_DIR, name + ".bak")
        shutil.copy2(src, dst)


def restore_prompts():
    for name in ("system.txt", "briefing.txt"):
        src = os.path.join(_PROMPTS_DIR, name + ".bak")
        dst = os.path.join(_PROMPTS_DIR, name)
        shutil.copy2(src, dst)
        os.remove(src)


def apply_edit(target_file, find, replace):
    path = os.path.join(_PROMPTS_DIR, target_file)
    with open(path) as f:
        content = f.read()
    if find not in content:
        print(f"      WARNING: find text not in {target_file}")
        return False
    with open(path, "w") as f:
        f.write(content.replace(find, replace, 1))
    return True


# ─── Define changes to test ───────────────────────────────────────────

CHANGES = [
    {
        "name": "A: Decision Box",
        "description": "Add GO/MARGINAL/NO-GO condition matrix before exec summary",
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
    {
        "name": "B: Route Segment Analysis",
        "description": "Add per-segment risk breakdown (departure/terrain/enroute/arrival)",
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
    {
        "name": "C: Timing Strategy",
        "description": "Add standalone timing strategy section with best/worst windows",
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
    {
        "name": "D: Bullet-first style",
        "description": "Add style rule requiring bullets over prose, action per section",
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
    {
        "name": "E: Controlling Variable",
        "description": "Add rule to name the single controlling variable in system prompt",
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
]


def main():
    print("Loading test flights ...")
    flights = []
    for prefix in TEST_FLIGHTS:
        f = load_flight(prefix)
        route = f"{f['origin']}->{f['destination']}"
        print(f"  {route}")
        flights.append(f)

    # Baseline
    print(f"\n{'='*70}")
    print("BASELINE — current prompt")
    print("="*70)
    baseline_results, baseline_avg = run_and_score(flights)
    print(f"  Baseline avg: {baseline_avg}/10")

    # Test each change
    all_results = {"baseline": {"avg": baseline_avg, "results": baseline_results}}

    for change in CHANGES:
        print(f"\n{'='*70}")
        print(f"TESTING: {change['name']}")
        print(f"  {change['description']}")
        print("="*70)

        backup_prompts()
        ok = apply_edit(change["target"], change["find"], change["replace"])
        if not ok:
            restore_prompts()
            all_results[change["name"]] = {"avg": 0, "results": [], "error": "find text not found"}
            continue

        results, avg = run_and_score(flights)
        delta = round(avg - baseline_avg, 2)
        print(f"  Avg: {avg}/10 (delta: {delta:+.2f})")

        all_results[change["name"]] = {"avg": avg, "delta": delta, "results": results}
        restore_prompts()

    # Combined: apply all positive changes
    positive = [c for c in CHANGES if all_results[c["name"]].get("delta", -1) > 0]
    if positive:
        print(f"\n{'='*70}")
        print(f"COMBINED — all {len(positive)} positive changes together")
        print("="*70)
        backup_prompts()
        for c in positive:
            apply_edit(c["target"], c["find"], c["replace"])
        results, avg = run_and_score(flights)
        delta = round(avg - baseline_avg, 2)
        print(f"  Avg: {avg}/10 (delta: {delta:+.2f})")
        all_results["COMBINED"] = {"avg": avg, "delta": delta, "results": results}
        restore_prompts()

    # Print comparison grid
    print(f"\n{'='*70}")
    print("RESULTS GRID")
    print("="*70)

    header = f"{'Change':<28} {'Avg':>5} {'Delta':>7}  "
    for s in CAT_SHORT:
        header += f"{s:>7}"
    print(header)
    print("-" * len(header))

    # Baseline row
    row = f"{'Baseline':<28} {baseline_avg:>5.1f} {'':>7}  "
    for cat in CATEGORIES:
        vals = [r["scores"]["scores"].get(cat, {}).get("score", 0) for r in baseline_results]
        row += f"{sum(vals)/len(vals):>7.1f}"
    print(row)

    # Each change
    for change in CHANGES:
        name = change["name"]
        data = all_results[name]
        if data.get("error"):
            print(f"{name:<28} {'ERR':>5} {'':>7}  FIND TEXT NOT FOUND")
            continue
        avg = data["avg"]
        delta = data.get("delta", 0)
        marker = "+" if delta > 0 else ("-" if delta < 0 else "=")
        row = f"{name:<28} {avg:>5.1f} {delta:>+6.2f}{marker} "
        for cat in CATEGORIES:
            vals = [r["scores"]["scores"].get(cat, {}).get("score", 0) for r in data["results"]]
            row += f"{sum(vals)/len(vals):>7.1f}"
        print(row)

    if "COMBINED" in all_results:
        data = all_results["COMBINED"]
        avg = data["avg"]
        delta = data.get("delta", 0)
        marker = "+" if delta > 0 else "-"
        row = f"{'COMBINED':<28} {avg:>5.1f} {delta:>+6.2f}{marker} "
        for cat in CATEGORIES:
            vals = [r["scores"]["scores"].get(cat, {}).get("score", 0) for r in data["results"]]
            row += f"{sum(vals)/len(vals):>7.1f}"
        print(row)

    # Save
    out_path = os.path.join(_SCRIPT_DIR,
        f"change_test_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%MZ')}.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
