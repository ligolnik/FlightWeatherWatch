#!/usr/bin/env python3
"""
FlightWeatherWatch — Aviation weather briefing from WPC prog charts + Claude.

Outputs a self-contained HTML file with embedded charts.

Usage:
    python3 flightweather.py <origin> <destination> <date> <time_utc> <altitude_ft>

Examples:
    python3 flightweather.py KORD KJFK 2026-03-12 14:00 8000
    python3 flightweather.py KMQY KEDC 2026-03-16 15:00 12000
    python3 flightweather.py --all KDEN KPHX 2026-03-11 06:00 10500
"""

import argparse
import base64
import json
import os
import re
import sys
import webbrowser
from datetime import datetime, timezone
from typing import Optional

import httpx
import anthropic

# Load .env if present (keeps API key out of the environment)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())


# ---------------------------------------------------------------------------
# Chart catalogs
# ---------------------------------------------------------------------------

# WPC Surface Progs — always included for pattern development
SHORT_TERM_CHARTS = [
    (6,  "https://www.wpc.ncep.noaa.gov/basicwx/91fndfd.jpg",  "6-hr Surface Prog"),
    (12, "https://www.wpc.ncep.noaa.gov/basicwx/92fndfd.jpg",  "12-hr Surface Prog"),
    (18, "https://www.wpc.ncep.noaa.gov/basicwx/93fndfd.jpg",  "18-hr Surface Prog"),
    (24, "https://www.wpc.ncep.noaa.gov/basicwx/94fndfd.jpg",  "24-hr Surface Prog"),
    (30, "https://www.wpc.ncep.noaa.gov/basicwx/95fndfd.jpg",  "30-hr Surface Prog"),
    (36, "https://www.wpc.ncep.noaa.gov/basicwx/96fndfd.jpg",  "36-hr Surface Prog"),
    (48, "https://www.wpc.ncep.noaa.gov/basicwx/98fndfd.jpg",  "48-hr Surface Prog"),
    (60, "https://www.wpc.ncep.noaa.gov/basicwx/99fndfd.jpg",  "60-hr Surface Prog"),
]

EXTENDED_CHARTS = [
    (3,  "https://www.wpc.ncep.noaa.gov/medr/9jhwbg_conus.gif", "Day 3 Extended Prog"),
    (4,  "https://www.wpc.ncep.noaa.gov/medr/9khwbg_conus.gif", "Day 4 Extended Prog"),
    (5,  "https://www.wpc.ncep.noaa.gov/medr/9lhwbg_conus.gif", "Day 5 Extended Prog"),
    (6,  "https://www.wpc.ncep.noaa.gov/medr/9mhwbg_conus.gif", "Day 6 Extended Prog"),
    (7,  "https://www.wpc.ncep.noaa.gov/medr/9nhwbg_conus.gif", "Day 7 Extended Prog"),
]

QPF_CHARTS = [
    # Day 1 — 6-hr panels
    (6,  "https://www.wpc.ncep.noaa.gov/qpf/fill_91ewbg.gif",  "QPF Day1 00-06hr"),
    (12, "https://www.wpc.ncep.noaa.gov/qpf/fill_92ewbg.gif",  "QPF Day1 06-12hr"),
    (18, "https://www.wpc.ncep.noaa.gov/qpf/fill_93ewbg.gif",  "QPF Day1 12-18hr"),
    (24, "https://www.wpc.ncep.noaa.gov/qpf/fill_9eewbg.gif",  "QPF Day1 18-24hr"),
    (30, "https://www.wpc.ncep.noaa.gov/qpf/fill_9fewbg.gif",  "QPF Day1 24-30hr"),
    # Day 2 — 24hr total + 6-hr panels
    (48, "https://www.wpc.ncep.noaa.gov/qpf/fill_98qwbg.gif",  "QPF Day2 24hr Total"),
    (36, "https://www.wpc.ncep.noaa.gov/qpf/fill_9gewbg.gif",  "QPF Day2 30-36hr"),
    (42, "https://www.wpc.ncep.noaa.gov/qpf/fill_9hewbg.gif",  "QPF Day2 36-42hr"),
    (48, "https://www.wpc.ncep.noaa.gov/qpf/fill_9iewbg.gif",  "QPF Day2 42-48hr"),
    (54, "https://www.wpc.ncep.noaa.gov/qpf/fill_9jewbg.gif",  "QPF Day2 48-54hr"),
    # Day 3 — 24hr total + 6-hr panels
    (72, "https://www.wpc.ncep.noaa.gov/qpf/fill_99qwbg.gif",  "QPF Day3 24hr Total"),
    (60, "https://www.wpc.ncep.noaa.gov/qpf/fill_9kewbg.gif",  "QPF Day3 54-60hr"),
    (66, "https://www.wpc.ncep.noaa.gov/qpf/fill_9lewbg.gif",  "QPF Day3 60-66hr"),
    (72, "https://www.wpc.ncep.noaa.gov/qpf/fill_9oewbg.gif",  "QPF Day3 66-72hr"),
    (78, "https://www.wpc.ncep.noaa.gov/qpf/fill_9newbg.gif",  "QPF Day3 72-78hr"),
    (84, "https://www.wpc.ncep.noaa.gov/qpf/fill_9pewbg.gif",  "QPF Day3 78-84hr"),
    (90, "https://www.wpc.ncep.noaa.gov/qpf/fill_9qewbg.gif",  "QPF Day3 84-90hr"),
]

# ---------------------------------------------------------------------------
# AWC products (aviationweather.gov)  — available 0-18 hrs out
#
# URL patterns reverse-engineered from:
#   https://aviationweather.gov/assets/index-QLTrhXUA.js
#
# Base: https://aviationweather.gov/data/products/{dir}/
#
# Icing (FIP)   : F{HH}_fip_{LVL}_{field}.gif
#   fhr  : 00 01 02 03 06 09 12 15 18
#   level: 010 030 060 090 120 150 180 210 240 270 max
#   field: prob sev sevsld
#
# Turbulence (GTG): F{HH}_gtg_{LVL}_{field}.gif
#   fhr  : 00 01 02 03 06 09 12 15 18
#   level: 010 030 060 090 120 150 180 210 240 270 300 360 420 480 maxb maxa
#   field: cat mtw total
#
# GAIRMET       : F{HH}_gairmet_{field}_{region}.gif
#   fhr  : 00 03 06 09 12
#   field: sierra tango zulu-f zulu-i
#   region: us (we use CONUS)
#
# SIGMET        : sigmet_{field}.gif
#   field: all cb ic if tb   (current snapshot, no forecast hour)
#
# SigWx Low     : {pckg}_sigwx_lo_us.gif     pckg: 00 06 12 18
# SigWx Mid     : {pckg}_sigwx_mid_nat.gif   pckg: 00 06 12 18
# ---------------------------------------------------------------------------

AWC_BASE = "https://aviationweather.gov/data/products"

ICING_LEVELS = [10, 30, 60, 90, 120, 150, 180, 210, 240, 270]
TURBULENCE_LEVELS = [10, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 360, 420, 480]

AWC_FHRS = ["00", "03", "06", "09", "12", "15", "18"]
GAIRMET_FHRS = ["00", "03", "06", "09", "12"]


def _nearest_level(altitude_ft, levels):
    """Return the level (in hundreds of feet) closest to the given altitude."""
    fl = altitude_ft // 100
    return min(levels, key=lambda x: abs(x - fl))


# ---------------------------------------------------------------------------
# Chart selection
# ---------------------------------------------------------------------------

def select_charts(hours_until, altitude_ft):
    """Select relevant charts based on hours until departure and cruise altitude.

    Strategy:
    - ALL short-term surface progs always included (shows weather development)
    - Extended progs added when flight is ≥48 hrs out
    - QPF panels for the departure window
    - AWC products (icing, turbulence, G-AIRMET, SIGMET, SigWx) only when
      the flight is within their forecast window (≤18 hrs out)
    """
    seen = set()
    unique = []

    def _add(item):
        if item[1] not in seen:
            seen.add(item[1])
            unique.append(item)

    # Always include ALL short-term prog charts — pattern evolution
    for item in SHORT_TERM_CHARTS:
        _add(item)

    # Extended progs for longer-range flights
    if hours_until >= 48:
        for item in EXTENDED_CHARTS:
            _add(item)

    # QPF selection
    if hours_until <= 30:
        for h, u, l in QPF_CHARTS:
            if l.startswith("QPF Day1") and h <= hours_until + 12:
                _add((h, u, l))
    if 24 <= hours_until <= 72:
        for h, u, l in QPF_CHARTS:
            if l.startswith("QPF Day2"):
                if "24hr Total" in l or h <= hours_until + 12:
                    _add((h, u, l))
    if 48 <= hours_until <= 90:
        for h, u, l in QPF_CHARTS:
            if l.startswith("QPF Day3"):
                if "24hr Total" in l or h <= hours_until + 12:
                    _add((h, u, l))
    if hours_until > 60:
        for h, u, l in QPF_CHARTS:
            if "24hr Total" in l:
                _add((h, u, l))

    # AWC products — only if flight is within the forecast window
    if hours_until <= 18:
        _add_awc_products(unique, seen, hours_until, altitude_ft)
    elif hours_until <= 30:
        # ETCF extends to 30 hrs — add it even beyond the 18-hr AWC window
        etcf_fhrs = _pick_bracket_fhrs(
            hours_until, ["10", "12", "14", "16", "18", "20", "22", "24", "26", "28", "30"])
        for fhr_num in etcf_fhrs:
            fhr = f"{fhr_num:02d}"
            _add((fhr_num, f"{AWC_BASE}/etcf/F{fhr}_etcf.gif",
                  f"ETCF +{fhr}hr"))

    return unique


def _pick_bracket_fhrs(hours_until, available_fhrs):
    """Pick the 2-3 forecast frames that bracket the departure time."""
    nums = sorted(int(f) for f in available_fhrs)
    before = [n for n in nums if n <= hours_until]
    after = [n for n in nums if n > hours_until]
    picked = set()
    # Always include current (F00)
    picked.add(nums[0])
    # Last frame at or before departure
    if before:
        picked.add(before[-1])
    # First frame after departure
    if after:
        picked.add(after[0])
    return sorted(picked)


def _add_awc_products(unique, seen, hours_until, altitude_ft):
    """Append icing, turbulence, G-AIRMET, SIGMET, and SigWx charts."""

    def _add(item):
        if item[1] not in seen:
            seen.add(item[1])
            unique.append(item)

    ice_lvl = f"{_nearest_level(altitude_ft, ICING_LEVELS):03d}"
    turb_lvl = f"{_nearest_level(altitude_ft, TURBULENCE_LEVELS):03d}"

    # Pick bracketing frames (not every 3-hr step)
    fip_fhrs = _pick_bracket_fhrs(hours_until, AWC_FHRS)
    gairmet_fhrs = _pick_bracket_fhrs(hours_until, GAIRMET_FHRS)

    for fhr_num in fip_fhrs:
        fhr = f"{fhr_num:02d}"
        # Icing probability + severity + SLD
        _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_prob.gif",
              f"Icing Prob +{fhr}hr FL{ice_lvl}"))
        _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_sev.gif",
              f"Icing Sev +{fhr}hr FL{ice_lvl}"))
        _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_sevsld.gif",
              f"Icing SLD +{fhr}hr FL{ice_lvl}"))

        # Turbulence — total (CAT + MWT combined)
        _add((fhr_num, f"{AWC_BASE}/turbulence/F{fhr}_gtg_{turb_lvl}_total.gif",
              f"Turb Total +{fhr}hr FL{turb_lvl}"))

    # G-AIRMET — all four hazard types, CONUS (bracketing frames)
    for fhr_num in gairmet_fhrs:
        fhr = f"{fhr_num:02d}"
        for field, label in [
            ("sierra", "IFR/Mtn Obscn"),
            ("tango", "Turb/LLWS"),
            ("zulu-f", "Freezing"),
            ("zulu-i", "Icing"),
        ]:
            _add((fhr_num, f"{AWC_BASE}/gairmet/F{fhr}_gairmet_{field}_us.gif",
                  f"G-AIRMET {label} +{fhr}hr"))

    # GFA — clouds + surface, CONUS
    gfa_fhrs = _pick_bracket_fhrs(hours_until, ["03", "06", "09", "12", "15", "18"])
    for fhr_num in gfa_fhrs:
        fhr = f"{fhr_num:02d}"
        _add((fhr_num, f"{AWC_BASE}/gfa/F{fhr}_gfa_clouds_us.png",
              f"GFA Clouds +{fhr}hr"))
        _add((fhr_num, f"{AWC_BASE}/gfa/F{fhr}_gfa_sfc_us.png",
              f"GFA Surface +{fhr}hr"))

    # TCF — Terminal Ceiling & Flight Rules (4-8 hr)
    tcf_fhrs = _pick_bracket_fhrs(hours_until, ["04", "06", "08"])
    for fhr_num in tcf_fhrs:
        fhr = f"{fhr_num:02d}"
        _add((fhr_num, f"{AWC_BASE}/tcf/F{fhr}_tcf.gif",
              f"TCF +{fhr}hr"))

    # ETCF — Extended TCF (10-30 hr)
    if hours_until >= 8:
        etcf_fhrs = _pick_bracket_fhrs(
            hours_until, ["10", "12", "14", "16", "18", "20", "22", "24", "26", "28", "30"])
        for fhr_num in etcf_fhrs:
            fhr = f"{fhr_num:02d}"
            _add((fhr_num, f"{AWC_BASE}/etcf/F{fhr}_etcf.gif",
                  f"ETCF +{fhr}hr"))

    # SIGMET — current snapshot, just the combined view
    _add((0, f"{AWC_BASE}/sigmet/sigmet_all.gif", "SIGMET Current"))

    # SigWx Low Level — pick frame closest to departure
    swl_fhrs = _pick_bracket_fhrs(hours_until, ["00", "06", "12", "18"])
    for fhr_num in swl_fhrs:
        pckg = f"{fhr_num:02d}"
        _add((fhr_num, f"{AWC_BASE}/swl/{pckg}_sigwx_lo_us.gif",
              f"SigWx Low +{pckg}hr"))

    # SigWx Mid Level — only if cruising above FL180
    if altitude_ft >= 18000:
        for fhr_num in swl_fhrs:
            pckg = f"{fhr_num:02d}"
            _add((fhr_num, f"{AWC_BASE}/swm/{pckg}_sigwx_mid_nat.gif",
                  f"SigWx Mid +{pckg}hr"))


def all_charts(altitude_ft=10000):
    """Return every chart in every catalog (used with --all flag)."""
    seen = set()
    out = []

    def _add(item):
        if item[1] not in seen:
            seen.add(item[1])
            out.append(item)

    for item in SHORT_TERM_CHARTS + EXTENDED_CHARTS + QPF_CHARTS:
        _add(item)

    # Add all AWC products at the given altitude
    _add_awc_products(out, seen, 18, altitude_ft)
    return out


# ---------------------------------------------------------------------------
# Image fetching — returns (label, url, base64_data, media_type)
# ---------------------------------------------------------------------------

def fetch_chart(url, label):
    # type: (...) -> Optional[tuple]
    try:
        print(f"  Fetching {label} ... ", end="", flush=True)
        r = httpx.get(url, timeout=20, follow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "gif" in ct or url.lower().endswith(".gif"):
            media_type = "image/gif"
        elif "png" in ct or url.lower().endswith(".png"):
            media_type = "image/png"
        else:
            media_type = "image/jpeg"
        encoded = base64.standard_b64encode(r.content).decode("utf-8")
        print("OK")
        return (label, url, encoded, media_type)
    except Exception as exc:
        print(f"FAILED ({exc})")
        return None


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wx Brief — {origin} → {destination} {dep_date}</title>
<style>
  :root {{
    --bg:      #0d1117;
    --surface: #161b22;
    --raised:  #1c2128;
    --border:  #30363d;
    --text:    #e6edf3;
    --muted:   #8b949e;
    --green:   #3fb950;
    --amber:   #d29922;
    --red:     #f85149;
    --blue:    #58a6ff;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding-bottom: 4rem;
  }}

  /* ── Header ── */
  header {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 1.25rem 2rem;
  }}
  .header-top {{
    display: flex;
    align-items: baseline;
    gap: 1.25rem;
    flex-wrap: wrap;
    margin-bottom: 0.4rem;
  }}
  header h1 {{
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--blue);
    font-family: "SF Mono", "Fira Code", monospace;
    letter-spacing: 0.05em;
  }}
  .badge {{
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    background: rgba(88,166,255,0.15);
    color: var(--blue);
    border: 1px solid rgba(88,166,255,0.3);
  }}
  .meta {{
    font-size: 0.8rem;
    color: var(--muted);
    display: flex;
    gap: 1.5rem;
    flex-wrap: wrap;
  }}
  .meta strong {{ color: var(--text); }}

  /* ── Layout ── */
  .container {{ max-width: 1400px; margin: 0 auto; padding: 0 2rem; }}
  section {{ margin-top: 2rem; }}
  .section-label {{
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.45rem;
    margin-bottom: 1.1rem;
  }}

  /* ── Chart gallery ── */
  .chart-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 1rem;
  }}
  .chart-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }}
  .chart-card img {{
    width: 100%;
    display: block;
    background: #fff;
  }}
  .chart-caption {{
    padding: 0.45rem 0.75rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 0.78rem;
  }}
  .chart-label {{ font-weight: 600; color: var(--text); }}
  .chart-caption a {{
    color: var(--blue);
    text-decoration: none;
    opacity: 0.75;
    font-size: 0.72rem;
  }}
  .chart-caption a:hover {{ opacity: 1; text-decoration: underline; }}

  /* ── Briefing panel ── */
  .briefing {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 2rem;
  }}
  .briefing h2 {{
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--blue);
    margin: 1.75rem 0 0.55rem;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid var(--border);
  }}
  .briefing h2:first-child {{ margin-top: 0; }}
  .briefing h3 {{
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--amber);
    margin: 1.1rem 0 0.35rem;
  }}
  .briefing h4 {{
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text);
    margin: 0.9rem 0 0.3rem;
  }}
  .briefing p {{ margin: 0.55rem 0; font-size: 0.875rem; }}
  .briefing ul, .briefing ol {{
    margin: 0.4rem 0 0.4rem 1.4rem;
    font-size: 0.875rem;
  }}
  .briefing li {{ margin: 0.2rem 0; }}
  .briefing strong {{ font-weight: 700; color: var(--text); }}
  .briefing em {{ font-style: normal; font-weight: 600; color: var(--amber); }}
  .briefing blockquote {{
    border-left: 3px solid var(--amber);
    margin: 0.9rem 0;
    padding: 0.55rem 1rem;
    background: rgba(210,153,34,0.07);
    border-radius: 0 4px 4px 0;
    font-size: 0.85rem;
    color: var(--amber);
  }}
  .briefing table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
    margin: 0.9rem 0;
  }}
  .briefing th {{
    background: rgba(88,166,255,0.1);
    color: var(--blue);
    font-weight: 600;
    text-align: left;
    padding: 0.45rem 0.7rem;
    border: 1px solid var(--border);
    font-size: 0.75rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  .briefing td {{
    padding: 0.45rem 0.7rem;
    border: 1px solid var(--border);
    vertical-align: top;
  }}
  .briefing tr:nth-child(even) td {{ background: rgba(255,255,255,0.02); }}
  .briefing code {{
    background: rgba(255,255,255,0.07);
    padding: 0.1em 0.35em;
    border-radius: 3px;
    font-family: "SF Mono","Fira Code",monospace;
    font-size: 0.82em;
    color: var(--green);
  }}
  .briefing hr {{
    border: none;
    border-top: 1px solid var(--border);
    margin: 1.5rem 0;
  }}

  /* ── Collapsible sections (synoptic + reference charts) ── */
  details.collapsible {{
    background: var(--raised);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }}
  details.collapsible summary {{
    padding: 0.85rem 1.25rem;
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 0.6rem;
    list-style: none;
    user-select: none;
  }}
  details.collapsible summary::-webkit-details-marker {{ display: none; }}
  details.collapsible summary::before {{
    content: "▶";
    font-size: 0.65rem;
    transition: transform 0.2s;
    color: var(--muted);
  }}
  details.collapsible[open] summary::before {{ transform: rotate(90deg); }}
  details.collapsible summary:hover {{ color: var(--text); }}
  details.collapsible summary .pill {{
    margin-left: auto;
    font-size: 0.65rem;
    background: rgba(255,255,255,0.06);
    padding: 0.1rem 0.5rem;
    border-radius: 10px;
    color: var(--muted);
  }}
  .collapsible-body {{
    padding: 1.25rem 1.5rem 1.5rem;
    border-top: 1px solid var(--border);
  }}
  .collapsible-body h2 {{
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--blue);
    margin: 1.4rem 0 0.5rem;
    padding-bottom: 0.25rem;
    border-bottom: 1px solid var(--border);
  }}
  .collapsible-body h2:first-child {{ margin-top: 0; }}
  .collapsible-body h3 {{
    font-size: 0.875rem;
    font-weight: 600;
    color: var(--amber);
    margin: 1rem 0 0.3rem;
  }}
  .collapsible-body p {{ font-size: 0.85rem; margin: 0.5rem 0; }}
  .collapsible-body ul, .collapsible-body ol {{
    font-size: 0.85rem;
    margin: 0.4rem 0 0.4rem 1.4rem;
  }}
  .collapsible-body li {{ margin: 0.2rem 0; }}
  .collapsible-body strong {{ font-weight: 700; }}
  .collapsible-body em {{ font-style: normal; color: var(--amber); font-weight: 600; }}
  .collapsible-body table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    margin: 0.8rem 0;
  }}
  .collapsible-body th {{
    background: rgba(88,166,255,0.08);
    color: var(--blue);
    font-weight: 600;
    text-align: left;
    padding: 0.4rem 0.65rem;
    border: 1px solid var(--border);
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .collapsible-body td {{
    padding: 0.4rem 0.65rem;
    border: 1px solid var(--border);
    vertical-align: top;
  }}
  .collapsible-body tr:nth-child(even) td {{ background: rgba(255,255,255,0.02); }}
  .collapsible-body .chart-grid {{ margin-top: 1rem; }}

  /* ── Footer ── */
  footer {{
    margin-top: 2.5rem;
    padding: 1rem 2rem;
    font-size: 0.72rem;
    color: var(--muted);
    border-top: 1px solid var(--border);
    text-align: center;
    line-height: 1.8;
  }}
  footer strong {{ color: var(--red); }}

  /* ── Chart images — never overflow on any screen ── */
  .chart-card img, img {{ max-width: 100%; height: auto; }}

  /* ── Mobile responsive ── */
  @media (max-width: 640px) {{
    .container {{ padding: 0 1rem; }}
    header {{ padding: 1rem; }}
    .header-top {{
      flex-direction: column;
      align-items: flex-start;
      gap: 0.5rem;
    }}
    header h1 {{ font-size: 1.2rem; }}
    .meta {{
      flex-direction: column;
      gap: 0.35rem;
    }}
    .chart-grid {{
      grid-template-columns: 1fr;
    }}
    .briefing {{
      overflow-x: auto;
      padding: 1rem;
    }}
    details.collapsible {{
      width: 100%;
    }}
    .collapsible-body {{ padding: 1rem; }}
    footer {{
      padding: 1rem;
      font-size: 0.65rem;
      text-align: center;
      line-height: 2;
    }}
  }}
</style>
</head>
<body>

<header>
  <div class="header-top">
    <h1>{origin} → {destination}</h1>
    <span class="badge">{altitude_ft} ft</span>
  </div>
  <div class="meta">
    <span>Departure <strong>{dep_str}</strong></span>
    <span>Generated <strong>{generated}</strong></span>
    <span>Charts <strong>{chart_count}</strong></span>
  </div>
</header>

<div class="container">

  <section>
    <div class="section-label">Weather Charts</div>
    <details class="collapsible" open>
      <summary>
        Significant weather along route
        <span class="pill">{sig_count} charts</span>
      </summary>
      <div class="collapsible-body">
        <div class="chart-grid">
{significant_chart_cards}
        </div>
      </div>
    </details>
{reference_section}
  </section>

  <section>
    <div class="section-label">Synoptic Overview</div>
    <details class="collapsible">
      <summary>
        Background pattern — chart-by-chart detail
        <span class="pill">click to expand</span>
      </summary>
      <div class="collapsible-body">
{synoptic_html}
      </div>
    </details>
  </section>

  <section>
    <div class="section-label">Operational Briefing</div>
    <div class="briefing">
{briefing_html}
    </div>
  </section>

</div>

<footer>
  FlightWeatherWatch &middot;
  Charts: NOAA/NWS WPC + AWC &middot;
  Analysis: Claude claude-sonnet-4-6 &middot;
  <strong>NOT FOR FLIGHT PLANNING — obtain an official preflight weather briefing before departure.</strong>
</footer>

<!-- Lightbox overlay -->
<div id="lightbox" onclick="closeLightbox()">
  <button id="lb-close" onclick="closeLightbox()">&times;</button>
  <img id="lb-img" src="" alt="">
  <div id="lb-label"></div>
</div>
<style>
  #lightbox {{
    display: none;
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: rgba(0,0,0,0.92);
    justify-content: center;
    align-items: center;
    flex-direction: column;
    cursor: zoom-out;
  }}
  #lightbox.active {{ display: flex; }}
  #lb-close {{
    position: absolute;
    top: 1rem;
    right: 1.25rem;
    background: none;
    border: none;
    color: #fff;
    font-size: 2.5rem;
    cursor: pointer;
    line-height: 1;
    opacity: 0.7;
    z-index: 10000;
  }}
  #lb-close:hover {{ opacity: 1; }}
  #lb-img {{
    max-width: 95vw;
    max-height: 88vh;
    object-fit: contain;
    border-radius: 4px;
    background: #fff;
  }}
  #lb-label {{
    color: var(--muted);
    font-size: 0.8rem;
    margin-top: 0.75rem;
    text-align: center;
  }}
</style>
<script>
  document.querySelectorAll('.chart-card img').forEach(function(img) {{
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', function(e) {{
      e.stopPropagation();
      var lb = document.getElementById('lightbox');
      document.getElementById('lb-img').src = img.src;
      var label = img.closest('.chart-card').querySelector('.chart-label');
      document.getElementById('lb-label').textContent = label ? label.textContent : '';
      lb.classList.add('active');
      document.body.style.overflow = 'hidden';
    }});
  }});
  function closeLightbox() {{
    document.getElementById('lightbox').classList.remove('active');
    document.body.style.overflow = '';
  }}
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') closeLightbox();
  }});
</script>

</body>
</html>
"""

REFERENCE_SECTION_TEMPLATE = """\
    <details class="collapsible" style="margin-top:1rem">
      <summary>
        Reference charts — no relevant weather along route
        <span class="pill">{ref_count} charts</span>
      </summary>
      <div class="collapsible-body">
        <div class="chart-grid">
{reference_chart_cards}
        </div>
      </div>
    </details>"""

CHART_CARD_TEMPLATE = """\
      <div class="chart-card">
        <img src="data:{media_type};base64,{b64}" alt="{label}" loading="lazy">
        <div class="chart-caption">
          <span class="chart-label">{label}</span>
          <a href="{url}" target="_blank" rel="noopener">Source ↗</a>
        </div>
      </div>"""


# ---------------------------------------------------------------------------
# Claude analysis — returns (synoptic_html, briefing_html, significant_labels)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a highly experienced CFI/CFII and charter pilot talking directly to a fellow pilot. "
    "Your job is to help them decide whether to fly, when to fly, and how to fly it safely. "
    "Write like a pilot, not a meteorologist. Use plain language — 'expect a rough ride over Arkansas', "
    "'you'll likely be in the soup on departure', 'that front will nail your arrival window'. "
    "Skip the textbook synoptic descriptions. Focus on what the pilot will actually see, feel, and deal with. "
    "Be direct and opinionated. If it looks bad, say so clearly. If it looks fine, say that too. "
    "Respond only with HTML body content using the tags specified. No outer html/head/body/style tags."
)

def analyze(origin, destination, departure_dt, altitude_ft, chart_data):
    """
    Two-pass Claude query:
      1. Synoptic overview — broad pattern analysis + chart significance classification
      2. Operational briefing — focused on day-before and day-of at planned altitude

    chart_data: list of (label, url, b64, media_type)
    Returns (synoptic_html, briefing_html, significant_labels)
    """
    client = anthropic.Anthropic()
    dep_str = departure_dt.strftime("%Y-%m-%d %H:%MZ")

    # Build shared image content block
    image_blocks = []
    chart_labels = []
    for label, url, b64, media_type in chart_data:
        chart_labels.append(label)
        image_blocks.append({"type": "text", "text": f"--- {label} ---"})
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })

    flight_header = f"""
FLIGHT
  Route        : {origin.upper()} → {destination.upper()}
  Departure    : {dep_str} UTC
  Planned Alt  : {altitude_ft:,} ft MSL
  Charts       : {len(chart_data)} weather charts
"""

    label_list = "\n".join(f"  - {l}" for l in chart_labels)

    # ── Pass 1: Synoptic overview + chart classification ─────────────────
    synoptic_prompt = image_blocks + [{
        "type": "text",
        "text": flight_header + f"""
TASK — BACKGROUND PATTERN + CHART CLASSIFICATION

FIRST, output a JSON line classifying which charts show significant weather
hazards relevant to this specific flight route ({origin.upper()} → {destination.upper()}).
A chart is "significant" if it shows relevant weather (fronts, precip, icing,
turbulence, IFR conditions, SIGMETs, wind shifts, etc.) that intersect or
affect the planned route, departure/arrival airports, or alternates.
Charts showing no relevant weather along the route go in the reference pile.

Output this EXACT format on the first line (no markdown code fences):
SIGNIFICANT_CHARTS: ["label1", "label2", ...]

All chart labels:
{label_list}

THEN a blank line, then your HTML analysis.

Walk through each chart chronologically. Describe what's moving where and how it's evolving.
This is the "nerd section" — technical chart reading for the curious pilot who wants the full picture.
Keep it factual: what systems are where, where they're headed, what they're doing.

Respond with HTML using: h2, h3, p, ul, ol, li, strong, em, table, thead, tbody, tr, th, td, blockquote.
Do NOT include html/head/body/style/script tags.""",
    }]

    print(f"\nPass 1/2 — Synoptic overview ", end="", flush=True)
    synoptic_html = ""
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": synoptic_prompt}],
    ) as stream:
        for text in stream.text_stream:
            synoptic_html += text
            print(".", end="", flush=True)
    print(" done.")

    # Parse SIGNIFICANT_CHARTS from the first line
    significant_labels = set()
    sig_match = re.match(r'SIGNIFICANT_CHARTS:\s*(\[.*?\])', synoptic_html, re.DOTALL)
    if sig_match:
        try:
            labels = json.loads(sig_match.group(1))
            significant_labels = set(labels)
        except (json.JSONDecodeError, TypeError):
            pass
        # Strip the classification line from the displayed HTML
        synoptic_html = synoptic_html[sig_match.end():].lstrip("\n")

    # Fallback: if parsing failed, treat all charts as significant
    if not significant_labels:
        significant_labels = set(chart_labels)

    # ── Pass 2: Operational briefing ──────────────────────────────────────
    briefing_prompt = image_blocks + [{
        "type": "text",
        "text": flight_header + f"""
TASK — PILOT BRIEFING (the main briefing a pilot actually reads)

This is what a weather-savvy CFI would tell you over the phone before your flight.
Write in plain pilot language. No meteorology lectures. Answer the questions pilots actually ask:
"Can I get out of Nashville?", "Will I hit ice at 12 grand?", "What's Austin doing when I get there?",
"Should I just wait until tomorrow?". Be direct. Be opinionated. Ground it in what the charts show.

Do NOT repeat the background pattern analysis — that's in a separate section.
Focus ONLY on the day before and the day/time of the flight.

REQUIRED SECTIONS (use these exact h2 headings):

<h2>The Day Before — What to Watch</h2>
What's the situation the evening before? What weather check should the pilot do that night?
Tell them what to look for and what would change the go/no-go. Keep it short and practical.

<h2>Leaving {origin.upper()} — Departure at {dep_str}</h2>
Will they get out? What are conditions like on the ground and climbing out?
Talk about what they'll see: are they punching through a layer, is it clear, is there a front nearby?
Winds on the runway, ceilings, visibility — pilot language, not METAR codes.

<h2>The Ride at {altitude_ft:,} ft</h2>
What's the flight actually going to be like at {altitude_ft:,} ft?
- Will they be in ice? Is {altitude_ft:,} ft above or below the freezing level?
- Smooth or bumpy? Where are the rough spots?
- Headwind or tailwind? Rough estimate of the wind effect at that altitude.
- Any weather to dodge or plan around?
Use a table: Hazard | Risk | Leg | What to Expect
Incorporate findings from icing (FIP), turbulence (GTG), G-AIRMET, SIGMET, SigWx, and QPF charts.
Heavy QPF near the route means IMC, potential icing, and possible convection.

<h2>Getting Into {destination.upper()}</h2>
What does the pilot walk into on arrival? Estimate block time and describe arrival conditions.
Is there a front nearby? Are ceilings dropping? Is it a non-event? Pick good alternates.

<h2>Fly or No</h2>
Bottom line up front. Start with a clear verdict: GO / MARGINAL GO / NO-GO.
Hard stops first. Then the stuff to watch. Be opinionated — this is what the pilot needs.

<h2>If You Go — Do This</h2>
Concrete action items. Departure time tweak, altitude change, specific alternates, fuel stop,
what forecast products to check the night before and morning of.

Respond with HTML using: h2, h3, h4, p, ul, ol, li, strong, em, blockquote, table, thead, tbody, tr, th, td, code, hr.
No html/head/body/style/script tags.""",
    }]

    print(f"Pass 2/2 — Operational briefing ", end="", flush=True)
    briefing_html = ""
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": briefing_prompt}],
    ) as stream:
        for text in stream.text_stream:
            briefing_html += text
            print(".", end="", flush=True)
    print(" done.")

    return synoptic_html, briefing_html, significant_labels


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aviation weather briefing from WPC prog charts + Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("origin",      help="Departure airport ICAO (e.g. KMQY)")
    parser.add_argument("destination", help="Arrival airport ICAO  (e.g. KEDC)")
    parser.add_argument("date",        help="Departure date UTC (YYYY-MM-DD)")
    parser.add_argument("time",        help="Departure time UTC (HH:MM)")
    parser.add_argument("altitude",    help="Planned cruise altitude in feet (e.g. 12000)", type=int)
    parser.add_argument("--all",       action="store_true",
                        help="Fetch all available charts instead of auto-selecting")
    parser.add_argument("--no-open",   action="store_true",
                        help="Save HTML but do not open browser automatically")
    parser.add_argument("--cache",      action="store_true",
                        help="Save LLM output + chart data to a cache file for re-rendering")
    parser.add_argument("--from-cache", metavar="FILE",
                        help="Skip fetching/analysis — rebuild HTML from a cache file")
    args = parser.parse_args()

    try:
        departure_dt = datetime.strptime(
            f"{args.date} {args.time}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit("Error: Use YYYY-MM-DD for date and HH:MM for time (UTC).")

    now_utc = datetime.now(timezone.utc)
    hours_until = (departure_dt - now_utc).total_seconds() / 3600

    print(f"\nFlight   : {args.origin.upper()} → {args.destination.upper()}")
    print(f"Departs  : {departure_dt.strftime('%Y-%m-%d %H:%MZ')} ({hours_until:+.1f} hrs from now)")
    print(f"Altitude : {args.altitude:,} ft MSL")

    if hours_until < -2:
        sys.exit("Error: Departure time is more than 2 hours in the past.")
    if hours_until > 7 * 24:
        print("Warning: >7 days out — extended prog reliability is very low.")

    if args.from_cache:
        # ── Rebuild from cache — no fetching, no API calls ──────────────
        print(f"\nLoading cache: {args.from_cache}")
        with open(args.from_cache, "r", encoding="utf-8") as f:
            cache = json.load(f)
        chart_data = [tuple(c) for c in cache["chart_data"]]
        synoptic_html = cache["synoptic_html"]
        briefing_html = cache["briefing_html"]
        significant_labels = set(cache["significant_labels"])
        print(f"  {len(chart_data)} charts, {len(significant_labels)} significant")
    else:
        # ── Normal flow: fetch charts + run analysis ────────────────────
        if hours_until <= 18:
            print(f"AWC products: Icing FL{_nearest_level(args.altitude, ICING_LEVELS):03d}, "
                  f"Turb FL{_nearest_level(args.altitude, TURBULENCE_LEVELS):03d}, "
                  f"G-AIRMET, SIGMET, SigWx")
        else:
            print("AWC products: skipped (>18 hrs out)")

        chart_list = (all_charts(args.altitude) if args.all
                      else select_charts(max(hours_until, 0), args.altitude))
        print(f"\nFetching {len(chart_list)} chart(s):")

        chart_data = []
        for _, url, label in chart_list:
            result = fetch_chart(url, label)
            if result:
                chart_data.append(result)

        if not chart_data:
            sys.exit("Error: Could not fetch any charts. Check your internet connection.")

        synoptic_html, briefing_html, significant_labels = analyze(
            args.origin, args.destination, departure_dt, args.altitude, chart_data
        )

        if args.cache:
            cache_obj = {
                "origin": args.origin.upper(),
                "destination": args.destination.upper(),
                "departure": departure_dt.strftime("%Y-%m-%d %H:%MZ"),
                "altitude_ft": args.altitude,
                "chart_data": chart_data,
                "synoptic_html": synoptic_html,
                "briefing_html": briefing_html,
                "significant_labels": sorted(significant_labels),
            }
            dep_str_safe = departure_dt.strftime("%Y-%m-%d")
            cache_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                f"cache_{args.origin.upper()}_{args.destination.upper()}_{dep_str_safe}.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_obj, f)
            print(f"\nCache saved → {cache_path}")

    # Split charts into significant / reference
    sig_cards = []
    ref_cards = []
    for label, url, b64, media_type in chart_data:
        card = CHART_CARD_TEMPLATE.format(
            label=label, url=url, b64=b64, media_type=media_type
        )
        if label in significant_labels:
            sig_cards.append(card)
        else:
            ref_cards.append(card)

    reference_section = ""
    if ref_cards:
        reference_section = REFERENCE_SECTION_TEMPLATE.format(
            ref_count=len(ref_cards),
            reference_chart_cards="\n".join(ref_cards),
        )

    now_utc = datetime.now(timezone.utc)
    html = HTML_TEMPLATE.format(
        origin=args.origin.upper(),
        destination=args.destination.upper(),
        dep_date=args.date,
        dep_str=departure_dt.strftime("%Y-%m-%d %H:%MZ"),
        altitude_ft=f"{args.altitude:,}",
        generated=now_utc.strftime("%Y-%m-%d %H:%MZ"),
        chart_count=len(chart_data),
        sig_count=len(sig_cards),
        significant_chart_cards="\n".join(sig_cards),
        reference_section=reference_section,
        synoptic_html=synoptic_html,
        briefing_html=briefing_html,
    )

    fname = f"briefing_{args.origin.upper()}_{args.destination.upper()}_{args.date}.html"
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nSaved → {out_path}")
    if not args.no_open:
        webbrowser.open(f"file://{out_path}")


if __name__ == "__main__":
    main()
