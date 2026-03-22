# Rebuild Briefing from Cache

## Problem/Feature Description

A pilot ran a weather briefing yesterday for a flight from Nashville (their preferred Nashville airport) to Austin (their preferred Austin airport) on March 16, 2026 departing at 15:00 UTC, 12,000 feet, 170 knots TAS. The cached data exists.

Now they want to regenerate the HTML briefing from the cached data without making any new API calls or fetching charts. The route, date, time, and altitude remain the same.

## Output Specification

Produce the exact `python3 flightweather.py` command to rebuild this briefing from cache. Write it to a file called `rebuild.sh` and include a brief explanation of how the `--from-cache` flag works.
