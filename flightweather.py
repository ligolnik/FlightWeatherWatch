#!/usr/bin/env python3
"""
FlightWeatherWatch — Aviation weather briefing from WPC prog charts + Claude.

Outputs a self-contained HTML file with embedded charts.

Usage:
    python3 flightweather.py <origin> <destination> <date> <time_utc> <altitude_ft>
    python3 flightweather.py <origin> [waypoints...] <destination> <date> <time_utc> <altitude_ft>

Examples:
    python3 flightweather.py KORD KJFK 2026-03-12 14:00 8000
    python3 flightweather.py KMQY KEDC 2026-03-16 15:00 12000
    python3 flightweather.py KBNA KMEM KDFW KEDC 2026-03-10 19:00 12000
    python3 flightweather.py --all KDEN KPHX 2026-03-11 06:00 10500
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import webbrowser
from datetime import datetime, timezone
from typing import Optional

import math

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

    ice_cruise = _nearest_level(altitude_ft, ICING_LEVELS)
    turb_lvl = f"{_nearest_level(altitude_ft, TURBULENCE_LEVELS):03d}"

    # Build vertical icing profile: cruise level + levels above & below + max
    ice_idx = ICING_LEVELS.index(ice_cruise)
    ice_profile = set()
    ice_profile.add(ice_cruise)
    # 2 levels below, 2 above (for escape altitude / best cruise analysis)
    for offset in [-2, -1, 1, 2]:
        idx = ice_idx + offset
        if 0 <= idx < len(ICING_LEVELS):
            ice_profile.add(ICING_LEVELS[idx])
    ice_profile_sorted = sorted(ice_profile)

    # Pick bracketing frames (not every 3-hr step)
    fip_fhrs = _pick_bracket_fhrs(hours_until, AWC_FHRS)
    gairmet_fhrs = _pick_bracket_fhrs(hours_until, GAIRMET_FHRS)

    # Use the frame closest to departure for the vertical icing profile
    dep_fhr = fip_fhrs[-1] if len(fip_fhrs) > 1 else fip_fhrs[0]
    dep_fhr_str = f"{dep_fhr:02d}"

    for fhr_num in fip_fhrs:
        fhr = f"{fhr_num:02d}"
        ice_lvl = f"{ice_cruise:03d}"

        # Icing at cruise level — prob + severity + SLD
        _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_prob.gif",
              f"Icing Prob +{fhr}hr FL{ice_lvl}"))
        _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_sev.gif",
              f"Icing Sev +{fhr}hr FL{ice_lvl}"))
        _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_{ice_lvl}_sevsld.gif",
              f"Icing SLD +{fhr}hr FL{ice_lvl}"))

        # Icing max — worst across all altitudes
        _add((fhr_num, f"{AWC_BASE}/icing/F{fhr}_fip_max_prob.gif",
              f"Icing Prob MAX +{fhr}hr"))

        # Turbulence — total (CAT + MWT combined)
        _add((fhr_num, f"{AWC_BASE}/turbulence/F{fhr}_gtg_{turb_lvl}_total.gif",
              f"Turb Total +{fhr}hr FL{turb_lvl}"))

    # Vertical icing profile at departure frame — prob at each level
    for lvl in ice_profile_sorted:
        lvl_str = f"{lvl:03d}"
        if lvl == ice_cruise:
            continue  # already added above
        tag = ""
        _add((dep_fhr, f"{AWC_BASE}/icing/F{dep_fhr_str}_fip_{lvl_str}_prob.gif",
              f"Icing Prob +{dep_fhr_str}hr FL{lvl_str}"))

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

def _compute_valid_time(last_modified, forecast_hr):
    """Compute chart valid time from Last-Modified header + forecast hour.

    Returns a datetime string like 'Tue 18Z' or None.
    """
    from datetime import timedelta
    from email.utils import parsedate_to_datetime
    try:
        issued = parsedate_to_datetime(last_modified)
        # Round down to nearest 6-hr cycle (00/06/12/18Z)
        cycle_hr = (issued.hour // 6) * 6
        issued = issued.replace(hour=cycle_hr, minute=0, second=0, microsecond=0)
        valid = issued + timedelta(hours=forecast_hr)
        return valid.strftime("%a %HZ")
    except Exception:
        return None


def fetch_chart(url, label, forecast_hr=0):
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

        # Compute valid time from Last-Modified + forecast hour
        lm = r.headers.get("last-modified", "")
        valid_str = _compute_valid_time(lm, forecast_hr) if lm else None
        if valid_str:
            label = f"{label} (valid {valid_str})"

        print("OK")
        return (label, url, encoded, media_type)
    except Exception as exc:
        print(f"FAILED ({exc})")
        return None


AWC_API = "https://aviationweather.gov/api/data"

# Winds aloft station IDs near common route corridors
# We fetch the full product and filter by stations near the route
WINDS_ALOFT_FCSTS = ["06", "12", "24"]  # available forecast periods


# ---------------------------------------------------------------------------
# Route overlay — draw magenta flight path on chart images
# ---------------------------------------------------------------------------

_georef_cache = {}  # type: dict


def _get_georef(img_w, img_h):
    """Get a ChartGeoreferencer for the given image dimensions, or None."""
    key = (img_w, img_h)
    if key in _georef_cache:
        return _georef_cache[key]

    try:
        from chart_georef import load_calibration, list_calibrations
    except ImportError:
        _georef_cache[key] = None
        return None

    # Match image dimensions to a calibration:
    #   WPC short-term prog  ~799x559  -> wpc_prog_short  (also wpc_qpf ~800x561)
    #   WPC extended prog    ~848x638  -> wpc_prog_extended
    #   AWC charts           ~1000x720 -> (not yet calibrated)
    #
    # Tolerance of ±5 pixels handles slight size variations between chart issues.
    cal_map = [
        ("wpc_prog_short",    799, 559, 5),
        ("wpc_qpf",           800, 561, 5),
        ("wpc_prog_extended", 848, 638, 10),
    ]

    georef = None
    for cal_name, ref_w, ref_h, tol in cal_map:
        if abs(img_w - ref_w) <= tol and abs(img_h - ref_h) <= tol:
            if cal_name in list_calibrations():
                try:
                    georef = load_calibration(cal_name)
                    break
                except Exception:
                    pass

    _georef_cache[key] = georef
    return georef


def draw_route_on_chart(b64_data, media_type, waypoints):
    """Draw a magenta route line on a chart image.

    waypoints: list of (lon, lat) tuples
    Returns new base64-encoded image data, or original if drawing fails.
    """
    if len(waypoints) < 2:
        return b64_data

    try:
        from PIL import Image, ImageDraw
        import io
    except ImportError:
        return b64_data

    raw = base64.standard_b64decode(b64_data)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size

    georef = _get_georef(w, h)
    if georef is None:
        return b64_data

    draw = ImageDraw.Draw(img)
    lw = max(2, w // 400)
    rad = max(3, w // 250)

    # Convert waypoints to pixels (waypoints are lon, lat)
    pixels = []
    for lon, lat in waypoints:
        px, py = georef.latlon_to_pixel(lat, lon)
        pixels.append((int(round(px)), int(round(py))))

    # Draw route segments
    for i in range(len(pixels) - 1):
        draw.line([pixels[i], pixels[i + 1]], fill='magenta', width=lw)

    # Draw waypoint dots
    for p in pixels:
        draw.ellipse([p[0] - rad, p[1] - rad, p[0] + rad, p[1] + rad], fill='magenta')

    # Encode back as PNG (works for all input formats)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def resolve_route_coords(airports):
    """Resolve airport ICAO codes to (lon, lat) and names via AWC flightpath API.

    airports: list of ICAO codes, e.g. ["KMQY", "KEDC"]
    Returns (coords, names) where coords is list of (lon, lat) tuples
    and names is dict mapping ICAO to airport name, or (None, {}).
    """
    path = " ".join(a.upper() for a in airports)
    try:
        r = httpx.get(f"{AWC_API}/flightpath", params={"path": path}, timeout=10)
        r.raise_for_status()
        data = r.json()
        coords = []
        names = {}
        for feat in data.get("features", []):
            if feat["geometry"]["type"] == "Point":
                lon, lat = feat["geometry"]["coordinates"]
                coords.append((lon, lat))
                faa_id = feat["properties"].get("id", "")
                name = feat["properties"].get("name", "")
                # Map back to ICAO — AWC strips the K prefix
                for apt in airports:
                    if apt.upper().endswith(faa_id):
                        names[apt.upper()] = name.replace("_", " ").title()
                        break
        return (coords, names) if len(coords) >= 2 else (None, {})
    except Exception:
        return None, {}


# ---------------------------------------------------------------------------
# TAF fetching
# ---------------------------------------------------------------------------


def _nm_distance(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def _get_station_info(icao):
    """Return station dict from AWC or None."""
    try:
        r = httpx.get(f"{AWC_API}/stationinfo", params={"ids": icao}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None
    except Exception:
        return None


def _find_nearby_taf_station(icao, max_nm=50):
    """If `icao` has no TAF, find the nearest station that does within max_nm."""
    info = _get_station_info(icao)
    if not info:
        return None, None
    lat, lon = info["lat"], info["lon"]

    # Search a bounding box roughly max_nm around the station
    deg = max_nm / 60.0  # ~1 degree latitude ≈ 60 nm
    try:
        r = httpx.get(f"{AWC_API}/stationinfo",
                      params={"bbox": f"{lat-deg},{lon-deg},{lat+deg},{lon+deg}"},
                      timeout=10)
        r.raise_for_status()
        stations = r.json()
    except Exception:
        return None, None

    best = None
    best_dist = max_nm + 1
    for s in stations:
        if "TAF" not in s.get("siteType", []):
            continue
        if s["icaoId"] == icao:
            continue
        d = _nm_distance(lat, lon, s["lat"], s["lon"])
        if d < best_dist:
            best_dist = d
            best = s
    if best:
        return best["icaoId"], best_dist
    return None, None


def _taf_covers_time(taf_text, target_dt):
    """Check if a TAF's validity period covers the target datetime."""
    import re
    m = re.search(r'(\d{2})(\d{2})/(\d{2})(\d{2})', taf_text)
    if not m:
        return False
    start_day, start_hr = int(m.group(1)), int(m.group(2))
    end_day, end_hr = int(m.group(3)), int(m.group(4))
    t_day, t_hr = target_dt.day, target_dt.hour
    # Simple check: target day/hour within start and end
    # Handle month wrap (start_day > end_day)
    if end_day >= start_day:
        in_range = (t_day > start_day or (t_day == start_day and t_hr >= start_hr)) and \
                   (t_day < end_day or (t_day == end_day and t_hr <= end_hr))
    else:
        # Wraps across month boundary
        in_range = (t_day >= start_day and t_hr >= start_hr) or \
                   (t_day <= end_day and t_hr <= end_hr)
    return in_range


def _fetch_single_taf(icao, target_dt=None):
    """Fetch TAF for one airport. Falls back to nearest TAF station.
    If target_dt is provided, only returns TAF if it covers that time.
    """
    print(f"  TAF {icao} ... ", end="", flush=True)
    try:
        r = httpx.get(f"{AWC_API}/taf", params={"ids": icao, "format": "raw"}, timeout=10)
        taf_text = r.text.strip()
    except Exception:
        taf_text = ""

    if taf_text and taf_text.startswith("TAF"):
        if target_dt and not _taf_covers_time(taf_text, target_dt):
            print("not yet valid for flight time")
            return {"icao": icao, "taf": None, "note": "TAF not yet available for flight time"}
        print("OK")
        return {"icao": icao, "taf": taf_text, "note": None}

    # No TAF — search nearby
    print("not available, searching nearby ... ", end="", flush=True)
    nearby_icao, dist = _find_nearby_taf_station(icao)
    if nearby_icao:
        try:
            r = httpx.get(f"{AWC_API}/taf",
                          params={"ids": nearby_icao, "format": "raw"}, timeout=10)
            taf_text = r.text.strip()
        except Exception:
            taf_text = ""
        if taf_text and taf_text.startswith("TAF"):
            if target_dt and not _taf_covers_time(taf_text, target_dt):
                print(f"{nearby_icao} not yet valid for flight time")
                return {"icao": nearby_icao, "taf": None,
                        "note": f"TAF not yet available for flight time (nearest: {nearby_icao}, {dist:.0f} nm)"}
            print(f"using {nearby_icao} ({dist:.0f} nm)")
            return {"icao": nearby_icao, "taf": taf_text,
                    "note": f"Nearest TAF to {icao} ({dist:.0f} nm)"}
    print("none found")
    return {"icao": icao, "taf": None, "note": "No TAF available"}


def compute_route_legs(airports, waypoint_coords, departure_dt, tas_kts):
    """Compute ETA at each waypoint based on great-circle distance and TAS.

    Returns list of dicts: [{"icao": "KMQY", "role": "Departure", "eta": datetime, "nm": 0}, ...]
    """
    legs = [{"icao": airports[0], "role": "Departure", "eta": departure_dt, "nm": 0, "cum_nm": 0}]
    cum_time = 0.0
    cum_nm = 0.0

    for i in range(1, len(airports)):
        if i < len(waypoint_coords) and (i - 1) < len(waypoint_coords):
            lon1, lat1 = waypoint_coords[i - 1]
            lon2, lat2 = waypoint_coords[i]
            dist = _nm_distance(lat1, lon1, lat2, lon2)
        else:
            dist = 0

        leg_time_hrs = dist / tas_kts if tas_kts > 0 else 0
        cum_time += leg_time_hrs
        cum_nm += dist

        from datetime import timedelta
        eta = departure_dt + timedelta(hours=cum_time)
        role = "Arrival" if i == len(airports) - 1 else "Enroute"
        legs.append({
            "icao": airports[i],
            "role": role,
            "eta": eta,
            "nm": dist,
            "cum_nm": cum_nm,
        })
    return legs


def fetch_tafs(airports, route_legs):
    """Fetch TAFs for all airports on the route.

    Returns list of dicts: [{"icao", "taf", "note", "role", "eta", "nm"}, ...]
    """
    results = []
    for leg in route_legs:
        entry = _fetch_single_taf(leg["icao"], target_dt=leg["eta"])
        entry["role"] = leg["role"]
        entry["eta"] = leg["eta"].strftime("%Y-%m-%d %H:%MZ")
        entry["eta_dt"] = leg["eta"]  # keep datetime for internal use
        entry["nm"] = leg.get("cum_nm", 0)
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Area Forecast Discussion — AVIATION section
# ---------------------------------------------------------------------------


def _get_wfo_for_coords(lat, lon):
    """Return 3-letter WFO code for a lat/lon via NWS points API, or None."""
    try:
        r = httpx.get(
            f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}",
            headers={"User-Agent": "FlightWeatherWatch/1.0"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        cwa = data.get("properties", {}).get("cwa")
        return cwa  # e.g. "BOU"
    except Exception:
        return None


def _fetch_afd_text(wfo):
    """Fetch AFD text product for a WFO. Returns raw text or None."""
    url = (
        f"https://forecast.weather.gov/product.php"
        f"?site={wfo}&issuedby={wfo}&product=AFD&format=txt&version=1&glossary=0"
    )
    try:
        r = httpx.get(url, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def _extract_aviation_section(afd_text):
    """Extract the AVIATION section from an AFD. Returns text or None."""
    if not afd_text:
        return None
    # Match .AVIATION ... through && (end marker)
    m = re.search(r'(\.AVIATION.*?)(?:\n&&)', afd_text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def fetch_afd_aviation(waypoint_coords):
    """Fetch AFD AVIATION sections for WFOs covering departure and arrival.

    waypoint_coords: list of (lon, lat) tuples — first is departure, last is arrival.
    Returns list of dicts: [{"wfo": "BOU", "airport": "KBJC", "role": "Departure", "text": "..."}, ...]
    """
    if not waypoint_coords or len(waypoint_coords) < 2:
        return []

    results = []
    seen_wfos = set()

    endpoints = [
        (waypoint_coords[0], "Departure"),
        (waypoint_coords[-1], "Arrival"),
    ]

    for (lon, lat), role in endpoints:
        wfo = _get_wfo_for_coords(lat, lon)
        if not wfo or wfo in seen_wfos:
            if wfo and wfo in seen_wfos:
                print(f"  AFD {wfo} ({role}) ... same WFO as departure, skipping")
            else:
                print(f"  AFD ({role}) ... could not determine WFO")
            continue
        seen_wfos.add(wfo)

        print(f"  AFD {wfo} ({role}) ... ", end="", flush=True)
        raw = _fetch_afd_text(wfo)
        aviation = _extract_aviation_section(raw)
        if aviation:
            results.append({"wfo": wfo, "role": role, "text": aviation})
            print("OK")
        else:
            print("no AVIATION section found")

    return results


# ---------------------------------------------------------------------------
# Winds aloft
# ---------------------------------------------------------------------------

def fetch_winds_aloft(waypoint_coords, hours_until):
    """Fetch winds/temps aloft for stations near the route.

    Returns a formatted text block for the LLM prompt, or empty string.
    """
    if not waypoint_coords or len(waypoint_coords) < 2:
        return ""

    # Pick the best forecast period
    if hours_until <= 9:
        fcst = "06"
    elif hours_until <= 18:
        fcst = "12"
    elif hours_until <= 30:
        fcst = "24"
    else:
        return ""  # No winds aloft beyond 24hrs

    print(f"  Winds aloft (FD {fcst}hr) ... ", end="", flush=True)

    try:
        r = httpx.get(f"{AWC_API}/windtemp",
                      params={"region": "all", "level": "low", "fcst": fcst},
                      timeout=10)
        raw = r.text.strip()
    except Exception:
        print("FAILED")
        return ""

    if not raw or "error" in raw[:50].lower():
        print("FAILED")
        return ""

    # Parse the product — extract header + station lines
    lines = raw.split("\n")
    header_lines = []
    station_lines = {}
    ft_line = ""
    for line in lines:
        line = line.rstrip()
        if line.startswith("FT "):
            ft_line = line
            continue
        if line.startswith("DATA BASED") or line.startswith("VALID "):
            header_lines.append(line)
            continue
        # Station lines start with 3-letter ID
        if len(line) >= 3 and line[:3].isalpha() and line[:3].isupper():
            sid = line[:3]
            station_lines[sid] = line

    if not station_lines:
        print("no data")
        return ""

    # Find stations within ~100nm of route waypoints
    # First, get station locations
    try:
        r2 = httpx.get(f"{AWC_API}/stationinfo",
                       params={"bbox": _route_bbox(waypoint_coords, margin_deg=2.0)},
                       timeout=10)
        all_stations = r2.json()
    except Exception:
        print("station lookup failed")
        return ""

    # Filter to stations that are in the winds aloft data and near the route
    route_stations = []
    for stn in all_stations:
        icao = stn.get("icaoId") or ""
        faa = stn.get("faaId") or ""
        sid = icao[1:] if icao.startswith("K") else faa
        if not sid or sid not in station_lines:
            continue
        # Check distance to nearest route segment
        stn_lat, stn_lon = stn["lat"], stn["lon"]
        min_dist = min(
            _nm_distance(stn_lat, stn_lon, lat, lon)
            for lon, lat in waypoint_coords
        )
        if min_dist < 100:
            route_stations.append((sid, min_dist, station_lines[sid]))

    route_stations.sort(key=lambda x: x[1])

    if not route_stations:
        print("no stations near route")
        return ""

    # Build formatted text
    result_lines = []
    for hl in header_lines:
        result_lines.append(hl)
    if ft_line:
        result_lines.append(ft_line)
    for sid, dist, line in route_stations[:15]:  # max 15 stations
        result_lines.append(line)

    print(f"{len(route_stations)} stations")
    return "\n".join(result_lines)


def _route_bbox(waypoint_coords, margin_deg=2.0):
    """Compute a bounding box string for route waypoints."""
    lats = [lat for lon, lat in waypoint_coords]
    lons = [lon for lon, lat in waypoint_coords]
    return f"{min(lats)-margin_deg},{min(lons)-margin_deg},{max(lats)+margin_deg},{max(lons)+margin_deg}"


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
    <h1>{route_display}</h1>
    <span class="badge">{altitude_ft} ft</span>
  </div>
  <div class="meta">
    <span>Departure <strong>{dep_str}</strong></span>
    <span>Arrival <strong>{arr_str}</strong></span>
    <span>Route <strong>{total_nm} nm</strong></span>
    <span>Charts <strong>{chart_count}</strong></span>
    <span>Generated <strong>{generated}</strong></span>
  </div>
</header>

<div class="container">

{taf_section_html}
{afd_section_html}
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
  <button id="lb-prev" onclick="navLightbox(-1)">&#8249;</button>
  <button id="lb-next" onclick="navLightbox(1)">&#8250;</button>
  <img id="lb-img" src="" alt="">
  <div id="lb-label"></div>
  <div id="lb-counter"></div>
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
  #lb-prev, #lb-next {{
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    background: rgba(255,255,255,0.1);
    border: none;
    color: #fff;
    font-size: 3rem;
    cursor: pointer;
    padding: 0.5rem 0.8rem;
    border-radius: 6px;
    opacity: 0.6;
    z-index: 10000;
    line-height: 1;
  }}
  #lb-prev {{ left: 1rem; }}
  #lb-next {{ right: 1rem; }}
  #lb-prev:hover, #lb-next:hover {{ opacity: 1; background: rgba(255,255,255,0.2); }}
  #lb-img {{
    max-width: 90vw;
    max-height: 85vh;
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
  #lb-counter {{
    color: var(--muted);
    font-size: 0.7rem;
    margin-top: 0.25rem;
    opacity: 0.6;
  }}
</style>
<script>
  var lbImages = [];
  var lbIndex = 0;
  document.querySelectorAll('.chart-card img').forEach(function(img, idx) {{
    var label = img.closest('.chart-card').querySelector('.chart-label');
    lbImages.push({{ src: img.src, label: label ? label.textContent : '' }});
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', function(e) {{
      e.stopPropagation();
      lbIndex = idx;
      showLightbox();
    }});
  }});
  function showLightbox() {{
    var lb = document.getElementById('lightbox');
    document.getElementById('lb-img').src = lbImages[lbIndex].src;
    document.getElementById('lb-label').textContent = lbImages[lbIndex].label;
    document.getElementById('lb-counter').textContent = (lbIndex+1) + ' / ' + lbImages.length;
    lb.classList.add('active');
    document.body.style.overflow = 'hidden';
  }}
  function closeLightbox() {{
    document.getElementById('lightbox').classList.remove('active');
    document.body.style.overflow = '';
  }}
  function navLightbox(dir) {{
    event.stopPropagation();
    lbIndex = (lbIndex + dir + lbImages.length) % lbImages.length;
    document.getElementById('lb-img').src = lbImages[lbIndex].src;
    document.getElementById('lb-label').textContent = lbImages[lbIndex].label;
    document.getElementById('lb-counter').textContent = (lbIndex+1) + ' / ' + lbImages.length;
  }}
  document.addEventListener('keydown', function(e) {{
    if (!document.getElementById('lightbox').classList.contains('active')) return;
    if (e.key === 'Escape') closeLightbox();
    if (e.key === 'ArrowLeft') navLightbox(-1);
    if (e.key === 'ArrowRight') navLightbox(1);
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
      <div class="chart-card{extra_class}">
        <img src="data:{media_type};base64,{b64}" alt="{label}" loading="lazy">
        <div class="chart-caption">
          <span class="chart-label">{label}</span>
          <a href="{url}" target="_blank" rel="noopener">Source ↗</a>
        </div>
      </div>"""

def _card_class(label):
    """Return extra CSS class for chart cards."""
    return ""


def _highlight_taf_line(taf_text, target_ddhh, role_label):
    """Bold the TAF line that covers target_ddhh (e.g. '1019' = 10th day 19Z).

    TAF lines start with FM, TEMPO, PROB, BECMG or the initial line.
    We find the last FM/initial group whose start time is at or before target.
    """
    lines = taf_text.split("\n")
    # Parse each line's validity start as DDHH
    line_times = []  # (index, ddhh_start_str)
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Initial TAF line: "TAF KMQY 092333Z 1000/1024 ..."
        #   validity starts at DDHH from the "DDHH/DDHH" group
        m = re.search(r'\b(\d{4})/(\d{4})\b', stripped)
        if i == 0 and m:
            line_times.append((i, m.group(1)))
            continue
        # FM lines: "FM101600" → starts at DDHH=1016
        m = re.match(r'\s*FM(\d{6})', stripped)
        if m:
            line_times.append((i, m.group(1)[:4]))  # take DDHH, drop MM
            continue

    # Find the line whose start is at or before target
    best_idx = None
    for idx, start in line_times:
        if start <= target_ddhh:
            best_idx = idx

    if best_idx is not None:
        tag = f'<b style="color:var(--amber)"> ◀ {role_label}</b>'
        # Bold the line and append the tag
        original = lines[best_idx]
        lines[best_idx] = f'<b style="color:#fff">{original}</b>{tag}'

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude analysis — returns (briefing_html, significant_labels, prompts)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a highly experienced CFI/CFII and charter pilot talking directly to a fellow pilot. "
    "Your job is to help them decide whether to fly, when to fly, and how to fly it safely.\n\n"
    "CRITICAL — DO NOT OVERSTATE CONDITIONS:\n\n"
    "Do not describe IMC, icing, turbulence, or convection as 'likely' unless it is directly supported "
    "by forecast data (TAFs, cloud cover, icing charts, satellite, turbulence guidance, or widespread QPF) "
    "that clearly overlaps the route and timing.\n\n"
    "If conditions are patchy, terrain-driven, altitude-dependent, or uncertain, say so explicitly using words like:\n"
    "- 'possible'\n"
    "- 'localized'\n"
    "- 'conditional'\n"
    "- 'brief'\n\n"
    "Do not upgrade limited or terrain-localized weather into route-wide conditions.\n\n"
    "WHEN DESCRIBING ANY HAZARD, classify it as one of:\n"
    "- WIDESPREAD\n"
    "- LOCALIZED\n"
    "- CONDITIONAL\n"
    "- LOW CONFIDENCE\n\n"
    "Use those words where appropriate.\n\n"
    "MOISTURE GATING RULE:\n\n"
    "Clouds, IMC, and icing require explicit evidence of moisture.\n"
    "Do not infer moisture from temperature, terrain, or wind alone.\n\n"
    "If icing charts, QPF, or cloud fields do not show meaningful moisture:\n"
    "- assume no widespread IMC\n"
    "- assume no significant icing risk\n\n"
    "Localized terrain clouds may still exist but must be described as LOCALIZED or CONDITIONAL.\n\n"
    "PRIMARY RISK RULE:\n\n"
    "Identify and explicitly state the single most important operational risk at the beginning of the briefing:\n\n"
    "Primary Risk: <one sentence>\n\n"
    "Structure the entire briefing around this risk.\n"
    "Clearly label secondary risks as secondary or conditional.\n\n"
    "CAUSE → EFFECT RULE:\n\n"
    "For each hazard:\n"
    "- cite the data\n"
    "- explain the mechanism\n"
    "- describe the pilot impact\n\n"
    "Do not skip steps or jump to conclusions.\n\n"
    "ROUTE/TIME OVERLAP RULE:\n\n"
    "Only describe weather as affecting the flight if it overlaps:\n"
    "- route\n"
    "- time window\n"
    "- altitude band\n\n"
    "Do not extrapolate beyond these.\n\n"
    "ALTITUDE SENSITIVITY RULE:\n\n"
    "State how each hazard changes with altitude:\n"
    "- better above?\n"
    "- worse above?\n"
    "- avoidable?\n\n"
    "Call out escape altitudes when relevant.\n\n"
    "ABSENCE OF EVIDENCE RULE:\n\n"
    "If supporting data is missing:\n"
    "- explicitly say so\n\n"
    "Examples:\n"
    "- 'No significant icing signal present'\n"
    "- 'No widespread moisture indicated'\n\n"
    "Do not assume hazards without evidence.\n\n"
    "DECISION CLARITY RULE:\n\n"
    "Tie GO / MARGINAL / NO-GO directly to:\n"
    "- the primary risk\n"
    "- whether it is manageable\n\n"
    "Avoid vague reasoning.\n\n"
    "LANGUAGE PRECISION RULE:\n\n"
    "- Use 'likely' only with strong evidence\n"
    "- Otherwise use 'possible' or 'conditional'\n\n"
    "Do not mix strong language with weak data.\n\n"
    "DECISIVENESS RULE:\n\n"
    "Be decisive, but only where supported.\n\n"
    "- Strong evidence → direct language\n"
    "- Weak evidence → explicit uncertainty\n\n"
    "Do not default to overly cautious wording.\n\n"
    "TERRAIN VS SYSTEM RULE:\n\n"
    "Distinguish:\n"
    "- large-scale systems\n"
    "- terrain-driven effects\n\n"
    "Do not describe terrain effects as route-wide conditions.\n\n"
    "PILOT MENTAL MODEL:\n\n"
    "Include one sentence describing the day:\n\n"
    "Examples:\n"
    "- 'This is a wind and terrain day, not a weather system day'\n"
    "- 'This is a VMC flight with localized terrain effects'\n\n"
    "--------------------------------------------------\n\n"
    "ADDITIONAL OPERATIONAL RULES:\n\n"
    "1) AFD OUTPUT REQUIREMENT:\n"
    "You MUST include:\n\n"
    "<h3>What Forecasters Are Saying</h3>\n\n"
    "Summarize in 2-4 bullets:\n"
    "- key concerns\n"
    "- confidence\n"
    "- terrain/local effects\n"
    "- trigger conditions\n\n"
    "AFD must:\n"
    "- adjust Confidence\n"
    "- influence at least one operational section\n\n"
    "Do not use AFD to introduce new hazards.\n\n"
    "2) ARRIVAL WIND ANALYSIS:\n"
    "For destination:\n"
    "- likely runway(s)\n"
    "- estimated surface wind (TAF + winds aloft + terrain)\n"
    "- approximate headwind / crosswind / tailwind\n\n"
    "If terrain may distort winds:\n"
    "- say so explicitly\n"
    "- avoid definitive claims\n\n"
    "3) ABORT CRITERIA:\n"
    "Provide 2-4 conditions to:\n"
    "- not depart OR\n"
    "- divert\n\n"
    "Use thresholds where possible:\n"
    "- wind (e.g., >25G35 crosswind)\n"
    "- turbulence\n"
    "- inability to stabilize\n"
    "- ceilings/visibility\n\n"
    "Tie directly to primary risk.\n\n"
    "4) CONFIDENCE CALIBRATION:\n"
    "Do NOT assign HIGH confidence if:\n"
    "- terrain-driven effects dominate\n"
    "- gusty/variable winds expected\n"
    "- local terrain uncertainty exists\n\n"
    "Use MEDIUM or MEDIUM-HIGH instead.\n\n"
    "Confidence must reflect:\n"
    "- chart agreement\n"
    "- AFD agreement/uncertainty\n\n"
    "5) WIND ALIGNMENT RULE:\n"
    "Do not assume winds aloft = runway winds.\n\n"
    "For terrain airports:\n"
    "- treat as variable/terrain-modified\n"
    "- use 'likely' or 'possible' unless confirmed\n\n"
    "Avoid 'direct headwind' claims without surface data.\n\n"
    "6) WEAK SIGNAL SIMPLIFICATION:\n"
    "If a hazard is weak:\n"
    "- say 'not a factor' or 'minimal risk'\n\n"
    "Do not stack uncertainty language.\n\n"
    "7) UNCERTAINTY CONSISTENCY:\n"
    "All sections must follow:\n"
    "- LANGUAGE PRECISION RULE\n"
    "- ABSENCE OF EVIDENCE RULE\n"
    "- AFD CONFIDENCE ADJUSTMENT\n\n"
    "Keep uncertainty consistent across the briefing.\n\n"
    "--------------------------------------------------\n\n"
    "Write like a pilot, not a meteorologist.\n"
    "Use plain language. Be direct and opinionated, but grounded in data.\n\n"
    "Respond only with HTML body content using allowed tags.\n"
    "No html/head/body/style/script tags."
)

def analyze(origin, destination, departure_dt, altitude_ft, chart_data, taf_data=None, winds_text="", airport_names=None, afd_data=None):
    """
    Single-pass Claude query: operational briefing + chart classification.

    chart_data: list of (label, url, b64, media_type)
    taf_data: list from fetch_tafs() or dict (legacy) or None
    winds_text: formatted winds aloft text or empty string
    Returns (briefing_html, significant_labels, prompts)
    """
    client = anthropic.Anthropic()
    dep_str = departure_dt.strftime("%Y-%m-%d %H:%MZ")

    # Build image content block
    image_blocks = []
    chart_labels = []
    for label, url, b64, media_type in chart_data:
        chart_labels.append(label)
        image_blocks.append({"type": "text", "text": f"--- {label} ---"})
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })

    # Build TAF text block for the prompt
    taf_section = ""
    if taf_data:
        taf_lines = []
        if isinstance(taf_data, list):
            for entry in taf_data:
                if entry.get("taf"):
                    eta = entry.get("eta", "")
                    eta_str = eta if isinstance(eta, str) else (eta.strftime("%H:%MZ") if eta else "?")
                    hdr = entry.get("note") or entry["icao"]
                    taf_lines.append(f"  {entry['role']} {hdr} (ETA {eta_str}):\n    {entry['taf']}")
        elif isinstance(taf_data, dict):
            for role in ["origin", "destination"]:
                entry = taf_data.get(role)
                if entry and entry.get("taf"):
                    hdr = entry["note"] if entry["note"] else entry["icao"]
                    taf_lines.append(f"  {role.upper()} ({hdr}):\n    {entry['taf']}")
        if taf_lines:
            taf_section = "\n  TAFs:\n" + "\n".join(taf_lines) + "\n"

    now_str = datetime.now(timezone.utc).strftime("%A %Y-%m-%d %H:%MZ")
    dep_day = departure_dt.strftime("%A")

    winds_section = ""
    if winds_text:
        winds_section = f"\n  Winds/Temps Aloft (stations near route):\n{winds_text}\n"

    afd_section = ""
    if afd_data:
        afd_lines = []
        for entry in afd_data:
            afd_lines.append(f"  {entry['role']} WFO {entry['wfo']}:\n{entry['text']}")
        if afd_lines:
            afd_section = "\n  Area Forecast Discussion — AVIATION:\n" + "\n\n".join(afd_lines) + "\n"

    # Airport names — prevents LLM from guessing wrong names
    names_section = ""
    if airport_names:
        name_lines = "\n".join(f"    {k} = {v}" for k, v in airport_names.items())
        names_section = f"\n  Airport Names (use these exact names):\n{name_lines}\n"

    flight_header = f"""
FLIGHT
  Today        : {now_str}
  Route        : {origin.upper()} → {destination.upper()}
  Departure    : {dep_day} {dep_str} UTC
  Planned Alt  : {altitude_ft:,} ft MSL
  Charts       : {len(chart_data)} weather charts
{names_section}{taf_section}{winds_section}{afd_section}"""

    label_list = "\n".join(f"  - {l}" for l in chart_labels)

    def _stream_with_retry(prompt, max_tokens=4096, retries=3):
        """Stream a Claude request with retry on rate limit."""
        for attempt in range(retries):
            try:
                result = ""
                with client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                ) as stream:
                    for text in stream.text_stream:
                        result += text
                        print(".", end="", flush=True)
                print(" done.")
                return result
            except anthropic.RateLimitError:
                wait = 30 * (attempt + 1)
                print(f" rate limited, waiting {wait}s ...", end="", flush=True)
                time.sleep(wait)
        print(" FAILED after retries.")
        return ""

    # ── Single pass: classification + operational briefing ────────────────
    briefing_prompt = image_blocks + [{
        "type": "text",
        "text": flight_header + f"""
TASK — CHART CLASSIFICATION + PILOT BRIEFING

FIRST, output a JSON line classifying which charts show relevant weather
for this specific flight route ({origin.upper()} → {destination.upper()}).
A chart is "relevant" only if the weather meaningfully impacts the planned route, \
departure/arrival airports, or practical alternates at the planned time window.

Do not include charts showing distant, weak, non-overlapping, or merely possible weather \
with no meaningful route impact.
Charts showing no meaningful weather impact along the route go in the reference pile.

Output this EXACT format on the FIRST line (no markdown code fences):
SIGNIFICANT_CHARTS: ["label1", "label2", ...]

All chart labels:
{label_list}

THEN a blank line, then the pilot briefing below.

---

PILOT BRIEFING — the main briefing a pilot actually reads.

This is what a weather-savvy CFI would tell you over the phone before your flight.
Write in plain pilot language. No meteorology lectures. Answer the questions pilots actually ask:
"Can I get out?", "Will I hit ice at {altitude_ft:,}?", "What's it doing when I get there?",
"Should I just wait until tomorrow?". Be direct. Be opinionated. Ground it in what the charts show.

Focus ONLY on the day before and the day/time of the flight.
If TAFs are provided above, use them for specific ceiling/visibility/wind forecasts at departure and arrival.
AREA FORECAST DISCUSSION (AFD) INTEGRATION:

AFD USAGE RULE:
Use AFD only as supporting context, not primary evidence.

AFD may be used to:
- confirm or increase confidence in hazards already supported by charts
- highlight forecaster concerns (winds, timing, uncertainty)
- identify terrain-driven effects (mountain wave, downslope winds, mixing)

AFD must NOT be used to:
- introduce new hazards not supported by charts or forecast data
- override chart-based evidence
- justify "likely" conditions on its own

AFD EXTRACTION:
If AFD is provided, extract ONLY the following:
- Key concerns (winds, clouds, precipitation, timing)
- Forecaster confidence (high / low / uncertain)
- Any mention of terrain effects (wave, downslope, mixing)
- Any "if/then" trigger conditions that could change the forecast
Summarize these in 2-4 concise bullet points before using them in the briefing.

AFD FRAMING:
When using AFD-derived insights, explicitly identify them as forecaster input.
Use phrases such as:
- "Forecaster discussion suggests..."
- "AFD highlights..."
- "Forecaster confidence is..."
Do NOT blend AFD conclusions indistinguishably with chart-based evidence.

AFD CONFIDENCE ADJUSTMENT:
Use AFD primarily to adjust confidence, not severity.
- If AFD expresses uncertainty, disagreement, or timing sensitivity:
  -> reduce confidence level in the briefing
- If AFD strongly confirms conditions:
  -> increase confidence, but do NOT increase hazard severity without chart support

AFD TRIGGER CONDITIONS:
Translate any AFD "if/then" statements into pilot-relevant decision triggers.
Examples:
- "If surface winds mix down earlier..." -> potential for stronger gusts at destination
- "If cloud cover increases..." -> potential for reduced ceilings
Use these triggers to inform:
- arrival risk discussion
- timing considerations
- abort/diversion criteria

AFD PRIORITY:
Charts and forecast data define WHAT conditions exist.
AFD explains HOW CONFIDENT those conditions are and WHAT MIGHT CHANGE.
Never reverse this relationship.

REQUIRED SECTIONS (use these exact h2 headings):

<h2>The Day Before — What to Watch</h2>
What's the situation the evening before? What weather check should the pilot do that night?
Tell them what to look for and what would change the go/no-go. Keep it short and practical.

<h2>Leaving {origin.upper()} — Departure at {dep_str}</h2>
Will they get out? What are conditions like on the ground and climbing out?
Talk about what they'll see: are they punching through a layer, is it clear, is there a front nearby?
Winds on the runway, ceilings, visibility — pilot language, not METAR codes.

IMC / CEILING RULE:
Only state "likely IMC" if:
- TAFs show BKN/OVC ceilings at or below likely climb altitudes, OR
- cloud/icing products show a continuous saturated/cloud-bearing layer overlapping the route and timing.

If neither is present:
- do NOT say "likely IMC"
- instead describe cloud layers, coverage, and where IMC could occur, if at all.

Clearly distinguish:
- widespread departure IMC
- brief layer penetration
- localized terrain cloud
- mostly VMC with pockets of cloud

<h2>The Ride at {altitude_ft:,} ft</h2>
What's the flight actually going to be like at {altitude_ft:,} ft?

ICING ANALYSIS (you have FIP charts at multiple altitudes — use them):
- Am I above or below the freezing level at {altitude_ft:,} ft? Where is the freezing level?
- What's the worst icing band? Which altitude range has the highest icing probability?
- What's the best cruise altitude to avoid or minimize icing along this route?
- If I pick up ice, where's my escape altitude — up or down?
- Any SLD risk? SLD is a hard no-go for most GA aircraft.
Use a mini table for the vertical icing picture: Altitude | Icing Prob | Notes

ICING INTERPRETATION RULE:
Icing requires BOTH temperature AND moisture.
If icing charts show little or no icing probability, assume limited moisture even if temperatures are favorable.

Do not infer widespread icing without a clear icing signal.
State clearly whether icing is:
- WIDESPREAD
- LOCALIZED
- CONDITIONAL
- LOW CONFIDENCE

If no SLD signal is shown, say that explicitly. If SLD risk exists, treat it as a hard stop for most GA aircraft.

TURBULENCE & RIDE QUALITY:
- Smooth or bumpy? Where are the rough spots?
- Winds aloft: If winds/temps data is provided above, decode and present the actual winds
  at cruise altitude for stations along the route. Calculate headwind/tailwind/crosswind
  components for each leg. Estimate total wind effect on flight time.

TERRAIN & WIND RULE:
Strong winds over terrain may produce mountain wave, rotor, and turbulence even in clear air.

Treat this as:
- LOCALIZED or CONDITIONAL hazard unless widespread turbulence guidance supports broader impact.

Do not ignore terrain-driven turbulence.
Do not assume smooth conditions just because skies are clear.

If altitude materially changes the risk, say so explicitly.

OTHER HAZARDS:
- Any weather to dodge or plan around?
Use a table: Hazard | Risk | Leg | What to Expect
Incorporate findings from icing (FIP), turbulence (GTG), G-AIRMET, SIGMET, SigWx, and QPF charts.
Heavy QPF near the route means IMC, potential icing, and possible convection.

<h2>Getting Into {destination.upper()}</h2>
What does the pilot walk into on arrival? Estimate block time and describe arrival conditions.
Is there a front nearby? Are ceilings dropping? Is it a non-event? Pick good alternates.

When discussing arrival conditions, distinguish between:
- widespread en route cloud/IMC
- terrain-localized cloud near arrival
- a non-event VMC arrival

Do not imply route-wide IMC unless supported by overlapping forecast evidence.

<h2>Fly or No</h2>
Bottom line up front. Start with a clear verdict: GO / MARGINAL GO / NO-GO.
Hard stops first. Then the stuff to watch. Be opinionated — this is what the pilot needs.

IFR JUSTIFICATION RULE:
If recommending IFR, clearly state WHY:
- IMC (clouds/visibility)
- terrain / altitude / routing complexity
- workload / safety margin

Do not imply IMC as the reason unless it is clearly supported by the data.

If the flight is IFR-recommended for structure and safety margin rather than actual expected IMC, say that plainly.

<h2>If You Go — Do This</h2>
Concrete action items. Departure time tweak, altitude change, specific alternates, fuel stop,
what forecast products to check the night before and morning of.

<h2>Confidence</h2>
State your confidence level (High / Medium / Low) and what forecast elements could still change the go/no-go picture.

When the data signal is weak, mixed, or terrain-localized, preserve that uncertainty in the wording.
Do not convert "possible" into "likely" unless the forecast evidence clearly supports it.
Operational tone is encouraged, but accuracy and uncertainty preservation come first.

Respond with HTML using: h2, h3, h4, p, ul, ol, li, strong, em, blockquote, table, thead, tbody, tr, th, td, code, hr.
No html/head/body/style/script tags.""",
    }]

    print(f"\nAnalyzing {len(chart_data)} charts ", end="", flush=True)
    raw_html = _stream_with_retry(briefing_prompt, max_tokens=6144)

    # Parse SIGNIFICANT_CHARTS from the first line
    significant_labels = set()
    sig_match = re.match(r'SIGNIFICANT_CHARTS:\s*(\[.*?\])', raw_html, re.DOTALL)
    if sig_match:
        try:
            labels = json.loads(sig_match.group(1))
            significant_labels = set(labels)
        except (json.JSONDecodeError, TypeError):
            pass
        briefing_html = raw_html[sig_match.end():].lstrip("\n")
    else:
        briefing_html = raw_html

    # Fallback: if parsing failed, treat all charts as significant
    if not significant_labels:
        significant_labels = set(chart_labels)

    # Extract text-only prompt for cache
    def _prompt_text(blocks):
        return "\n".join(b["text"] for b in blocks if b.get("type") == "text")

    prompts = {
        "system": SYSTEM_PROMPT,
        "briefing_prompt": _prompt_text(briefing_prompt),
    }

    return briefing_html, significant_labels, prompts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aviation weather briefing from WPC prog charts + Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("route",       nargs="+",
                        help="Route: ORIGIN [WAYPOINTS...] DESTINATION DATE TIME ALTITUDE")
    parser.add_argument("--tas",       type=int, default=150,
                        help="True airspeed in knots for ETA estimates (default: 150)")
    parser.add_argument("--no-route",  action="store_true",
                        help="Skip drawing route line on charts")
    parser.add_argument("--all",       action="store_true",
                        help="Fetch all available charts instead of auto-selecting")
    parser.add_argument("--no-open",   action="store_true",
                        help="Save HTML but do not open browser automatically")
    parser.add_argument("--cache",      action="store_true",
                        help="Save LLM output + chart data to a cache file for re-rendering")
    parser.add_argument("--from-cache", metavar="FILE",
                        help="Skip fetching/analysis — rebuild HTML from a cache file")
    args = parser.parse_args()

    # Parse route: AIRPORT [AIRPORT...] DATE TIME ALTITUDE
    # Last 3 positional args are always date, time, altitude
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

    origin = airports[0]
    destination = airports[-1]

    try:
        departure_dt = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit("Error: Use YYYY-MM-DD for date and HH:MM for time (UTC).")

    now_utc = datetime.now(timezone.utc)
    hours_until = (departure_dt - now_utc).total_seconds() / 3600

    route_str = " → ".join(airports)
    print(f"\nFlight   : {route_str}")
    print(f"Departs  : {departure_dt.strftime('%Y-%m-%d %H:%MZ')} ({hours_until:+.1f} hrs from now)")
    print(f"Altitude : {altitude:,} ft MSL")
    print(f"TAS      : {args.tas} kts")

    if hours_until < -2 and not args.from_cache:
        sys.exit("Error: Departure time is more than 2 hours in the past.")
    if hours_until > 7 * 24:
        print("Warning: >7 days out — extended prog reliability is very low.")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    dep_str_safe = departure_dt.strftime("%Y-%m-%d")
    cache_prefix = f"cache_{origin}_{destination}_{dep_str_safe}"

    if args.from_cache:
        # ── Rebuild from cache — no fetching, no API calls ──────────────
        # --from-cache takes the prefix or the _charts.json path
        prefix = args.from_cache.replace("_charts.json", "").replace("_llm.json", "")
        charts_path = prefix + "_charts.json"
        llm_path = prefix + "_llm.json"
        print(f"\nLoading cache: {prefix}_*.json")
        with open(charts_path, "r", encoding="utf-8") as f:
            charts_cache = json.load(f)
        with open(llm_path, "r", encoding="utf-8") as f:
            llm_cache = json.load(f)
        chart_data = [tuple(c) for c in charts_cache["chart_data"]]
        taf_data = llm_cache.get("taf_data")
        afd_data = llm_cache.get("afd_data", [])
        briefing_html = llm_cache.get("briefing_html", llm_cache.get("synoptic_html", ""))
        # Legacy caches may have synoptic_html but no briefing_html
        significant_labels = set(llm_cache["significant_labels"])
        print(f"  {len(chart_data)} charts, {len(significant_labels)} significant")

        # Draw route overlay on cached charts
        if not args.no_route:
            print("Resolving route ... ", end="", flush=True)
            waypoints, _ = resolve_route_coords(airports)
            if waypoints:
                print(f"{len(waypoints)} waypoints, drawing ... ", end="", flush=True)
                overlaid = 0
                for i, (label, url, b64, mt) in enumerate(chart_data):
                    new_b64 = draw_route_on_chart(b64, mt, waypoints)
                    if new_b64 is not b64:
                        chart_data[i] = (label, url, new_b64, "image/png")
                        overlaid += 1
                print(f"{overlaid} charts")
            else:
                print("FAILED")
    else:
        # ── Resolve route coordinates (used for overlay + legs) ───────
        waypoints = None
        airport_names = {}
        if not args.no_route:
            print("\nResolving route coordinates ... ", end="", flush=True)
            waypoints, airport_names = resolve_route_coords(airports)
            if waypoints:
                print(f"{len(waypoints)} waypoints")
                for apt, name in airport_names.items():
                    print(f"  {apt}: {name}")
            else:
                print("FAILED (continuing without route overlay)")

        # ── Normal flow: fetch charts + run analysis ────────────────────
        if hours_until <= 18:
            print(f"AWC products: Icing FL{_nearest_level(altitude, ICING_LEVELS):03d}, "
                  f"Turb FL{_nearest_level(altitude, TURBULENCE_LEVELS):03d}, "
                  f"G-AIRMET, SIGMET, SigWx")
        else:
            print("AWC products: skipped (>18 hrs out)")

        chart_list = (all_charts(altitude) if args.all
                      else select_charts(max(hours_until, 0), altitude))
        print(f"\nFetching {len(chart_list)} chart(s):")

        chart_data = []
        for fhr, url, label in chart_list:
            result = fetch_chart(url, label, forecast_hr=fhr)
            if result:
                chart_data.append(result)

        if not chart_data:
            sys.exit("Error: Could not fetch any charts. Check your internet connection.")

        # Draw route overlay on charts
        if waypoints:
            overlaid = 0
            for i, (label, url, b64, mt) in enumerate(chart_data):
                new_b64 = draw_route_on_chart(b64, mt, waypoints)
                if new_b64 is not b64:
                    chart_data[i] = (label, url, new_b64, "image/png")
                    overlaid += 1
            print(f"{overlaid} charts")

        # Compute route legs and fetch TAFs
        print("\nComputing route legs:")
        route_legs = compute_route_legs(airports, waypoints if waypoints else [], departure_dt, args.tas)
        for leg in route_legs:
            eta_str = leg["eta"].strftime("%H:%MZ")
            print(f"  {leg['icao']:6s} {leg['role']:10s} ETA {eta_str}  ({leg['cum_nm']:.0f} nm)")

        print("\nFetching TAFs:")
        taf_data = fetch_tafs(airports, route_legs)

        print("\nFetching winds aloft:")
        winds_text = fetch_winds_aloft(waypoints, hours_until)

        print("\nFetching AFD aviation sections:")
        afd_data = fetch_afd_aviation(waypoints) if waypoints else []

        briefing_html, significant_labels, prompts = analyze(
            origin, destination, departure_dt, altitude, chart_data, taf_data, winds_text, airport_names, afd_data
        )

        if args.cache:
            # Charts file — binary image data + metadata
            charts_obj = {
                "origin": origin,
                "destination": destination,
                "departure": departure_dt.strftime("%Y-%m-%d %H:%MZ"),
                "altitude_ft": altitude,
                "chart_data": chart_data,
            }
            charts_path = os.path.join(base_dir, cache_prefix + "_charts.json")
            with open(charts_path, "w", encoding="utf-8") as f:
                json.dump(charts_obj, f)

            # LLM file — prompts, responses, TAFs, classification
            llm_obj = {
                "origin": origin,
                "destination": destination,
                "departure": departure_dt.strftime("%Y-%m-%d %H:%MZ"),
                "altitude_ft": altitude,
                "taf_data": [{k: v for k, v in e.items() if k != "eta_dt"} for e in taf_data] if isinstance(taf_data, list) else taf_data,
                "winds_text": winds_text,
                "afd_data": afd_data,
                "prompts": prompts,
                "briefing_html": briefing_html,
                "significant_labels": sorted(significant_labels),
            }
            llm_path = os.path.join(base_dir, cache_prefix + "_llm.json")
            with open(llm_path, "w", encoding="utf-8") as f:
                json.dump(llm_obj, f)

            print(f"\nCache saved:")
            print(f"  Charts → {charts_path}")
            print(f"  LLM    → {llm_path}")

    # Split charts into significant / reference
    sig_cards = []
    ref_cards = []
    for label, url, b64, media_type in chart_data:
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
    if taf_data:
        taf_blocks = []
        for entry in (taf_data if isinstance(taf_data, list) else []):
            if not entry.get("taf"):
                continue
            role_label = entry.get("role", "")
            eta_dt = entry.get("eta_dt")
            eta_s = entry.get("eta", "")
            if eta_dt:
                target_hour = eta_dt.strftime("%d%H")
                eta_str = eta_dt.strftime("%H:%MZ")
            elif eta_s:
                target_hour = eta_s[8:10] + eta_s[11:13]  # parse "YYYY-MM-DD HH:MMZ"
                eta_str = eta_s[11:]
            else:
                target_hour = departure_dt.strftime("%d%H")
                eta_str = ""
            nm = entry.get("nm", 0)
            hdr = entry["icao"]
            note = ""
            if entry.get("note"):
                note = f' <span style="color:var(--amber);font-size:0.75rem">({entry["note"]})</span>'

            eta_badge = ""
            if eta_str:
                dist_str = f" &middot; {nm:.0f} nm" if nm > 0 else ""
                eta_badge = f' <span style="color:var(--muted);font-size:0.72rem">ETA {eta_str}{dist_str}</span>'

            taf_html = _highlight_taf_line(entry["taf"], target_hour, role_label)

            taf_blocks.append(
                f'<div style="margin-bottom:0.75rem">'
                f'<strong>{hdr}</strong>'
                f' <span style="color:var(--blue);font-size:0.75rem;font-weight:700">{role_label}</span>'
                f'{eta_badge}{note}'
                f'<pre style="margin-top:0.3rem;font-size:0.78rem;color:var(--green);'
                f'white-space:pre-wrap;line-height:1.5">{taf_html}</pre></div>'
            )

        # Fallback: legacy dict format from old cache files
        if not taf_blocks and isinstance(taf_data, dict):
            for role in ["origin", "destination"]:
                entry = taf_data.get(role)
                if entry and entry.get("taf"):
                    role_label = "Departure" if role == "origin" else "Arrival"
                    target_hour = departure_dt.strftime("%d%H")
                    taf_html = _highlight_taf_line(entry["taf"], target_hour, role_label)
                    note = ""
                    if entry.get("note"):
                        note = f' <span style="color:var(--amber);font-size:0.75rem">({entry["note"]})</span>'
                    taf_blocks.append(
                        f'<div style="margin-bottom:0.75rem">'
                        f'<strong>{entry["icao"]}</strong>'
                        f' <span style="color:var(--blue);font-size:0.75rem;font-weight:700">{role_label}</span>'
                        f'{note}'
                        f'<pre style="margin-top:0.3rem;font-size:0.78rem;color:var(--green);'
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
    if afd_data:
        afd_blocks = []
        for entry in afd_data:
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

    # Compute arrival info from route legs or TAF data
    arr_str = "—"
    total_nm = "—"
    if isinstance(taf_data, list) and taf_data:
        last = taf_data[-1]
        arr_str = last.get("eta", "—")
        total_nm = f"{last.get('nm', 0):.0f}"

    now_utc = datetime.now(timezone.utc)
    html = HTML_TEMPLATE.format(
        origin=origin,
        destination=destination,
        route_display=" → ".join(airports),
        dep_date=date_str,
        dep_str=departure_dt.strftime("%Y-%m-%d %H:%MZ"),
        arr_str=arr_str,
        total_nm=total_nm,
        altitude_ft=f"{altitude:,}",
        generated=now_utc.strftime("%Y-%m-%d %H:%MZ"),
        taf_section_html=taf_section_html,
        afd_section_html=afd_section_html,
        chart_count=len(chart_data),
        sig_count=len(sig_cards),
        significant_chart_cards="\n".join(sig_cards),
        reference_section=reference_section,
        briefing_html=briefing_html,
    )

    gen_stamp = now_utc.strftime("%Y%m%d_%H%MZ")
    fname = f"briefing_{origin}_{destination}_{date_str}_{gen_stamp}.html"
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nSaved → {out_path}")
    if not args.no_open:
        webbrowser.open(f"file://{out_path}")


if __name__ == "__main__":
    main()
