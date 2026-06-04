---
name: flight-weather
description: Generate an aviation weather briefing for a VFR/IFR flight. Use when the user asks for a weather briefing, preflight weather, go/no-go assessment, or route weather analysis for a flight. Fetches WPC prog charts, QPF, AWC icing/turbulence/SIGMET charts, overlays the flight route, and produces a self-contained HTML briefing.
---

# Flight Weather Briefing

Generate a comprehensive aviation weather briefing by running the FlightWeatherWatch CLI tool.

## Use When

Invoke this skill when the user:
- Asks for a **weather briefing** for a flight (e.g., "get me weather for BJC to Heber tomorrow")
- Mentions flying between two airports and wants to know about **weather, icing, turbulence, or winds**
- Asks to **re-run**, **regenerate**, or **rebuild** a previous flight briefing
- Requests a **go/no-go** weather assessment for an upcoming flight
- Says anything like "brief me", "pull weather", or "what's the weather look like for my flight"

Do **not** invoke for general aviation questions, NOTAMs, TFRs, or non-weather flight planning.

## Project Location

The project root is the directory containing `flightweather.py`. All commands must be run from this directory.

## Command

```bash
python3 flightweather.py <ORIGIN> [WAYPOINTS...] <DESTINATION> <DATE> <TIME_UTC> <ALTITUDE_FT> [options]
```

### Positional Arguments (in order)
- **ORIGIN**: Departure ICAO airport code (e.g., KSQL, KMQY)
- **WAYPOINTS** (optional): Intermediate stops — fuel stops, diversions, or routing waypoints
- **DESTINATION**: Arrival ICAO airport code
- **DATE**: Departure date as `YYYY-MM-DD`
- **TIME_UTC**: Departure time as `HH:MM` in **UTC** (Zulu)
- **ALTITUDE_FT**: Cruise altitude in feet MSL (integer)

### Options
| Flag | Description |
|------|-------------|
| `--tas N` | True airspeed in knots (default: 150) |
| `--cache` | Save fetched charts + LLM analysis for later re-rendering |
| `--from-cache PREFIX` | Rebuild HTML from cached data — no fetching or API calls |
| `--all` | Fetch every available chart regardless of departure time |
| `--no-route` | Skip drawing route overlay on charts |
| `--no-open` | Save HTML file but don't open browser |

## Workflow

1. **Parse the request** — resolve airport codes, convert local time to UTC, identify waypoints.
2. **Confirm inputs** — before running, verify the command looks right: correct airports, date, UTC time, altitude, `--cache` flag present.
3. **Run the tool** — execute `python3 flightweather.py ...` and monitor stdout for errors.
4. **Validate output** — after the tool completes, check:
   - Exit code is 0 (no crash or unhandled error).
   - The HTML file was created (tool prints the filename).
   - Chart fetch summary shows `OK` for most charts (a few failures are tolerable; all failures means a network issue).
   - The briefing contains a GO / NO-GO / CAUTION recommendation.
   - **Time-window alignment** (especially for briefings generated the day before): the briefing must
     anchor each product to its own valid/issue time, not the flight time. Spot-check that AFD relative
     words ("this afternoon", "tonight") are tied to the AFD's issuance day rather than the flight
     window, that arrival conditions use the TAF group valid at the arrival ETA (not an expired earlier
     group), and that ride/turbulence reasoning leans on the winds-aloft period valid for the flight —
     not vivid prior-day AFD prose. A flag like "this afternoon's winds = your departure window" on a
     next-morning flight is the classic error to catch.
5. **Report to user** — summarize the recommendation and note any fetch failures or missing data (e.g., TAFs not yet valid). If the tool errored, diagnose and re-run or advise.

## Interpreting User Requests

### Airport Codes & Time Zones
Convert city names to ICAO codes and local times to UTC. See [reference.md](reference.md) for the user's preferred airports and timezone conversion table.

### Fuel Stops / Waypoints
If the user mentions a fuel stop or intermediate point, insert it as a waypoint between origin and destination. Example: "stop at Amarillo" → add `KAMA` between origin and destination.

### Always Use `--cache`
Always include `--cache` so the briefing can be re-rendered from cache without re-fetching charts or calling the API.

## Re-running from Cache

To rebuild a briefing from cached data (no API cost):

1. Find the cache file: `ls cache_*.json` — files are named `cache_ORIGIN_DEST_DATE_charts.json`
2. Run with `--from-cache` using the prefix (without `_charts.json` or `_llm.json`):

```bash
python3 flightweather.py --from-cache cache_KMQY_KEDC_2026-03-16 KMQY KEDC 2026-03-16 15:00 12000 --tas 170
```

Note: `--from-cache` still requires the positional arguments (origin, destination, date, time, altitude) because they're used for the HTML header and route overlay. The `--from-cache` flag bypasses the past-departure-time check, so old briefings can be regenerated.

After rebuilding, verify the HTML file was created and the briefing renders correctly before reporting success to the user.

## Output

The tool produces a self-contained HTML file and opens it in the default browser. Contents:
- Weather chart images with the flight route overlaid in **magenta**
- Claude's analysis with **GO / NO-GO / CAUTION** recommendation
- Significant charts highlighted separately from reference charts
- TAF data for departure, enroute, and arrival airports
- Winds/temps aloft along the route
- Route leg ETAs and distances

## Error Handling

- **"Could not fetch any charts"**: Network issue — check internet connection and retry.
- **"Departure time is more than 2 hours in the past"**: Use `--from-cache` to rebuild old briefings, or update the departure time.
- **TAF "not yet valid"**: Normal for flights >24 hours out — TAFs only cover ~24-30 hours ahead.
- **Route resolution fails**: The AWC API may be temporarily down. The tool continues without route overlay.

## Requirements

- `ANTHROPIC_API_KEY` environment variable (or `.env` file in the project directory)
- Python 3.9+
- Dependencies: `anthropic httpx Pillow numpy scipy pyproj opencv-python pyshp`
