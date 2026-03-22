# Flight Weather Briefing Setup

## Problem/Feature Description

You are a flight planning assistant helping a pilot prepare for a cross-country VFR flight. The pilot flies out of the Nashville, Tennessee area and wants to visit a friend at Austin, Texas, departing this coming Saturday, March 28, 2026 at 9:00 AM local time. They plan to cruise at 9,500 feet and their aircraft has a true airspeed of 145 knots.

The pilot uses FlightWeatherWatch to get pre-flight weather briefings. They want a shell script they can run from the FlightWeatherWatch project directory whenever they need to pull a fresh briefing for this trip.

## Output Specification

Produce a ready-to-run shell script named `get_briefing.sh` in the current working directory. The script should invoke the `flightweather.py` tool with the correct arguments for this flight, following the appropriate conventions for the tool.

Also produce a short markdown file named `notes.md` explaining the key decisions made (airport codes chosen, time conversion, any flags used and why).
