#!/usr/bin/env python3
"""
Clean A/B scoring runner for the time-window fix work.

Loads each cached flight's RAW INPUTS (charts + taf/winds/afd), re-runs
flightweather.analyze() with whatever code+prompts are CURRENTLY on disk
(no .briefing_cache, so code-only edits are picked up), scores via the
LLM judge, and writes per-flight + overall results to a named JSON.

Run once before fixes (baseline) and once after (fixed), then diff.

Usage:
    python3 ab_score.py --out ab_baseline.json
    python3 ab_score.py --out ab_fixed.json
    python3 ab_score.py --out x.json --flights cache_KSQL_KTVL_2026-06-04_1630Z
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Load .env (score_briefing also does this, but be explicit)
_env_path = os.path.join(_SCRIPT_DIR, ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, _SCRIPT_DIR)
import flightweather
from score_briefing import score_briefing

SUITE = [
    "cache_KSQL_KBDN_2026-03-23", "cache_KBDN_KSQL_2026-03-23",
    "cache_KSQL_KBDN_2026-03-25", "cache_KBDN_KSQL_2026-03-25",
    "cache_KSQL_KBDN_2026-03-30", "cache_KBDN_KSQL_2026-03-30",
    "cache_KBJC_KHCR_2026-03-21", "cache_KHCR_KSQL_2026-03-21",
    "cache_KMQY_KEDC_2026-03-16", "cache_KEDC_KBJC_2026-03-19",
    "cache_KSQL_KVGT_2026-03-13", "cache_KSQL_KCOS_2026-03-30",
    "cache_KSQL_KTVL_2026-06-04_1630Z",
]


def load_flight(prefix):
    p = os.path.join(_SCRIPT_DIR, prefix)
    with open(p + "_charts.json") as f:
        charts = json.load(f)
    with open(p + "_llm.json") as f:
        llm = json.load(f)
    dep_dt = datetime.strptime(
        llm["departure"].replace("Z", ""), "%Y-%m-%d %H:%M"
    ).replace(tzinfo=timezone.utc)
    return {
        "prefix": prefix,
        "origin": llm["origin"], "destination": llm["destination"],
        "departure_dt": dep_dt, "departure_str": llm["departure"],
        "altitude_ft": llm["altitude_ft"],
        "chart_data": [tuple(c) for c in charts["chart_data"]],
        "taf_data": llm.get("taf_data"),
        "winds_text": llm.get("winds_text", ""),
        "afd_data": llm.get("afd_data", []),
        "airport_names": llm.get("airport_names", {}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--flights", nargs="*", default=SUITE)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--judge-model", default="claude-sonnet-4-6")
    ap.add_argument("--save-html-dir", default=None,
                    help="If set, write each flight's generated inner HTML here")
    args = ap.parse_args()

    if args.save_html_dir:
        os.makedirs(args.save_html_dir, exist_ok=True)

    results = []
    for prefix in args.flights:
        flight = load_flight(prefix)
        route = f"{flight['origin']}->{flight['destination']}"
        print(f"  {route:16s} ...", end=" ", flush=True)
        html, _, prompts = flightweather.analyze(
            flight["origin"], flight["destination"], flight["departure_dt"],
            flight["altitude_ft"], flight["chart_data"], flight["taf_data"],
            flight["winds_text"], flight["airport_names"], flight["afd_data"], args.model,
        )
        if args.save_html_dir:
            with open(os.path.join(args.save_html_dir, prefix + ".html"), "w") as f:
                f.write(html)
        meta = {
            "origin": flight["origin"], "destination": flight["destination"],
            "departure": flight["departure_str"], "altitude_ft": flight["altitude_ft"],
        }
        scores = score_briefing(html, meta, model=args.judge_model)
        avg = scores.get("computed_avg", 0)
        print(f"{avg}/10")
        results.append({"prefix": prefix, "route": route, "avg": avg,
                        "scores": scores.get("scores", {})})

    overall = round(sum(r["avg"] for r in results) / len(results), 2) if results else 0
    out = {"overall": overall, "model": args.model, "judge_model": args.judge_model,
           "results": results}
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nOverall: {overall}/10  ({len(results)} flights)  ->  {args.out}")


if __name__ == "__main__":
    main()
