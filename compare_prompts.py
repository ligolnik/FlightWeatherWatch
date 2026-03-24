#!/usr/bin/env python3
"""
Compare briefing output across prompt versions using cached chart data.

Loads charts from a cached flight, re-runs the LLM analysis with the CURRENT
prompt (prompts/system.txt + prompts/briefing.txt), and produces a side-by-side
HTML comparison with the original cached output.

Usage:
    python3 compare_prompts.py <cache_prefix> [--model MODEL] [--no-open]

Examples:
    python3 compare_prompts.py cache_KBJC_KHCR_2026-03-21
    python3 compare_prompts.py test_briefings/v1_pre_prompt_rules/cache_KMQY_KEDC_2026-03-16
    python3 compare_prompts.py cache_KSQL_KBDN_2026-03-30 --model claude-sonnet-4-6
"""

import argparse
import json
import os
import sys
import re
from datetime import datetime, timezone

# Import analyze() from flightweather.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flightweather import analyze


COMPARISON_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Prompt Comparison — {origin} → {destination} {departure}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 0; padding: 20px; background: #f5f5f5; }}
  h1 {{ text-align: center; margin-bottom: 5px; }}
  .meta {{ text-align: center; color: #666; margin-bottom: 20px; font-size: 14px; }}
  .container {{ display: flex; gap: 20px; max-width: 1800px; margin: 0 auto; }}
  .panel {{ flex: 1; background: white; border-radius: 8px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow-x: auto; }}
  .panel h2:first-child {{ margin-top: 0; color: #333; font-size: 16px;
                           border-bottom: 2px solid #ddd; padding-bottom: 8px; }}
  .panel-old h2:first-child {{ border-bottom-color: #e74c3c; }}
  .panel-new h2:first-child {{ border-bottom-color: #27ae60; }}
  .prompt-info {{ background: #f8f9fa; padding: 10px; border-radius: 4px;
                  font-size: 12px; color: #666; margin-bottom: 15px; }}
  .briefing {{ font-size: 14px; line-height: 1.6; }}
  .briefing h2 {{ color: #2c3e50; border-bottom: 1px solid #eee; padding-bottom: 5px; }}
  .briefing table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  .briefing th, .briefing td {{ border: 1px solid #ddd; padding: 6px 10px; font-size: 13px; }}
  .briefing th {{ background: #f8f9fa; }}
  @media (max-width: 1200px) {{ .container {{ flex-direction: column; }} }}
</style>
</head>
<body>
<h1>Prompt Comparison</h1>
<div class="meta">
  {origin} → {destination} | {departure} | {altitude_ft:,} ft MSL | Generated {timestamp}
</div>
<div class="container">
  <div class="panel panel-old">
    <h2>ORIGINAL (cached prompt — {old_prompt_len:,} chars)</h2>
    <div class="prompt-info">Model: {old_model} | Prompt version: {old_version}</div>
    <div class="briefing">{old_html}</div>
  </div>
  <div class="panel panel-new">
    <h2>NEW (current prompt — {new_prompt_len:,} chars)</h2>
    <div class="prompt-info">Model: {new_model} | Prompt version: current</div>
    <div class="briefing">{new_html}</div>
  </div>
</div>
</body>
</html>
"""


def detect_version(sys_prompt_len):
    """Classify prompt version by system prompt length."""
    if sys_prompt_len < 1000:
        return "v1 (minimal)"
    elif sys_prompt_len < 6000:
        return "v2 (structured rules)"
    else:
        return "v3 (enhanced)"


def main():
    parser = argparse.ArgumentParser(description="Compare prompt versions on cached flights")
    parser.add_argument("cache_prefix", help="Cache prefix (e.g., cache_KBJC_KHCR_2026-03-21)")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Model for new analysis")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    prefix = args.cache_prefix.replace("_charts.json", "").replace("_llm.json", "")
    charts_path = prefix + "_charts.json"
    llm_path = prefix + "_llm.json"

    if not os.path.exists(charts_path):
        sys.exit(f"Error: {charts_path} not found")
    if not os.path.exists(llm_path):
        sys.exit(f"Error: {llm_path} not found")

    # Load cached data
    print(f"Loading cache: {prefix}")
    with open(charts_path, "r", encoding="utf-8") as f:
        charts_cache = json.load(f)
    with open(llm_path, "r", encoding="utf-8") as f:
        llm_cache = json.load(f)

    origin = llm_cache["origin"]
    destination = llm_cache["destination"]
    departure_str = llm_cache["departure"]
    altitude_ft = llm_cache["altitude_ft"]
    chart_data = [tuple(c) for c in charts_cache["chart_data"]]
    taf_data = llm_cache.get("taf_data")
    winds_text = llm_cache.get("winds_text", "")
    afd_data = llm_cache.get("afd_data", [])
    airport_names = llm_cache.get("airport_names", {})

    # Original output
    old_html = llm_cache.get("briefing_html", llm_cache.get("synoptic_html", ""))
    old_prompts = llm_cache.get("prompts", {})
    old_model = old_prompts.get("model", "unknown")
    old_sys_len = len(old_prompts.get("system", ""))
    old_version = detect_version(old_sys_len)

    print(f"  Route: {origin} → {destination}")
    print(f"  Departure: {departure_str}")
    print(f"  Altitude: {altitude_ft:,} ft")
    print(f"  Charts: {len(chart_data)}")
    print(f"  Original prompt: {old_version} ({old_sys_len:,} chars)")

    # Parse departure datetime
    departure_dt = datetime.strptime(departure_str.replace("Z", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

    # Re-run analysis with current prompt
    print(f"\nRe-analyzing with current prompt using {args.model} ...")
    new_html, new_sig_labels, new_prompts = analyze(
        origin, destination, departure_dt, altitude_ft,
        chart_data, taf_data, winds_text, airport_names, afd_data, args.model
    )
    new_sys_len = len(new_prompts.get("system", ""))
    print(f"  New prompt: {new_sys_len:,} chars")

    # Generate comparison HTML
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    route_tag = f"{origin}_{destination}"
    dep_date = departure_dt.strftime("%Y-%m-%d")

    html = COMPARISON_HTML.format(
        origin=origin,
        destination=destination,
        departure=departure_str,
        altitude_ft=altitude_ft,
        timestamp=timestamp,
        old_html=old_html,
        new_html=new_html,
        old_model=old_model,
        new_model=args.model,
        old_prompt_len=old_sys_len,
        new_prompt_len=new_sys_len,
        old_version=old_version,
    )

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"comparison_{route_tag}_{dep_date}_{timestamp.replace(' ', '_').replace(':', '')}.html"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved → {out_path}")

    if not args.no_open:
        import subprocess
        subprocess.run(["open", out_path])


if __name__ == "__main__":
    main()
