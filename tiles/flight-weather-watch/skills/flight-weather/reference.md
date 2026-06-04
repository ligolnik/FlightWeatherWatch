# Flight Weather Reference

## Preferred Airport Codes

Convert names/cities to 4-letter ICAO codes (K-prefixed in the US). The user's commonly used airports:
- Nashville area → **KMQY** (Smyrna/Rutherford County — the user's preferred Nashville airport)
- Austin → **KEDC** (Austin Executive)
- San Carlos → **KSQL**
- Las Vegas → **KVGT** (North Las Vegas)
- Denver metro → **KBJC** (Rocky Mountain Metropolitan)

For unfamiliar airports, look up the ICAO code before running.

## Local Time → UTC

The user gives departure in **local time at the departure airport**. Convert to UTC based on the airport's timezone. DST is active from the second Sunday of March through the first Sunday of November.

| Zone | Standard | Daylight |
|------|----------|----------|
| Eastern | +5 | +4 |
| Central | +6 | +5 |
| Mountain | +7 | +6 |
| Pacific | +8 | +7 |
