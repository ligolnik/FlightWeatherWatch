# DST Timezone Edge Case

## Problem/Feature Description

A pilot wants a weather briefing for a flight from San Carlos, California to Denver (Rocky Mountain Metro). Departure is Sunday, November 1, 2026 at 7:00 AM local time. This is the day DST ends — clocks fall back at 2:00 AM. Cruise altitude is 14,000 feet MSL, TAS 160 knots.

Generate the shell command to run the FlightWeatherWatch briefing tool for this flight.

## Output Specification

Produce the exact `python3 flightweather.py` command with all required arguments and flags. Write it to a file called `run_briefing.sh`. Include a brief note in `notes.md` explaining the timezone reasoning.
