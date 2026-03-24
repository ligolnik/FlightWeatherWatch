#!/usr/bin/env python3
"""
Automated prompt optimization loop for weather briefings.

Inspired by Karpathy's autoresearch pattern: propose a change, measure,
keep or revert. Uses cached chart data as a fixed test set, re-runs
analyze() with the current prompt, and scores output with an LLM judge.

Usage:
    python3 optimize_prompt.py                    # 3 iterations, default test set
    python3 optimize_prompt.py --iterations 5     # more rounds
    python3 optimize_prompt.py --dry-run          # propose changes without applying
    python3 optimize_prompt.py --focus terrain     # focus optimization on a category

Test set: uses chart caches from the project root (cache_*_charts.json).
Picks a diverse subset (different routes, weather scenarios) for evaluation.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
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

import anthropic

sys.path.insert(0, _SCRIPT_DIR)
from flightweather import analyze
from score_briefing import score_briefing, _load_judge_prompt

_PROMPTS_DIR = os.path.join(_SCRIPT_DIR, "prompts")

# Default test set — diverse routes with chart caches available
DEFAULT_TEST_SET = [
    "cache_KSQL_KBDN_2026-03-25",   # Bay Area to Bend, mountain terrain
    "cache_KBDN_KSQL_2026-03-25",   # Return flight, different weather window
    "cache_KBJC_KHCR_2026-03-21",   # Denver to Heber, high mountain
]

OPTIMIZER_SYSTEM = """\
You are an expert prompt engineer optimizing a weather briefing system prompt.

The system produces aviation weather briefings for pilots. The prompt is scored
by an LLM judge on 10 dimensions (1-10 each):
1. Executive Summary Quality
2. Forecast Honesty & Confidence
3. Forecast Honesty & Confidence
4. Decision Triggers
5. Winds & Turbulence Analysis
6. Terrain & Route Awareness
7. Altitude Strategy
8. Data Integration
9. Clarity & Signal-to-Noise
10. Pilot Realism

Your job: propose ONE targeted edit to the prompt that will improve scores.

RULES:
- Make ONE focused change. Do not rewrite the whole prompt.
- Target the weakest scoring category (or the focus area if specified).
- Changes should be additive rules, clarifications, or tightening of existing rules.
- Do not remove existing rules that are working.
- Do not make the prompt significantly longer (max +200 chars net).
- The prompt is for a weather briefing system — keep changes aviation-relevant.
- Be specific. Vague instructions like "be more detailed" don't help.

OUTPUT FORMAT (strict JSON):
{
  "target_file": "system.txt" or "briefing.txt",
  "category_targeted": "the scoring category you're trying to improve",
  "reasoning": "why this change should help (1-2 sentences)",
  "find": "exact text to find in the prompt (must match uniquely)",
  "replace": "replacement text"
}

The find/replace must work as an exact string substitution. Include enough
surrounding context in "find" to match uniquely. The replacement should be
a modified version of the found text, not a completely different section.
"""


def load_test_flight(cache_prefix):
    """Load chart data and metadata from a cached flight."""
    prefix = os.path.join(_SCRIPT_DIR, cache_prefix)
    charts_path = prefix + "_charts.json"
    llm_path = prefix + "_llm.json"

    if not os.path.exists(charts_path) or not os.path.exists(llm_path):
        return None

    with open(charts_path, "r", encoding="utf-8") as f:
        charts_cache = json.load(f)
    with open(llm_path, "r", encoding="utf-8") as f:
        llm_cache = json.load(f)

    departure_str = llm_cache["departure"]
    departure_dt = datetime.strptime(
        departure_str.replace("Z", ""), "%Y-%m-%d %H:%M"
    ).replace(tzinfo=timezone.utc)

    return {
        "origin": llm_cache["origin"],
        "destination": llm_cache["destination"],
        "departure_dt": departure_dt,
        "departure_str": departure_str,
        "altitude_ft": llm_cache["altitude_ft"],
        "chart_data": [tuple(c) for c in charts_cache["chart_data"]],
        "taf_data": llm_cache.get("taf_data"),
        "winds_text": llm_cache.get("winds_text", ""),
        "afd_data": llm_cache.get("afd_data", []),
        "airport_names": llm_cache.get("airport_names", {}),
    }


def run_and_score(flights, model="claude-sonnet-4-6", judge_model="claude-sonnet-4-6"):
    """Run analyze() on each flight with current prompt, then score each output."""
    results = []
    for flight in flights:
        route = f"{flight['origin']}->{flight['destination']}"
        print(f"    Generating briefing for {route} ...", end=" ", flush=True)

        html, sig_labels, prompts = analyze(
            flight["origin"],
            flight["destination"],
            flight["departure_dt"],
            flight["altitude_ft"],
            flight["chart_data"],
            flight["taf_data"],
            flight["winds_text"],
            flight["airport_names"],
            flight["afd_data"],
            model,
        )

        meta = {
            "origin": flight["origin"],
            "destination": flight["destination"],
            "departure": flight["departure_str"],
            "altitude_ft": flight["altitude_ft"],
            "model": model,
            "sys_prompt_len": len(prompts.get("system", "")),
        }

        print("scoring ...", end=" ", flush=True)
        scores = score_briefing(html, meta, model=judge_model)
        avg = scores.get("computed_avg", 0)
        print(f"{avg}/10")

        results.append({
            "route": route,
            "html": html,
            "scores": scores,
            "avg": avg,
        })

    overall_avg = sum(r["avg"] for r in results) / len(results) if results else 0
    return results, round(overall_avg, 2)


def propose_change(current_scores, prompt_texts, focus=None, history=None):
    """Ask the optimizer LLM to propose a prompt edit."""
    client = anthropic.Anthropic()

    # Build the scores summary
    scores_summary = []
    for r in current_scores:
        route_scores = r["scores"].get("scores", {})
        weaknesses = r["scores"].get("key_weaknesses", [])
        scores_summary.append(
            f"Route: {r['route']} (avg: {r['avg']}/10)\n"
            + "\n".join(
                f"  {k}: {v.get('score', '?')}/10 — {v.get('notes', '')}"
                for k, v in route_scores.items()
            )
            + "\nWeaknesses: " + "; ".join(weaknesses)
        )

    history_text = ""
    if history:
        history_text = "\n\nPREVIOUS ATTEMPTS (avoid repeating these):\n"
        for h in history:
            delta = h.get("delta", 0)
            result = "KEPT" if delta > 0 else "REVERTED"
            history_text += (
                f"- [{result} delta={delta:+.2f}] {h['category_targeted']}: "
                f"{h['reasoning']}\n"
            )

    focus_text = ""
    if focus:
        focus_text = f"\n\nFOCUS AREA: Prioritize improving the '{focus}' category.\n"

    user_msg = (
        f"CURRENT SCORES:\n\n"
        + "\n\n".join(scores_summary)
        + f"\n\nCURRENT system.txt ({len(prompt_texts['system'])} chars):\n"
        + prompt_texts["system"]
        + f"\n\nCURRENT briefing.txt ({len(prompt_texts['briefing'])} chars):\n"
        + prompt_texts["briefing"]
        + history_text
        + focus_text
        + "\n\nPropose ONE targeted edit to improve the weakest area."
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=OPTIMIZER_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text

    # Parse JSON from response
    json_text = text
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if m:
            json_text = m.group(1)

    try:
        proposal = json.loads(json_text)
    except json.JSONDecodeError:
        print(f"  Warning: could not parse optimizer response:\n{text[:500]}")
        return None

    return proposal


def apply_change(proposal):
    """Apply a find/replace edit to the target prompt file. Returns True if successful."""
    target = proposal["target_file"]
    path = os.path.join(_PROMPTS_DIR, target)

    with open(path, "r") as f:
        content = f.read()

    find_text = proposal["find"]
    replace_text = proposal["replace"]

    if find_text not in content:
        print(f"  ERROR: find text not found in {target}")
        print(f"  Looking for: {find_text[:100]}...")
        return False

    if content.count(find_text) > 1:
        print(f"  ERROR: find text matches {content.count(find_text)} locations in {target}")
        return False

    new_content = content.replace(find_text, replace_text, 1)
    with open(path, "w") as f:
        f.write(new_content)

    delta_chars = len(new_content) - len(content)
    print(f"  Applied to {target} ({delta_chars:+d} chars)")
    return True


def revert_change(proposal):
    """Revert a previously applied change."""
    target = proposal["target_file"]
    path = os.path.join(_PROMPTS_DIR, target)

    with open(path, "r") as f:
        content = f.read()

    # Reverse the find/replace
    new_content = content.replace(proposal["replace"], proposal["find"], 1)
    with open(path, "w") as f:
        f.write(new_content)
    print(f"  Reverted {target}")


def git_commit(proposal, delta):
    """Commit the prompt change with metadata."""
    target = proposal["target_file"]
    path = os.path.join(_PROMPTS_DIR, target)

    subprocess.run(["git", "add", path], cwd=_SCRIPT_DIR, check=True)
    msg = (
        f"optimize: {proposal['category_targeted']} ({delta:+.2f} avg)\n\n"
        f"{proposal['reasoning']}\n\n"
        f"Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
    )
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=_SCRIPT_DIR,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Automated prompt optimization loop")
    parser.add_argument("--iterations", type=int, default=3,
                        help="Number of optimization rounds (default: 3)")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Model for generating briefings")
    parser.add_argument("--judge-model", default="claude-sonnet-4-6",
                        help="Model for scoring briefings")
    parser.add_argument("--focus", default=None,
                        help="Category to focus optimization on")
    parser.add_argument("--dry-run", action="store_true",
                        help="Propose changes without applying them")
    parser.add_argument("--test-set", nargs="+", default=None,
                        help="Cache prefixes to use as test set")
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="Minimum score improvement to keep a change (default: 0.0)")
    args = parser.parse_args()

    test_prefixes = args.test_set or DEFAULT_TEST_SET

    # Load test flights
    print("Loading test flights ...")
    flights = []
    for prefix in test_prefixes:
        flight = load_test_flight(prefix)
        if flight:
            route = f"{flight['origin']}->{flight['destination']}"
            print(f"  {route} ({flight['departure_str']}, {flight['altitude_ft']:,} ft)")
            flights.append(flight)
        else:
            print(f"  SKIP {prefix} — cache not found")

    if not flights:
        sys.exit("No test flights available. Run some flights with --cache first.")

    print(f"\nTest set: {len(flights)} flights")

    # Baseline scores
    print("\n" + "=" * 60)
    print("BASELINE — scoring current prompt")
    print("=" * 60)
    baseline_results, baseline_avg = run_and_score(
        flights, model=args.model, judge_model=args.judge_model
    )
    print(f"\n  Baseline average: {baseline_avg}/10")

    # Load current prompts
    with open(os.path.join(_PROMPTS_DIR, "system.txt"), "r") as f:
        system_text = f.read()
    with open(os.path.join(_PROMPTS_DIR, "briefing.txt"), "r") as f:
        briefing_text = f.read()

    history = []
    current_avg = baseline_avg
    current_results = baseline_results

    for iteration in range(1, args.iterations + 1):
        print(f"\n{'=' * 60}")
        print(f"ITERATION {iteration}/{args.iterations} — current avg: {current_avg}/10")
        print("=" * 60)

        # Reload prompt texts (may have changed in previous iteration)
        with open(os.path.join(_PROMPTS_DIR, "system.txt"), "r") as f:
            prompt_texts = {"system": f.read()}
        with open(os.path.join(_PROMPTS_DIR, "briefing.txt"), "r") as f:
            prompt_texts["briefing"] = f.read()

        # Propose a change
        print("\n  Proposing change ...")
        proposal = propose_change(current_results, prompt_texts, args.focus, history)
        if not proposal:
            print("  No valid proposal. Skipping iteration.")
            continue

        print(f"  Target: {proposal['target_file']} — {proposal['category_targeted']}")
        print(f"  Reasoning: {proposal['reasoning']}")
        print(f"  Find: {proposal['find'][:80]}...")
        print(f"  Replace: {proposal['replace'][:80]}...")

        if args.dry_run:
            print("  [DRY RUN] Skipping apply/evaluate/commit")
            continue

        # Apply
        if not apply_change(proposal):
            print("  Skipping — could not apply change")
            history.append({
                "category_targeted": proposal["category_targeted"],
                "reasoning": proposal["reasoning"],
                "delta": 0,
                "status": "FAILED_APPLY",
            })
            continue

        # Evaluate
        print("\n  Evaluating new prompt ...")
        new_results, new_avg = run_and_score(
            flights, model=args.model, judge_model=args.judge_model
        )

        delta = round(new_avg - current_avg, 2)
        print(f"\n  New average: {new_avg}/10 (delta: {delta:+.2f})")

        # Decide
        if delta > args.threshold:
            print(f"  KEEP — improvement of {delta:+.2f}")
            git_commit(proposal, delta)
            current_avg = new_avg
            current_results = new_results
            history.append({
                "category_targeted": proposal["category_targeted"],
                "reasoning": proposal["reasoning"],
                "delta": delta,
                "status": "KEPT",
            })
        else:
            print(f"  REVERT — no improvement (delta: {delta:+.2f}, threshold: {args.threshold})")
            revert_change(proposal)
            history.append({
                "category_targeted": proposal["category_targeted"],
                "reasoning": proposal["reasoning"],
                "delta": delta,
                "status": "REVERTED",
            })

    # Summary
    print(f"\n{'=' * 60}")
    print("OPTIMIZATION SUMMARY")
    print("=" * 60)
    print(f"  Baseline:  {baseline_avg}/10")
    print(f"  Final:     {current_avg}/10")
    print(f"  Net delta: {current_avg - baseline_avg:+.2f}")
    print(f"\n  History:")
    for h in history:
        print(f"    [{h['status']:>8}] {h['delta']:+.2f}  {h['category_targeted']}: {h['reasoning']}")

    # Save run log
    log_path = os.path.join(
        _SCRIPT_DIR,
        f"optimize_log_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%MZ')}.json",
    )
    log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline_avg": baseline_avg,
        "final_avg": current_avg,
        "iterations": args.iterations,
        "test_set": test_prefixes,
        "model": args.model,
        "judge_model": args.judge_model,
        "history": history,
    }
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"\n  Log saved → {log_path}")


if __name__ == "__main__":
    main()
