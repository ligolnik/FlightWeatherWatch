# FlightWeatherWatch

Aviation weather briefing tool that fetches prog charts and forecast products from NOAA/NWS and uses Claude to generate a pilot-centric weather briefing.

## What it does

1. Fetches weather charts from WPC and AWC based on your flight window
2. Sends them to Claude for analysis
3. Outputs a self-contained HTML briefing with embedded charts, a lightbox viewer, and collapsible sections

## Weather products

| Source | Product | Coverage |
|--------|---------|----------|
| WPC | Surface progs (6–60 hr) | Always included — shows pattern development |
| WPC | Extended progs (Day 3–7) | Flights ≥48 hrs out |
| WPC | QPF (Day 1–3) | Precipitation forecast, time-windowed |
| AWC | Icing (FIP) — prob, severity, SLD | ≤18 hrs out, at cruise FL |
| AWC | Turbulence (GTG) — total | ≤18 hrs out, at cruise FL |
| AWC | G-AIRMET — IFR, turb, freezing, icing | ≤18 hrs out |
| AWC | GFA — clouds + surface | ≤18 hrs out |
| AWC | SIGMET | ≤18 hrs out |
| AWC | SigWx Low / Mid | ≤18 hrs out |
| AWC | TCF / ETCF | ≤18 / ≤30 hrs out |

Charts without relevant weather along the route are automatically folded into a reference section.

## Usage

```bash
pip install -r requirements.txt
```

Set your API key in `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

Run a briefing:
```bash
python3 flightweather.py KMQY KEDC 2026-03-10 19:00 12000
```

Arguments: `<origin> <destination> <date> <time_utc> <altitude_ft>`

### Flags

| Flag | Description |
|------|-------------|
| `--all` | Fetch all available charts instead of auto-selecting |
| `--no-open` | Save HTML without opening in browser |
| `--cache` | Save LLM output + chart data for re-rendering |
| `--from-cache FILE` | Rebuild HTML from cache (no API calls) |

### Cache workflow

```bash
# First run — hits the API, saves cache
python3 flightweather.py --cache KMQY KEDC 2026-03-10 19:00 12000

# Iterate on HTML/template — instant, no API cost
python3 flightweather.py --from-cache cache_KMQY_KEDC_2026-03-10.json KMQY KEDC 2026-03-10 19:00 12000
```

## Output

Self-contained HTML file: `briefing_{ORIGIN}_{DEST}_{DATE}.html`

- Dark aviation theme, mobile-responsive
- Collapsible significant / reference chart galleries with lightbox zoom
- Collapsible synoptic overview (chart-by-chart technical analysis)
- Pilot-centric operational briefing: departure, enroute, arrival, go/no-go, action items

## Requirements

- Python 3.9+
- `anthropic` SDK
- `httpx`
- Anthropic API key

## Disclaimer

**NOT FOR FLIGHT PLANNING.** Obtain an official preflight weather briefing before departure.
