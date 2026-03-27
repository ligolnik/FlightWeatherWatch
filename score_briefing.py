#!/usr/bin/env python3
"""
Score weather briefing quality using an LLM judge.

Reads cached briefing HTML from test_briefings/ and scores it on 10 dimensions
using the judge prompt in prompts/judge.txt. Supports scoring individual cached
briefings or batch-scoring all versions for comparison.

Usage:
    # Score a single cached briefing
    python3 score_briefing.py test_briefings/v3_externalized_enhanced/cache_KSQL_KBDN_2026-03-30

    # Score all cached briefings and show comparison
    python3 score_briefing.py --all

    # Use a specific judge model
    python3 score_briefing.py --all --model claude-opus-4-6
"""

import argparse
import json
import os
import sys
import glob
from datetime import datetime, timezone

# Load .env if present (keeps API key out of the environment)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

import anthropic

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROMPTS_DIR = os.path.join(_SCRIPT_DIR, "prompts")


def _load_judge_prompt():
    path = os.path.join(_PROMPTS_DIR, "judge.txt")
    with open(path, "r") as f:
        return f.read()


def load_briefing_html(cache_prefix):
    """Load briefing HTML from an LLM cache file."""
    prefix = cache_prefix.replace("_charts.json", "").replace("_llm.json", "")
    llm_path = prefix + "_llm.json"

    if not os.path.exists(llm_path):
        sys.exit(f"Error: {llm_path} not found")

    with open(llm_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    html = data.get("briefing_html", data.get("synoptic_html", ""))
    meta = {
        "origin": data["origin"],
        "destination": data["destination"],
        "departure": data["departure"],
        "altitude_ft": data["altitude_ft"],
        "model": data.get("prompts", {}).get("model", "unknown"),
        "sys_prompt_len": len(data.get("prompts", {}).get("system", "")),
    }
    return html, meta


def detect_version(sys_prompt_len):
    """Classify prompt version by system prompt length."""
    if sys_prompt_len < 1000:
        return "v1"
    elif sys_prompt_len < 6000:
        return "v2"
    else:
        return "v3"


def score_briefing(html, meta, model="claude-sonnet-4-6"):
    """Send briefing to judge LLM and return parsed scores."""
    judge_prompt = _load_judge_prompt()

    route_context = (
        f"Route: {meta['origin']} -> {meta['destination']}\n"
        f"Departure: {meta['departure']}\n"
        f"Altitude: {meta['altitude_ft']:,} ft\n"
    )

    import time as _time
    client = anthropic.Anthropic()
    for _attempt in range(5):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=judge_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": f"{route_context}\n---\n\nBRIEFING TO EVALUATE:\n\n{html}",
                    }
                ],
            )
            break
        except anthropic.RateLimitError:
            wait = 30 * (_attempt + 1)
            print(f" rate limited, waiting {wait}s ...", end="", flush=True)
            _time.sleep(wait)
    else:
        return {"scores": {}, "computed_avg": 0, "error": "rate_limited"}

    text = response.content[0].text

    # Extract JSON from response (may be wrapped in markdown code fences)
    json_match = text
    if "```" in text:
        import re
        m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            json_match = m.group(1)

    try:
        scores = json.loads(json_match)
    except json.JSONDecodeError:
        print(f"Warning: Could not parse JSON from judge response. Raw output:")
        print(text[:500])
        scores = {"raw_text": text, "overall_score": None}

    # Compute average from category scores (more reliable than LLM's "overall")
    cat_scores = [v.get("score", 0) for v in scores.get("scores", {}).values()
                  if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
    if cat_scores:
        scores["computed_avg"] = round(sum(cat_scores) / len(cat_scores), 1)

    return scores


def find_all_caches():
    """Find all LLM cache files in test_briefings/."""
    pattern = os.path.join(_SCRIPT_DIR, "test_briefings", "**", "*_llm.json")
    files = glob.glob(pattern, recursive=True)
    # Sort by version directory name
    files.sort()
    return [f.replace("_llm.json", "") for f in files]


def print_comparison_table(results):
    """Print a comparison table of scores across versions."""
    categories = [
        "executive_summary", "forecast_honesty", "operational_decision",
        "decision_triggers", "winds_turbulence", "terrain_route",
        "altitude_strategy", "data_integration", "clarity_signal_noise",
        "pilot_realism",
    ]
    cat_labels = [
        "Exec Summary", "Forecast Honesty", "Op Decision", "Decision Triggers",
        "Winds/Turb", "Terrain/Route", "Alt Strategy", "Data Integration",
        "Clarity/S:N", "Pilot Realism",
    ]

    # Group by version
    versions = {}
    for r in results:
        v = r["version"]
        if v not in versions:
            versions[v] = []
        versions[v].append(r)

    print("\n" + "=" * 80)
    print("BRIEFING QUALITY SCORES BY PROMPT VERSION")
    print("=" * 80)

    for version in sorted(versions.keys()):
        entries = versions[version]
        print(f"\n--- {version} ---")
        for entry in entries:
            route = f"{entry['meta']['origin']}->{entry['meta']['destination']}"
            scores = entry.get("scores", {}).get("scores", {})
            avg = entry.get("scores", {}).get("computed_avg", "?")
            print(f"\n  {route} (avg: {avg}/10)")
            for cat, label in zip(categories, cat_labels):
                s = scores.get(cat, {})
                score = s.get("score", "?")
                notes = s.get("notes", "")
                print(f"    {label:20s}  {score:>2}/10  {notes}")

    # Version averages
    print("\n" + "=" * 80)
    print("VERSION AVERAGES")
    print("=" * 80)
    print(f"\n  {'Version':<8} {'Overall':>8}  ", end="")
    for label in cat_labels:
        print(f"{label[:8]:>9}", end="")
    print()
    print("  " + "-" * (8 + 8 + 2 + 9 * len(cat_labels)))

    for version in sorted(versions.keys()):
        entries = versions[version]
        valid = [e for e in entries if e.get("scores", {}).get("computed_avg") is not None]
        if not valid:
            continue
        avg_overall = sum(e["scores"]["computed_avg"] for e in valid) / len(valid)
        print(f"  {version:<8} {avg_overall:>7.1f}  ", end="")
        for cat in categories:
            vals = [e["scores"]["scores"].get(cat, {}).get("score", 0)
                    for e in valid if "scores" in e.get("scores", {})]
            avg = sum(vals) / len(vals) if vals else 0
            print(f"{avg:>9.1f}", end="")
        print()


def main():
    parser = argparse.ArgumentParser(description="Score weather briefings with LLM judge")
    parser.add_argument("cache_prefix", nargs="?", help="Cache prefix to score")
    parser.add_argument("--all", action="store_true", help="Score all cached briefings")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Judge model (default: claude-sonnet-4-6)")
    parser.add_argument("--save", action="store_true",
                        help="Save scores to JSON alongside cache files")
    args = parser.parse_args()

    if not args.all and not args.cache_prefix:
        parser.error("Provide a cache_prefix or use --all")

    if args.all:
        prefixes = find_all_caches()
        if not prefixes:
            sys.exit("No cached briefings found in test_briefings/")
        print(f"Found {len(prefixes)} cached briefings")
    else:
        prefixes = [args.cache_prefix.replace("_charts.json", "").replace("_llm.json", "")]

    results = []
    for i, prefix in enumerate(prefixes):
        html, meta = load_briefing_html(prefix)
        version = detect_version(meta["sys_prompt_len"])
        route = f"{meta['origin']}->{meta['destination']}"

        print(f"\n[{i+1}/{len(prefixes)}] Scoring {route} ({version}, {meta['model']}) ...")
        scores = score_briefing(html, meta, model=args.model)

        result = {
            "cache_prefix": prefix,
            "version": version,
            "meta": meta,
            "scores": scores,
        }
        results.append(result)

        avg = scores.get("computed_avg", "?")
        print(f"  Average: {avg}/10")

        if args.save:
            score_path = prefix + "_scores.json"
            with open(score_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            print(f"  Saved -> {score_path}")

    if len(results) > 1:
        print_comparison_table(results)

    # Save aggregate results
    if args.all:
        out_path = os.path.join(_SCRIPT_DIR, "test_briefings",
                                f"scores_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%MZ')}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nAggregate scores saved -> {out_path}")


if __name__ == "__main__":
    main()
