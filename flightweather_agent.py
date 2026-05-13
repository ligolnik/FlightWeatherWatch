#!/usr/bin/env python3
"""
FlightWeatherWatch — Agent Mode

Uses Claude's tool_use API to let the model drive weather data gathering.
The model decides what to fetch, investigates further based on what it sees,
and produces the briefing iteratively.

Usage:
    python3 flightweather_agent.py <origin> [waypoints...] <destination> <date> <time_utc> <altitude_ft>
    python3 flightweather_agent.py KSQL KBDN 2026-03-15 18:00 10500 --tas 150
"""

import argparse
import json
import os
import re
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import anthropic
import httpx

# Import reusable functions from the pipeline version
from flightweather import (
    # Chart selection & fetching
    select_charts,
    all_charts,
    fetch_chart,
    draw_route_on_chart,
    # Route
    resolve_route_coords,
    compute_route_legs,
    # Weather data
    fetch_tafs,
    fetch_winds_aloft,
    fetch_afd_aviation,
    fetch_afd_for_airports,
    # Templates
    HTML_TEMPLATE,
    CHART_CARD_TEMPLATE,
    REFERENCE_SECTION_TEMPLATE,
    # Helpers
    _card_class,
    _highlight_taf_line,
    # Prompt loading
    SYSTEM_PROMPT,
    _load_prompt,
    # Constants
    SHORT_TERM_CHARTS,
    EXTENDED_CHARTS,
    QPF_CHARTS,
    AWC_BASE,
    ICING_LEVELS,
    TURBULENCE_LEVELS,
    AWC_FHRS,
    GAIRMET_FHRS,
    _nearest_level,
    _pick_bracket_fhrs,
    _compute_valid_time,
)


# ---------------------------------------------------------------------------
# Display helpers — make the agent loop visible
# ---------------------------------------------------------------------------

# ANSI colors
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_MAGENTA = "\033[35m"
_RESET = "\033[0m"


def _print_header(airports, departure_dt, altitude_ft):
    route = " -> ".join(airports)
    dep = departure_dt.strftime("%Y-%m-%d %H:%MZ")
    print()
    print(f"{_DIM}{'=' * 64}{_RESET}")
    print(f"  {_BOLD}FlightWeatherWatch — Agent Mode{_RESET}")
    print(f"  {route}  |  {dep}  |  {altitude_ft:,} ft")
    print(f"{_DIM}{'=' * 64}{_RESET}")
    print()


def _print_turn(n):
    print(f"\n{_DIM}--- Turn {n} {'-' * (52 - len(str(n)))}{_RESET}")


def _print_agent_text(text):
    """Print the model's reasoning text (non-tool-call content)."""
    if not text.strip():
        return
    for line in text.strip().split("\n"):
        print(f"  {_CYAN}[agent]{_RESET} {line}")


def _print_tool_call(name, args):
    """Print a tool call with its arguments."""
    print(f"\n  {_YELLOW}>>{_RESET} {_BOLD}{name}{_RESET}")
    for k, v in args.items():
        if isinstance(v, list):
            v_str = ", ".join(str(x) for x in v)
        elif isinstance(v, bool):
            v_str = str(v).lower()
        else:
            v_str = str(v)
        print(f"     {_DIM}{k}: {v_str}{_RESET}")


def _print_tool_result_summary(name, content):
    """Print a short summary of the tool result."""
    # For chart tools, just say how many charts
    if name == "fetch_selected_charts":
        # Count image blocks
        if isinstance(content, list):
            n_images = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image")
            labels = [c["text"].replace("--- ", "").replace(" ---", "")
                      for c in content if isinstance(c, dict) and c.get("type") == "text"
                      and c.get("text", "").startswith("---")]
            label_str = ", ".join(labels[:5])
            if len(labels) > 5:
                label_str += f", ... +{len(labels) - 5} more"
            print(f"  {_GREEN}<<{_RESET} {n_images} charts fetched [{label_str}]")
            print(f"     {_DIM}(images sent to model){_RESET}")
        else:
            print(f"  {_GREEN}<<{_RESET} {content[:120] if isinstance(content, str) else '(result)'}")
    else:
        # For text tools, show first ~120 chars
        if isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            summary = " ".join(text_parts)
        elif isinstance(content, str):
            summary = content
        else:
            summary = str(content)
        # Truncate
        summary = summary.replace("\n", " ").strip()
        if len(summary) > 140:
            summary = summary[:140] + "..."
        print(f"  {_GREEN}<<{_RESET} {summary}")


def _print_final_stats(turn_count, tool_call_count, model):
    print(f"\n{_DIM}{'=' * 64}{_RESET}")
    print(f"  {_BOLD}Briefing complete{_RESET}: {turn_count} turns, {tool_call_count} tool calls")
    print(f"  Model: {model}")
    print(f"{_DIM}{'=' * 64}{_RESET}")


def _summarize_tool_result(name, content):
    """Build a text summary of a tool result for the reasoning trace.
    Strips base64 image data, summarizes chart counts, truncates long text."""
    if name == "fetch_selected_charts":
        if isinstance(content, list):
            n_images = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image")
            labels = [c["text"].replace("--- ", "").replace(" ---", "")
                      for c in content if isinstance(c, dict) and c.get("type") == "text"
                      and c.get("text", "").startswith("---")]
            return "%d charts fetched: %s" % (n_images, ", ".join(labels))
        return "(no charts)"

    if name == "list_available_charts":
        if isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            full = "\n".join(text_parts)
            lines = [l for l in full.split("\n") if l.strip() and not l.startswith("Available")]
            return "%d products listed:\n%s" % (len(lines), "\n".join(lines[:30]))
        if isinstance(content, str):
            return content[:500]
        return str(content)[:500]

    # For text-based tools (TAFs, winds, AFD, route info, airport info)
    if isinstance(content, list):
        text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        full = "\n".join(text_parts)
    elif isinstance(content, str):
        full = content
    else:
        full = str(content)

    # Truncate long results for readability
    if len(full) > 2000:
        return full[:2000] + "\n... (truncated)"
    return full


# ---------------------------------------------------------------------------
# Briefing state — accumulated during the agent loop
# ---------------------------------------------------------------------------

class BriefingState:
    """Tracks data gathered by tool calls for final HTML assembly."""
    def __init__(self):
        self.chart_data = []        # list of (label, url, b64, media_type)
        self.chart_catalog = {}     # {label: (fhr, url)} from list_available_charts
        self.taf_data = []          # list from fetch_tafs()
        self.winds_text = ""
        self.afd_data = []          # list from fetch_afd_aviation()
        self.waypoints = None       # list of (lon, lat)
        self.airport_names = {}     # {ICAO: name}
        self.route_legs = []        # list from compute_route_legs()
        self.trace = []             # reasoning trace: list of turn dicts


# ---------------------------------------------------------------------------
# Tool definitions — JSON schemas for the Claude API
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "get_route_info",
        "description": (
            "Resolve ICAO airport codes to coordinates, compute leg distances and ETAs. "
            "Call this FIRST — you need the route geometry before fetching weather data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ICAO airport codes in route order, e.g. ['KSQL', 'KBDN']",
                },
                "departure_utc": {
                    "type": "string",
                    "description": "Departure time as 'YYYY-MM-DD HH:MM' UTC",
                },
                "tas_kts": {
                    "type": "integer",
                    "description": "True airspeed in knots for ETA computation",
                },
            },
            "required": ["airports", "departure_utc", "tas_kts"],
        },
    },
    {
        "name": "list_available_charts",
        "description": (
            "List all available weather charts with their issue/valid times — NO images downloaded. "
            "Use this to see what's available, then call fetch_selected_charts with the labels you want. "
            "Charts are grouped by category: Surface Progs, Extended Progs, QPF, Icing, Turbulence, "
            "G-AIRMET, GFA, TCF/ETCF, SIGMET, SigWx. Each entry shows the label, forecast hour, "
            "category, issue time, and valid time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_until": {
                    "type": "number",
                    "description": "Hours from now until departure (controls which charts are available)",
                },
                "altitude_ft": {
                    "type": "integer",
                    "description": "Cruise altitude in feet MSL (determines icing/turbulence flight levels)",
                },
            },
            "required": ["hours_until", "altitude_ft"],
        },
    },
    {
        "name": "fetch_selected_charts",
        "description": (
            "Download and display specific charts by label. Pass the exact labels from "
            "list_available_charts. Returns chart images with route overlay for analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact chart labels to fetch (from list_available_charts output)",
                },
                "airports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ICAO codes for route overlay on charts",
                },
            },
            "required": ["labels"],
        },
    },
    {
        "name": "fetch_tafs",
        "description": (
            "Fetch Terminal Aerodrome Forecasts for airports along the route. "
            "Returns raw TAF text with ETA annotations for departure, enroute, and arrival."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ICAO airport codes in route order",
                },
                "departure_utc": {
                    "type": "string",
                    "description": "Departure time as 'YYYY-MM-DD HH:MM' UTC",
                },
                "tas_kts": {
                    "type": "integer",
                    "description": "True airspeed in knots",
                },
            },
            "required": ["airports", "departure_utc", "tas_kts"],
        },
    },
    {
        "name": "fetch_winds",
        "description": (
            "Fetch winds and temperatures aloft for stations near the route. "
            "Returns decoded winds at multiple altitudes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ICAO airport codes defining the route",
                },
                "hours_until": {
                    "type": "number",
                    "description": "Hours from now until departure",
                },
            },
            "required": ["airports", "hours_until"],
        },
    },
    {
        "name": "fetch_afd",
        "description": (
            "Fetch Area Forecast Discussion aviation sections from NWS Weather Forecast Offices "
            "along the route. Most useful within 48 hours of departure — provides forecaster "
            "narrative about expected conditions, uncertainty, and trends."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ICAO airport codes defining the route",
                },
            },
            "required": ["airports"],
        },
    },
    {
        "name": "get_airport_info",
        "description": (
            "Get A/FD facility data for airports — elevation, runway info, frequencies, "
            "pattern altitude, etc. Useful for departure/arrival analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ICAO airport codes to look up",
                },
            },
            "required": ["airports"],
        },
        "cache_control": {"type": "ephemeral"},
    },
]


# ---------------------------------------------------------------------------
# Chart catalog — build the full list with categories, probe for issue/valid times
# ---------------------------------------------------------------------------

def _categorize_label(label):
    """Assign a display category to a chart label."""
    if "Surface Prog" in label:
        return "Surface Progs"
    if "Extended" in label or "Day" in label and "Prog" in label:
        return "Extended Progs"
    if "QPF" in label:
        return "QPF (Precipitation)"
    if "Icing" in label:
        return "Icing (FIP)"
    if "Turb" in label and "G-AIRMET" not in label:
        return "Turbulence (GTG)"
    if "G-AIRMET" in label:
        return "G-AIRMET"
    if "GFA" in label:
        return "GFA (Graphical Forecast)"
    if "TCF" in label or "ETCF" in label:
        return "Ceiling/Flight Rules"
    if "SIGMET" in label:
        return "SIGMET"
    if "SigWx" in label:
        return "Significant Weather"
    return "Other"


def _probe_chart_times(chart_list):
    """Do parallel HEAD requests to get issue/valid times without downloading images.

    Returns list of (label, fhr, url, category, issued_str, valid_str).
    """
    results = {}

    def _head_one(i, fhr, url, label):
        cat = _categorize_label(label)
        try:
            r = httpx.head(url, timeout=10, follow_redirects=True)
            lm = r.headers.get("last-modified", "")
            if lm:
                from email.utils import parsedate_to_datetime
                issued_dt = parsedate_to_datetime(lm)
                issued_str = issued_dt.strftime("%a %d %H:%MZ")
                valid_str = _compute_valid_time(lm, fhr)
            else:
                issued_str = "unknown"
                valid_str = None
            return (i, label, fhr, url, cat, issued_str, valid_str or "unknown")
        except Exception:
            return (i, label, fhr, url, cat, "unavailable", "unavailable")

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [
            executor.submit(_head_one, i, fhr, url, label)
            for i, (fhr, url, label) in enumerate(chart_list)
        ]
        for future in as_completed(futures):
            row = future.result()
            results[row[0]] = row[1:]  # (label, fhr, url, cat, issued, valid)

    return [results[i] for i in sorted(results)]


def _build_full_chart_catalog(hours_until, altitude_ft):
    """Build the complete chart list across all product types."""
    all_lists = {}
    product_args = [
        ("fetch_prog_charts", {"hours_until": hours_until}),
        ("fetch_qpf", {"hours_until": hours_until}),
        ("fetch_icing", {"hours_until": hours_until, "altitude_ft": altitude_ft}),
        ("fetch_turbulence", {"hours_until": hours_until, "altitude_ft": altitude_ft}),
        ("fetch_gairmets_sigmets", {"hours_until": hours_until}),
        ("fetch_ceiling_visibility", {"hours_until": hours_until, "altitude_ft": altitude_ft}),
    ]
    combined = []
    seen = set()
    for tool_name, args in product_args:
        charts = _build_chart_list(tool_name, args)
        for fhr, url, label in charts:
            if url not in seen:
                seen.add(url)
                combined.append((fhr, url, label))
    return combined


# ---------------------------------------------------------------------------
# Chart list builders — one per product type
# ---------------------------------------------------------------------------

def _build_chart_list(tool_name, args):
    """Build a list of (fhr, url, label) tuples for a chart tool."""
    hours = args.get("hours_until", 0)
    alt = args.get("altitude_ft", 10000)
    charts = []
    seen = set()

    def _add(item):
        if item[1] not in seen:
            seen.add(item[1])
            charts.append(item)

    if tool_name == "fetch_prog_charts":
        # All short-term progs (pattern evolution)
        for item in SHORT_TERM_CHARTS:
            _add(item)
        # Extended progs for longer-range flights
        if hours >= 48:
            for item in EXTENDED_CHARTS:
                _add(item)

    elif tool_name == "fetch_icing":
        if hours > 18:
            return []  # Outside forecast window
        ice_cruise = _nearest_level(alt, ICING_LEVELS)
        fip_fhrs = _pick_bracket_fhrs(hours, AWC_FHRS)
        dep_fhr = fip_fhrs[-1] if len(fip_fhrs) > 1 else fip_fhrs[0]
        dep_fhr_str = f"{dep_fhr:02d}"

        # Vertical icing profile: cruise +/- 2 levels
        ice_idx = ICING_LEVELS.index(ice_cruise)
        ice_profile = {ice_cruise}
        for offset in [-2, -1, 1, 2]:
            idx = ice_idx + offset
            if 0 <= idx < len(ICING_LEVELS):
                ice_profile.add(ICING_LEVELS[idx])

        for fhr_num in fip_fhrs:
            fhr = f"{fhr_num:02d}"
            ice_lvl = f"{ice_cruise:03d}"
            _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_prob.gif",
                  f"Icing Prob +{fhr}hr FL{ice_lvl}"))
            _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_sev.gif",
                  f"Icing Sev +{fhr}hr FL{ice_lvl}"))
            _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_sevsld.gif",
                  f"Icing SLD +{fhr}hr FL{ice_lvl}"))
            _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_max_prob.gif",
                  f"Icing Prob MAX +{fhr}hr"))

        # Vertical profile at departure frame
        for lvl in sorted(ice_profile):
            lvl_str = f"{lvl:03d}"
            if lvl == ice_cruise:
                continue
            _add((dep_fhr, f"{AWC_BASE}/icing/F{dep_fhr_str}_fip_{lvl_str}_prob.gif",
                  f"Icing Prob +{dep_fhr_str}hr FL{lvl_str}"))

    elif tool_name == "fetch_turbulence":
        if hours > 18:
            return []
        turb_lvl = f"{_nearest_level(alt, TURBULENCE_LEVELS):03d}"
        fip_fhrs = _pick_bracket_fhrs(hours, AWC_FHRS)
        for fhr_num in fip_fhrs:
            fhr = f"{fhr_num:02d}"
            _add((fhr_num, f"{AWC_BASE}/turbulence/F{fhr}_gtg_{turb_lvl}_total.gif",
                  f"Turb Total +{fhr}hr FL{turb_lvl}"))

    elif tool_name == "fetch_gairmets_sigmets":
        # G-AIRMETs
        if hours <= 12:
            gairmet_fhrs = _pick_bracket_fhrs(hours, GAIRMET_FHRS)
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
        # SIGMET — always current
        _add((0, f"{AWC_BASE}/sigmet/sigmet_all.gif", "SIGMET Current"))

    elif tool_name == "fetch_qpf":
        if hours <= 30:
            for h, u, l in QPF_CHARTS:
                if l.startswith("QPF Day1") and h <= hours + 12:
                    _add((h, u, l))
        if 24 <= hours <= 72:
            for h, u, l in QPF_CHARTS:
                if l.startswith("QPF Day2"):
                    if "24hr Total" in l or h <= hours + 12:
                        _add((h, u, l))
        if 48 <= hours <= 90:
            for h, u, l in QPF_CHARTS:
                if l.startswith("QPF Day3"):
                    if "24hr Total" in l or h <= hours + 12:
                        _add((h, u, l))
        if hours > 60:
            for h, u, l in QPF_CHARTS:
                if "24hr Total" in l:
                    _add((h, u, l))

    elif tool_name == "fetch_ceiling_visibility":
        # GFA — clouds + surface
        if hours <= 18:
            gfa_fhrs = _pick_bracket_fhrs(hours, ["03", "06", "09", "12", "15", "18"])
            for fhr_num in gfa_fhrs:
                fhr = f"{fhr_num:02d}"
                _add((fhr_num, f"{AWC_BASE}/gfa/F{fhr}_gfa_clouds_us.png",
                      f"GFA Clouds +{fhr}hr"))
                _add((fhr_num, f"{AWC_BASE}/gfa/F{fhr}_gfa_sfc_us.png",
                      f"GFA Surface +{fhr}hr"))

        # TCF — Terminal Ceiling & Flight Rules (4-8 hr)
        if hours <= 18:
            tcf_fhrs = _pick_bracket_fhrs(hours, ["04", "06", "08"])
            for fhr_num in tcf_fhrs:
                fhr = f"{fhr_num:02d}"
                _add((fhr_num, f"{AWC_BASE}/tcf/F{fhr}_tcf.gif",
                      f"TCF +{fhr}hr"))

        # ETCF — Extended TCF (10-30 hr)
        if hours >= 8:
            etcf_fhrs = _pick_bracket_fhrs(
                hours, ["10", "12", "14", "16", "18", "20", "22", "24", "26", "28", "30"])
            for fhr_num in etcf_fhrs:
                fhr = f"{fhr_num:02d}"
                _add((fhr_num, f"{AWC_BASE}/etcf/F{fhr}_etcf.gif",
                      f"ETCF +{fhr}hr"))

        # SigWx Low Level
        if hours <= 18:
            swl_fhrs = _pick_bracket_fhrs(hours, ["00", "06", "12", "18"])
            for fhr_num in swl_fhrs:
                pckg = f"{fhr_num:02d}"
                _add((fhr_num, f"{AWC_BASE}/swl/{pckg}_sigwx_lo_us.gif",
                      f"SigWx Low +{pckg}hr"))

            # SigWx Mid Level — only above FL180
            if alt >= 18000:
                for fhr_num in swl_fhrs:
                    pckg = f"{fhr_num:02d}"
                    _add((fhr_num, f"{AWC_BASE}/swm/{pckg}_sigwx_mid_nat.gif",
                          f"SigWx Mid +{pckg}hr"))

    return charts


def _fetch_and_return_charts(chart_list, args, state, all_airports):
    """Fetch a list of charts, overlay route, store in state, return image content blocks."""
    if not chart_list:
        return [{"type": "text", "text": "No charts available for this product/time window."}]

    # Fetch in parallel
    chart_data_map = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_chart, url, label, fhr): i
            for i, (fhr, url, label) in enumerate(chart_list)
        }
        for future in as_completed(futures):
            i = futures[future]
            result = future.result()
            if result:
                chart_data_map[i] = result
    fetched = [chart_data_map[i] for i in sorted(chart_data_map)]

    if not fetched:
        return [{"type": "text", "text": "ERROR: Could not fetch any charts. Network issue?"}]

    # Draw route overlay
    airports = [a.upper() for a in args.get("airports", all_airports)]
    wp = state.waypoints
    if not wp:
        wp, _ = resolve_route_coords(airports)
    if wp:
        for i, (label, url, b64, mt) in enumerate(fetched):
            new_b64 = draw_route_on_chart(b64, mt, wp)
            if new_b64 is not b64:
                fetched[i] = (label, url, new_b64, "image/png")

    # Store in state for HTML assembly
    state.chart_data.extend(fetched)

    # Build image content blocks for the model
    content = []
    for label, url, b64, media_type in fetched:
        content.append({"type": "text", "text": f"--- {label} ---"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })
    content.append({"type": "text", "text": f"\n{len(fetched)} charts fetched and sent."})
    return content


# ---------------------------------------------------------------------------
# Tool execution — dispatch to existing functions
# ---------------------------------------------------------------------------

def execute_tool(name, args, state, all_airports, departure_dt, tas_kts):
    """
    Execute a tool call and return the content for the tool_result message.
    Also updates BriefingState as a side effect.

    Returns: list of content blocks (text/image dicts)
    """

    if name == "get_route_info":
        airports = [a.upper() for a in args["airports"]]
        dep_str = args["departure_utc"]
        dep_dt = datetime.strptime(dep_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        tas = args.get("tas_kts", tas_kts)

        waypoints, names = resolve_route_coords(airports)
        if not waypoints:
            return [{"type": "text", "text": "ERROR: Failed to resolve route coordinates. AWC API may be down."}]

        state.waypoints = waypoints
        state.airport_names = names

        legs = compute_route_legs(airports, waypoints, dep_dt, tas)
        state.route_legs = legs

        leg_table = []
        for leg in legs:
            leg_table.append({
                "icao": leg["icao"],
                "role": leg["role"],
                "eta": leg["eta"].strftime("%H:%MZ"),
                "cum_nm": round(leg["cum_nm"]),
            })

        result = {
            "airports": names,
            "waypoints_count": len(waypoints),
            "legs": leg_table,
            "total_nm": round(legs[-1]["cum_nm"]) if legs else 0,
        }
        return [{"type": "text", "text": json.dumps(result, indent=2)}]

    elif name == "list_available_charts":
        hours = args["hours_until"]
        alt = args["altitude_ft"]
        catalog = _build_full_chart_catalog(hours, alt)
        if not catalog:
            return [{"type": "text", "text": "No charts available for this time window."}]

        # HEAD requests for issue/valid times
        probed = _probe_chart_times(catalog)

        # Store catalog in state for fetch_selected_charts
        state.chart_catalog = {}
        for label, fhr, url, cat, issued, valid in probed:
            state.chart_catalog[label] = (fhr, url)

        # Format as grouped text table
        by_cat = {}
        for label, fhr, url, cat, issued, valid in probed:
            by_cat.setdefault(cat, []).append((label, fhr, issued, valid))

        lines = [f"{len(catalog)} charts available:\n"]
        for cat in ["Surface Progs", "Extended Progs", "QPF (Precipitation)",
                     "Icing (FIP)", "Turbulence (GTG)", "G-AIRMET",
                     "GFA (Graphical Forecast)", "Ceiling/Flight Rules",
                     "SIGMET", "Significant Weather", "Other"]:
            entries = by_cat.get(cat)
            if not entries:
                continue
            lines.append(f"\n  {cat} ({len(entries)} charts):")
            for label, fhr, issued, valid in entries:
                lines.append(f"    - {label}  |  issued: {issued}  |  valid: {valid}")

        lines.append(f"\nCall fetch_selected_charts with the labels you want to see.")
        return [{"type": "text", "text": "\n".join(lines)}]

    elif name == "fetch_selected_charts":
        labels = args["labels"]
        # Look up URLs from the catalog
        chart_list = []
        missing = []
        for label in labels:
            entry = state.chart_catalog.get(label)
            if entry:
                fhr, url = entry
                chart_list.append((fhr, url, label))
            else:
                missing.append(label)

        if missing:
            # Try partial matching for labels that may have slight differences
            for m_label in missing:
                for cat_label, (fhr, url) in state.chart_catalog.items():
                    if m_label.lower() in cat_label.lower() or cat_label.lower() in m_label.lower():
                        chart_list.append((fhr, url, cat_label))
                        break

        if not chart_list:
            available = list(state.chart_catalog.keys())[:10]
            return [{"type": "text", "text":
                     f"ERROR: None of the requested labels matched. "
                     f"Available labels include: {available}"}]

        return _fetch_and_return_charts(chart_list, args, state, all_airports)

    elif name == "fetch_tafs":
        airports = [a.upper() for a in args["airports"]]
        dep_str = args["departure_utc"]
        dep_dt = datetime.strptime(dep_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        tas = args.get("tas_kts", tas_kts)

        wp = state.waypoints
        if not wp:
            wp, _ = resolve_route_coords(airports)
        legs = state.route_legs
        if not legs and wp:
            legs = compute_route_legs(airports, wp, dep_dt, tas)
            state.route_legs = legs

        taf_data = fetch_tafs(airports, legs)
        state.taf_data = taf_data

        lines = []
        for entry in taf_data:
            eta = entry.get("eta", "?")
            if entry.get("taf"):
                note = entry.get("note", entry["icao"])
                lines.append(f"{entry['role']} {note} (ETA {eta}):\n  {entry['taf']}")
            else:
                note = entry.get("note", "No TAF available")
                lines.append(f"{entry['role']} {entry['icao']} (ETA {eta}): {note}")
        return [{"type": "text", "text": "\n\n".join(lines) if lines else "No TAF data retrieved."}]

    elif name == "fetch_winds":
        airports = [a.upper() for a in args["airports"]]
        hours = args["hours_until"]

        wp = state.waypoints
        if not wp:
            wp, _ = resolve_route_coords(airports)
        if not wp:
            return [{"type": "text", "text": "ERROR: Could not resolve route for winds lookup."}]

        winds_text = fetch_winds_aloft(wp, hours)
        state.winds_text = winds_text
        return [{"type": "text", "text": winds_text if winds_text else "No winds aloft data available."}]

    elif name == "fetch_afd":
        airports = [a.upper() for a in args["airports"]]
        wp = state.waypoints
        if not wp:
            wp, _ = resolve_route_coords(airports)
        if not wp:
            return [{"type": "text", "text": "ERROR: Could not resolve route for AFD lookup."}]

        afd_data = fetch_afd_aviation(wp)
        state.afd_data = afd_data

        if not afd_data:
            return [{"type": "text", "text": "No AFD aviation sections found for WFOs along the route."}]

        lines = []
        for entry in afd_data:
            lines.append(f"WFO {entry['wfo']} ({entry['role']}):\n{entry['text']}")
        return [{"type": "text", "text": "\n\n---\n\n".join(lines)}]

    elif name == "get_airport_info":
        airports = [a.upper() for a in args["airports"]]
        text = fetch_afd_for_airports(airports)
        if not text:
            return [{"type": "text", "text": "No A/FD facility data found for these airports."}]
        return [{"type": "text", "text": text}]

    else:
        return [{"type": "text", "text": f"ERROR: Unknown tool '{name}'"}]


# ---------------------------------------------------------------------------
# HTML assembly — reuses templates from flightweather.py
# ---------------------------------------------------------------------------

def build_html(state, briefing_html, significant_labels, airports, departure_dt, altitude, date_str, time_str):
    """Assemble the final HTML page from accumulated state + briefing text."""

    origin = airports[0]
    destination = airports[-1]

    # Split charts into significant / reference
    sig_cards = []
    ref_cards = []
    for label, url, b64, media_type in state.chart_data:
        card = CHART_CARD_TEMPLATE.format(
            label=label, url=url, b64=b64, media_type=media_type,
            extra_class=_card_class(label),
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

    # Build TAF section HTML
    taf_section_html = ""
    if state.taf_data:
        taf_blocks = []
        for entry in state.taf_data:
            role_label = entry.get("role", "")
            eta_dt = entry.get("eta_dt")
            eta_s = entry.get("eta", "")
            if eta_dt:
                target_hour = eta_dt.strftime("%d%H")
                eta_str = eta_dt.strftime("%H:%MZ")
            elif eta_s:
                target_hour = eta_s[8:10] + eta_s[11:13]
                eta_str = eta_s[11:]
            else:
                target_hour = departure_dt.strftime("%d%H")
                eta_str = ""
            nm = entry.get("nm", 0)
            hdr = entry["icao"]
            note_text = entry.get("note", "")
            note = ""
            if note_text:
                note = f' <span style="color:var(--amber);font-size:0.75rem">({note_text})</span>'

            eta_badge = ""
            if eta_str:
                dist_str = f" &middot; {nm:.0f} nm" if nm > 0 else ""
                eta_badge = f' <span style="color:var(--muted);font-size:0.72rem">ETA {eta_str}{dist_str}</span>'

            if entry.get("taf"):
                taf_html = _highlight_taf_line(entry["taf"], target_hour, role_label)
                pre_color = "var(--green)"
            elif "not yet available" in note_text.lower() or "not yet valid" in note_text.lower():
                taf_html = "TAF exists but does not cover flight time — not yet valid"
                pre_color = "var(--amber)"
            else:
                taf_html = "No TAF available for this airport or vicinity"
                pre_color = "var(--amber)"

            taf_blocks.append(
                f'<div style="margin-bottom:0.75rem">'
                f'<strong>{hdr}</strong>'
                f' <span style="color:var(--blue);font-size:0.75rem;font-weight:700">{role_label}</span>'
                f'{eta_badge}{note}'
                f'<pre style="margin-top:0.3rem;font-size:0.78rem;color:{pre_color};'
                f'white-space:pre-wrap;line-height:1.5">{taf_html}</pre></div>'
            )

        if taf_blocks:
            taf_section_html = (
                '  <section>\n'
                '    <div class="section-label">Terminal Forecasts (TAF)</div>\n'
                '    <details class="collapsible">\n'
                '      <summary>\n'
                '        Raw TAFs — route\n'
                '        <span class="pill">' + str(len(taf_blocks)) + ' stations</span>\n'
                '      </summary>\n'
                '      <div class="collapsible-body">\n'
                + "\n".join(taf_blocks)
                + '\n      </div>\n'
                '    </details>\n'
                '  </section>\n'
            )

    # Build AFD section HTML
    afd_section_html = ""
    if state.afd_data:
        afd_blocks = []
        for entry in state.afd_data:
            afd_blocks.append(
                f'<div style="margin-bottom:0.75rem">'
                f'<strong>WFO {entry["wfo"]}</strong>'
                f' <span style="color:var(--blue);font-size:0.75rem;font-weight:700">{entry["role"]}</span>'
                f'<pre style="margin-top:0.3rem;font-size:0.78rem;color:var(--green);'
                f'white-space:pre-wrap;line-height:1.5">{entry["text"]}</pre></div>'
            )
        if afd_blocks:
            afd_section_html = (
                '  <section>\n'
                '    <div class="section-label">Area Forecast Discussion — Aviation</div>\n'
                '    <details class="collapsible">\n'
                '      <summary>\n'
                '        AFD Aviation Sections\n'
                '        <span class="pill">' + str(len(afd_blocks)) + ' WFOs</span>\n'
                '      </summary>\n'
                '      <div class="collapsible-body">\n'
                + "\n".join(afd_blocks)
                + '\n      </div>\n'
                '    </details>\n'
                '  </section>\n'
            )

    # Arrival info from route legs or TAF data
    arr_str = "\u2014"
    total_nm = "\u2014"
    if state.taf_data:
        last = state.taf_data[-1]
        arr_str = last.get("eta", "\u2014")
        total_nm = f"{last.get('nm', 0):.0f}"
    elif state.route_legs:
        last = state.route_legs[-1]
        arr_str = last["eta"].strftime("%H:%MZ")
        total_nm = f"{last['cum_nm']:.0f}"

    # Extract Executive Summary from briefing HTML
    exec_summary_html = ""
    exec_match = re.search(
        r'(<h2>Executive Summary</h2>.*?)(?=<h2>)',
        briefing_html, re.DOTALL
    )
    if exec_match:
        exec_summary_html = (
            '  <section>\n'
            '    <div class="section-label">Executive Summary</div>\n'
            '    <div class="briefing">\n'
            + exec_match.group(1)
            + '\n    </div>\n'
            '  </section>\n'
        )
        briefing_html = briefing_html[:exec_match.start()] + briefing_html[exec_match.end():]
        briefing_html = briefing_html.lstrip("\n")

    # Extract Route Hazards
    hazards_match = re.search(
        r'(<h2>Route Hazards</h2>.*?)(?=<h2>)',
        briefing_html, re.DOTALL
    )
    if hazards_match:
        exec_summary_html += (
            '  <section>\n'
            '    <div class="section-label">Route Hazards</div>\n'
            '    <div class="briefing">\n'
            + hazards_match.group(1)
            + '\n    </div>\n'
            '  </section>\n'
        )
        briefing_html = briefing_html[:hazards_match.start()] + briefing_html[hazards_match.end():]
        briefing_html = briefing_html.lstrip("\n")

    now_utc = datetime.now(timezone.utc)
    html = HTML_TEMPLATE.format(
        origin=origin,
        destination=destination,
        route_display=" \u2192 ".join(airports),
        dep_date=date_str,
        dep_str=departure_dt.strftime("%Y-%m-%d %H:%MZ"),
        arr_str=arr_str,
        total_nm=total_nm,
        altitude_ft=f"{altitude:,}",
        generated=now_utc.strftime("%Y-%m-%d %H:%MZ"),
        exec_summary_html=exec_summary_html,
        taf_section_html=taf_section_html,
        afd_section_html=afd_section_html,
        chart_count=len(state.chart_data),
        sig_count=len(sig_cards),
        significant_chart_cards="\n".join(sig_cards),
        reference_section=reference_section,
        briefing_html=briefing_html,
    )
    return html


# ---------------------------------------------------------------------------
# Reasoning trace HTML
# ---------------------------------------------------------------------------

REASONING_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent Trace — {origin} → {destination} {date}</title>
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
    --magenta: #d2a8ff;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 2rem;
    max-width: 900px;
    margin: 0 auto;
  }}
  header {{
    border-bottom: 1px solid var(--border);
    padding-bottom: 1.5rem;
    margin-bottom: 2rem;
  }}
  header h1 {{
    font-size: 1.5rem;
    color: var(--magenta);
  }}
  header .meta {{
    color: var(--muted);
    font-size: 0.9rem;
    margin-top: 0.3rem;
  }}
  .turn {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 1.5rem;
    overflow: hidden;
  }}
  .turn-header {{
    background: var(--raised);
    padding: 0.6rem 1rem;
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .turn-header .turn-num {{
    color: var(--blue);
  }}
  .turn-header .badge {{
    font-size: 0.75rem;
    padding: 0.15rem 0.5rem;
    border-radius: 10px;
    font-weight: 500;
  }}
  .badge-final {{
    background: var(--green);
    color: var(--bg);
  }}
  .badge-tools {{
    background: var(--amber);
    color: var(--bg);
  }}
  .thinking {{
    background: #1a1a2e;
    border-left: 3px solid var(--magenta);
    padding: 0.8rem 1rem;
    margin: 0.8rem 1rem;
    border-radius: 4px;
    font-size: 0.85rem;
    color: var(--muted);
    white-space: pre-wrap;
    /* no max-height — full content for PDF export */
  }}
  .thinking-label {{
    font-size: 0.75rem;
    text-transform: uppercase;
    color: var(--magenta);
    letter-spacing: 0.05em;
    margin: 0.8rem 1rem 0 1rem;
  }}
  .agent-text {{
    padding: 0.8rem 1rem;
    white-space: pre-wrap;
    font-size: 0.9rem;
  }}
  .tool-call {{
    margin: 0.5rem 1rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }}
  .tool-call-header {{
    background: var(--raised);
    padding: 0.4rem 0.8rem;
    font-size: 0.85rem;
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }}
  .tool-name {{
    color: var(--amber);
    font-weight: 600;
  }}
  .tool-args {{
    color: var(--muted);
    font-size: 0.8rem;
  }}
  .tool-result {{
    padding: 0.5rem 0.8rem;
    font-size: 0.8rem;
    color: var(--muted);
    white-space: pre-wrap;
    /* no max-height — full content for PDF export */
    border-top: 1px solid var(--border);
  }}
  .note {{
    color: var(--amber);
    font-style: italic;
    padding: 0.5rem 1rem;
    font-size: 0.85rem;
  }}
  .summary {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    margin-top: 2rem;
    text-align: center;
    color: var(--muted);
  }}
</style>
</head>
<body>
<header>
  <h1>Agent Reasoning Trace</h1>
  <div class="meta">{origin} → {destination} &mdash; {date} {time}Z &mdash; {altitude:,} ft &mdash; Model: {model}</div>
</header>
{turns_html}
<div class="summary">{turn_count} turns &middot; {tool_count} tool calls</div>
</body>
</html>
"""


def _escape_html(text):
    """Escape HTML special characters."""
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def build_reasoning_html(state, airports, departure_dt, altitude, model):
    """Build a self-contained HTML page showing the agent's reasoning trace."""
    origin = airports[0]
    destination = airports[-1]
    date_str = departure_dt.strftime("%Y-%m-%d")
    time_str = departure_dt.strftime("%H:%M")

    turns_html_parts = []
    total_tools = 0

    for entry in state.trace:
        turn_num = entry["turn"]
        is_final = entry.get("is_final", False)
        tool_calls = entry.get("tool_calls", [])
        total_tools += len(tool_calls)

        # Badge
        if is_final:
            badge = '<span class="badge badge-final">Final Output</span>'
        elif tool_calls:
            badge = '<span class="badge badge-tools">%d tool call%s</span>' % (
                len(tool_calls), "s" if len(tool_calls) != 1 else "")
        else:
            badge = ""

        parts = []
        parts.append('<div class="turn">')
        parts.append('<div class="turn-header"><span class="turn-num">Turn %d</span>%s</div>' % (turn_num, badge))

        # Thinking
        if entry.get("thinking"):
            parts.append('<div class="thinking-label">Internal Thinking</div>')
            parts.append('<div class="thinking">%s</div>' % _escape_html(entry["thinking"]))

        # Agent text
        if entry.get("agent_text"):
            parts.append('<div class="agent-text">%s</div>' % _escape_html(entry["agent_text"]))

        # Tool calls
        for tc in tool_calls:
            args_str = ", ".join("%s=%s" % (k, json.dumps(v) if isinstance(v, (list, dict, bool)) else str(v))
                                 for k, v in tc["args"].items())
            parts.append('<div class="tool-call">')
            parts.append('<div class="tool-call-header"><span class="tool-name">%s</span>'
                         '<span class="tool-args">%s</span></div>' % (
                             _escape_html(tc["name"]), _escape_html(args_str)))
            if tc.get("result_summary"):
                parts.append('<div class="tool-result">%s</div>' % _escape_html(tc["result_summary"]))
            parts.append('</div>')

        # Note (e.g. truncation warning)
        if entry.get("note"):
            parts.append('<div class="note">%s</div>' % _escape_html(entry["note"]))

        parts.append('</div>')
        turns_html_parts.append("\n".join(parts))

    return REASONING_HTML_TEMPLATE.format(
        origin=origin,
        destination=destination,
        date=date_str,
        time=time_str,
        altitude=altitude,
        model=model,
        turns_html="\n".join(turns_html_parts),
        turn_count=len(state.trace),
        tool_count=total_tools,
    )


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(airports, departure_dt, altitude, tas, model, max_turns, no_open):
    origin = airports[0]
    destination = airports[-1]
    date_str = departure_dt.strftime("%Y-%m-%d")
    time_str = departure_dt.strftime("%H:%M")
    now_utc = datetime.now(timezone.utc)
    hours_until = (departure_dt - now_utc).total_seconds() / 3600

    state = BriefingState()
    client = anthropic.Anthropic()

    # Load prompts
    agent_briefing_template = _load_prompt("agent_briefing.txt")
    altitude_str = "{:,}".format(altitude)
    dep_str = departure_dt.strftime("%Y-%m-%d %H:%MZ")
    agent_briefing = agent_briefing_template.format(
        origin=origin.upper(),
        destination=destination.upper(),
        altitude=altitude_str,
        dep_str=dep_str,
    )

    # System prompt = CFII rules + agent output format
    system = SYSTEM_PROMPT + "\n\n" + agent_briefing

    # Initial user message
    dep_day = departure_dt.strftime("%A")
    user_msg = (
        f"I need a weather briefing for this flight:\n\n"
        f"  Route        : {' -> '.join(airports)}\n"
        f"  Departure    : {dep_day} {dep_str} UTC\n"
        f"  Altitude     : {altitude:,} ft MSL\n"
        f"  TAS          : {tas} kts\n"
        f"  Hours until  : {hours_until:+.1f} hrs from now\n\n"
        f"Investigate the weather and produce a briefing. "
        f"Start by getting the route info and listing available charts."
    )

    messages = [{"role": "user", "content": user_msg}]

    _print_header(airports, departure_dt, altitude)
    print(f"  {_DIM}Model: {model} | Max turns: {max_turns}{_RESET}")
    print(f"  {_DIM}Hours until departure: {hours_until:+.1f}{_RESET}")

    turn = 0
    tool_call_count = 0

    def _prune_images(msgs):
        """Replace base64 image blocks in all tool results with text placeholders.
        Called before each API call (turn 2+), so the model has already seen and
        analyzed every image in the history — safe to prune all of them."""
        if len(msgs) < 3:
            return msgs
        pruned = 0
        for msg in msgs:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for tool_result in content:
                if not isinstance(tool_result, dict) or tool_result.get("type") != "tool_result":
                    continue
                blocks = tool_result.get("content")
                if not isinstance(blocks, list):
                    continue
                new_blocks = []
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "image":
                        # Find the preceding text label if any
                        label = "chart"
                        if new_blocks and isinstance(new_blocks[-1], dict) and new_blocks[-1].get("type") == "text":
                            text = new_blocks[-1].get("text", "")
                            if text.startswith("---"):
                                label = text.replace("---", "").strip()
                        new_blocks.append({"type": "text", "text": f"[Image: {label} — already analyzed]"})
                        pruned += 1
                    else:
                        new_blocks.append(block)
                tool_result["content"] = new_blocks
        if pruned:
            print(f"  {_DIM}[pruned {pruned} image(s) from conversation history]{_RESET}")
        return msgs

    while turn < max_turns:
        turn += 1
        _print_turn(turn)

        # Prune images from prior turns to save tokens
        if turn > 1:
            _prune_images(messages)

        # Call the API (system + tools cached after turn 1)
        with client.messages.stream(
            model=model,
            max_tokens=50000,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        ) as stream:
            response = stream.get_final_message()

        # Show token usage / cache stats
        usage = response.usage
        cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
        cache_create = getattr(usage, 'cache_creation_input_tokens', 0) or 0
        input_tok = getattr(usage, 'input_tokens', 0) or 0
        output_tok = getattr(usage, 'output_tokens', 0) or 0
        cache_str = ""
        if cache_read:
            cache_str = f" | cache read: {cache_read:,}"
        elif cache_create:
            cache_str = f" | cache write: {cache_create:,}"
        print(f"  {_DIM}[tokens in:{input_tok:,} out:{output_tok:,}{cache_str}]{_RESET}")

        # Process response content blocks
        text_parts = []
        thinking_parts = []
        tool_uses = []

        for block in response.content:
            if block.type == "thinking":
                if block.thinking and block.thinking.strip():
                    thinking_parts.append(block.thinking.strip())
                    print(f"\n  {_DIM}--- thinking ---{_RESET}")
                    for line in block.thinking.strip().split("\n"):
                        print(f"  {_DIM}{line}{_RESET}")
                    print(f"  {_DIM}--- end thinking ---{_RESET}\n")
            elif block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        # Display the model's reasoning text
        agent_text = "\n".join(text_parts)
        _print_agent_text(agent_text)

        # Start building trace entry for this turn
        turn_trace = {
            "turn": turn,
            "thinking": "\n\n".join(thinking_parts) if thinking_parts else None,
            "agent_text": agent_text.strip() if agent_text.strip() else None,
            "tool_calls": [],
            "is_final": False,
        }

        # If no tool calls, this is the final response
        if not tool_uses:
            turn_trace["is_final"] = True
            if response.stop_reason == "max_tokens":
                turn_trace["note"] = "Output truncated at max_tokens"
                print(f"\n  {_YELLOW}[output truncated at max_tokens — briefing may be incomplete]{_RESET}")
            else:
                print(f"\n  {_MAGENTA}[briefing output received]{_RESET}")
            state.trace.append(turn_trace)
            break

        # Execute tool calls
        tool_results = []
        for tool_block in tool_uses:
            tool_call_count += 1
            _print_tool_call(tool_block.name, tool_block.input)

            content = execute_tool(
                tool_block.name,
                tool_block.input,
                state,
                airports,
                departure_dt,
                tas,
            )

            _print_tool_result_summary(tool_block.name, content)

            # Build result summary for trace (no base64 images)
            result_summary = _summarize_tool_result(tool_block.name, content)

            turn_trace["tool_calls"].append({
                "name": tool_block.name,
                "args": tool_block.input,
                "result_summary": result_summary,
            })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": content,
            })

        state.trace.append(turn_trace)

        # Append assistant response + tool results to conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    else:
        print(f"\n  {_YELLOW}[max turns reached — extracting briefing from last response]{_RESET}")
        state.trace.append({
            "turn": turn,
            "thinking": None,
            "agent_text": None,
            "tool_calls": [],
            "is_final": True,
            "note": "Max turns reached — briefing extracted from last response",
        })

    # Parse the final output
    raw_output = "\n".join(
        block.text for block in response.content if hasattr(block, "text") and block.type == "text"
    )

    # Parse SIGNIFICANT_CHARTS from the first line
    significant_labels = set()
    sig_match = re.match(r'SIGNIFICANT_CHARTS:\s*(\[.*?\])', raw_output, re.DOTALL)
    if sig_match:
        try:
            labels = json.loads(sig_match.group(1))
            significant_labels = set(labels)
        except (json.JSONDecodeError, TypeError):
            pass
        briefing_html = raw_output[sig_match.end():].lstrip("\n")
    else:
        briefing_html = raw_output

    # Fallback: if parsing failed, treat all charts as significant
    if not significant_labels:
        significant_labels = set(label for label, _, _, _ in state.chart_data)

    # Assemble HTML
    html = build_html(
        state, briefing_html, significant_labels,
        airports, departure_dt, altitude, date_str, time_str,
    )

    # Save briefing
    dep_stamp = time_str.replace(":", "")
    gen_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%MZ")
    fname = f"briefing_{origin}_{destination}_{date_str}_{dep_stamp}Z_{gen_stamp}.html"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(base_dir, fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Save reasoning trace
    trace_fname = f"trace_{origin}_{destination}_{date_str}_{dep_stamp}Z_{gen_stamp}.html"
    trace_path = os.path.join(base_dir, trace_fname)
    trace_html = build_reasoning_html(state, airports, departure_dt, altitude, model)
    with open(trace_path, "w", encoding="utf-8") as f:
        f.write(trace_html)

    _print_final_stats(turn, tool_call_count, model)
    print(f"  Saved -> {out_path}")
    print(f"  Trace -> {trace_path}")

    if not no_open:
        webbrowser.open(f"file://{out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aviation weather briefing — Agent Mode (model drives data gathering)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("route", nargs="+",
                        help="Route: ORIGIN [WAYPOINTS...] DESTINATION DATE TIME ALTITUDE")
    parser.add_argument("--tas", type=int, default=150,
                        help="True airspeed in knots (default: 150)")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Model ID (default: claude-sonnet-4-6)")
    parser.add_argument("--max-turns", type=int, default=10,
                        help="Maximum agent turns before forcing output (default: 10)")
    parser.add_argument("--no-open", action="store_true",
                        help="Save HTML but do not open browser")

    args = parser.parse_args()

    route_args = args.route
    if len(route_args) < 5:
        sys.exit("Error: Need at least ORIGIN DESTINATION DATE TIME ALTITUDE")
    try:
        altitude = int(route_args[-1])
        time_str = route_args[-2]
        date_str = route_args[-3]
        airports = [a.upper() for a in route_args[:-3]]
    except (ValueError, IndexError):
        sys.exit("Error: Usage: ORIGIN [WAYPOINTS...] DESTINATION DATE TIME ALTITUDE")

    if len(airports) < 2:
        sys.exit("Error: Need at least origin and destination airports.")

    try:
        departure_dt = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit("Error: Use YYYY-MM-DD for date and HH:MM for time (UTC).")

    now_utc = datetime.now(timezone.utc)
    hours_until = (departure_dt - now_utc).total_seconds() / 3600

    if hours_until < -2:
        sys.exit("Error: Departure time is more than 2 hours in the past.")
    if hours_until > 7 * 24:
        print("Warning: >7 days out — extended prog reliability is very low.")

    run_agent(airports, departure_dt, altitude, args.tas, args.model, args.max_turns, args.no_open)


if __name__ == "__main__":
    main()
