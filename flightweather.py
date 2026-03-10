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
import os
import sys
import webbrowser
from datetime import datetime, timezone
from typing import Optional

import httpx
import anthropic


# ---------------------------------------------------------------------------
# Chart catalog
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Chart selection
# ---------------------------------------------------------------------------

def select_charts(hours_until):
    result = []

    if hours_until <= 72:
        candidates = [(h, u, l) for h, u, l in SHORT_TERM_CHARTS if h <= hours_until + 12]
        result.extend(candidates[-3:])

    # Long-range: include ALL short-term charts to show pattern evolution
    if hours_until > 60:
        for item in SHORT_TERM_CHARTS:
            result.append(item)

    if hours_until >= 48:
        result.extend(EXTENDED_CHARTS)

    if not result:
        result = list(SHORT_TERM_CHARTS[:3])

    seen = set()
    unique = []
    for item in result:
        if item[1] not in seen:
            seen.add(item[1])
            unique.append(item)
    return unique


def all_charts():
    seen = set()
    out = []
    for item in SHORT_TERM_CHARTS + EXTENDED_CHARTS:
        if item[1] not in seen:
            seen.add(item[1])
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Image fetching — returns (label, url, base64_data, media_type)
# ---------------------------------------------------------------------------

def fetch_chart(url, label):
    try:
        print(f"  Fetching {label} ... ", end="", flush=True)
        r = httpx.get(url, timeout=20, follow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        media_type = "image/gif" if ("gif" in ct or url.lower().endswith(".gif")) else "image/jpeg"
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

  /* ── Collapsible synoptic overview ── */
  details.synoptic {{
    background: var(--raised);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }}
  details.synoptic summary {{
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
  details.synoptic summary::-webkit-details-marker {{ display: none; }}
  details.synoptic summary::before {{
    content: "▶";
    font-size: 0.65rem;
    transition: transform 0.2s;
    color: var(--muted);
  }}
  details.synoptic[open] summary::before {{ transform: rotate(90deg); }}
  details.synoptic summary:hover {{ color: var(--text); }}
  details.synoptic summary .pill {{
    margin-left: auto;
    font-size: 0.65rem;
    background: rgba(255,255,255,0.06);
    padding: 0.1rem 0.5rem;
    border-radius: 10px;
    color: var(--muted);
  }}
  .synoptic-body {{
    padding: 1.25rem 1.5rem 1.5rem;
    border-top: 1px solid var(--border);
  }}
  .synoptic-body h2 {{
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--blue);
    margin: 1.4rem 0 0.5rem;
    padding-bottom: 0.25rem;
    border-bottom: 1px solid var(--border);
  }}
  .synoptic-body h2:first-child {{ margin-top: 0; }}
  .synoptic-body h3 {{
    font-size: 0.875rem;
    font-weight: 600;
    color: var(--amber);
    margin: 1rem 0 0.3rem;
  }}
  .synoptic-body p {{ font-size: 0.85rem; margin: 0.5rem 0; }}
  .synoptic-body ul, .synoptic-body ol {{
    font-size: 0.85rem;
    margin: 0.4rem 0 0.4rem 1.4rem;
  }}
  .synoptic-body li {{ margin: 0.2rem 0; }}
  .synoptic-body strong {{ font-weight: 700; }}
  .synoptic-body em {{ font-style: normal; color: var(--amber); font-weight: 600; }}
  .synoptic-body table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    margin: 0.8rem 0;
  }}
  .synoptic-body th {{
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
  .synoptic-body td {{
    padding: 0.4rem 0.65rem;
    border: 1px solid var(--border);
    vertical-align: top;
  }}
  .synoptic-body tr:nth-child(even) td {{ background: rgba(255,255,255,0.02); }}

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
    /* Container padding */
    .container {{ padding: 0 1rem; }}

    /* Header: stack vertically, smaller h1 */
    header {{ padding: 1rem; }}
    .header-top {{
      flex-direction: column;
      align-items: flex-start;
      gap: 0.5rem;
    }}
    header h1 {{ font-size: 1.2rem; }}

    /* Meta row: wrap with smaller gaps */
    .meta {{
      flex-direction: column;
      gap: 0.35rem;
    }}

    /* Chart grid: single column, full-width cards */
    .chart-grid {{
      grid-template-columns: 1fr;
    }}

    /* Briefing tables: horizontal scroll so they don't break layout */
    .briefing {{
      overflow-x: auto;
      padding: 1rem;
    }}

    /* Details/summary synoptic: full width */
    details.synoptic {{
      width: 100%;
    }}
    .synoptic-body {{ padding: 1rem; }}

    /* Footer: smaller text, centered, wraps properly */
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
    <div class="section-label">Prog Charts</div>
    <div class="chart-grid">
{chart_cards}
    </div>
  </section>

  <section>
    <div class="section-label">Synoptic Overview</div>
    <details class="synoptic">
      <summary>
        Background pattern — chart-by-chart detail
        <span class="pill">click to expand</span>
      </summary>
      <div class="synoptic-body">
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
  FlightWeatherWatch &middot; Charts: NOAA/NWS Weather Prediction Center &middot;
  Analysis: Claude claude-sonnet-4-6 &middot;
  <strong>NOT FOR FLIGHT PLANNING — obtain an official preflight weather briefing before departure.</strong>
</footer>

</body>
</html>
"""

CHART_CARD_TEMPLATE = """\
      <div class="chart-card">
        <img src="data:{media_type};base64,{b64}" alt="{label}" loading="lazy">
        <div class="chart-caption">
          <span class="chart-label">{label}</span>
          <a href="{url}" target="_blank" rel="noopener">Source ↗</a>
        </div>
      </div>"""


# ---------------------------------------------------------------------------
# Claude analysis — returns (synoptic_html, briefing_html)
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
      1. Synoptic overview — broad pattern analysis across all charts (hidden)
      2. Operational briefing — focused on day-before and day-of at planned altitude (visible)

    chart_data: list of (label, url, b64, media_type)
    Returns (synoptic_html, briefing_html)
    """
    client = anthropic.Anthropic()
    dep_str = departure_dt.strftime("%Y-%m-%d %H:%MZ")

    # Build shared image content block
    image_blocks = []
    for label, url, b64, media_type in chart_data:
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
  Charts       : {len(chart_data)} WPC surface prog chart(s)
"""

    # ── Pass 1: Synoptic overview ──────────────────────────────────────────
    synoptic_prompt = image_blocks + [{
        "type": "text",
        "text": flight_header + """
TASK — BACKGROUND PATTERN (shown collapsed — for reference only)

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
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": synoptic_prompt}],
    ) as stream:
        for text in stream.text_stream:
            synoptic_html += text
            print(".", end="", flush=True)
    print(" done.")

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

    return synoptic_html, briefing_html


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
                        help="Fetch all available prog charts instead of auto-selecting")
    parser.add_argument("--no-open",   action="store_true",
                        help="Save HTML but do not open browser automatically")
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

    chart_list = all_charts() if args.all else select_charts(max(hours_until, 0))
    print(f"\nFetching {len(chart_list)} chart(s):")

    chart_data = []
    for _, url, label in chart_list:
        result = fetch_chart(url, label)
        if result:
            chart_data.append(result)

    if not chart_data:
        sys.exit("Error: Could not fetch any charts. Check your internet connection.")

    synoptic_html, briefing_html = analyze(
        args.origin, args.destination, departure_dt, args.altitude, chart_data
    )

    # Build chart cards
    chart_cards = "\n".join(
        CHART_CARD_TEMPLATE.format(label=label, url=url, b64=b64, media_type=media_type)
        for label, url, b64, media_type in chart_data
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
        chart_cards=chart_cards,
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
