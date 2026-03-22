# Error Recovery — Past Departure Time

## Problem/Feature Description

A pilot ran a weather briefing two days ago for a flight from Nashville to Las Vegas on March 20, 2026 departing at 16:00 UTC, 16,000 feet, 155 knots TAS. The briefing was run with `--cache` so cached data exists.

Now they want to look at that briefing again, but when they try to run the original command they get the error: "Departure time is more than 2 hours in the past."

Help them regenerate the briefing.

## Output Specification

Produce the exact `python3 flightweather.py` command that will work despite the departure time being in the past. Write it to `run_briefing.sh` and explain in `notes.md` why the original command failed and how the solution works.
