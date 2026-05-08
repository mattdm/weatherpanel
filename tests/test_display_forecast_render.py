"""Render tests using real sample forecast data from NOAA JSON fixtures.

Parses each location's hourly + griddata + historical through the real Station
methods, renders the display, and compares against committed reference PNGs.

All locations have historical baseline data stored in sample-forecasts/ as
*_historical.json files.  Tests fail if a file is missing rather than
silently skipping — use make update-libraries to keep fixtures current.

Two Alaska locations (anchorage_ak, ketchikan_ak) have null in their
historical files because ACIS grid 21 (PRISM) returns an empty HTTP body for
out-of-coverage coordinates.  The null fixture causes get_historical_day to
return None for all slots — the same graceful degradation that occurs on the
live device — so the display renders without climate baseline color-coding.

The main parametrized test uses offset=0 (fresh forecast), which fills all 64
display columns — as it always looks in normal operation, since the scheduler
fetches 65 hours specifically to guarantee a full display.

Stale-forecast tests show what happens when the data has aged:
  - boston_stale_8h:  8 hours expired (~12% through the forecast)
  - fargo_stale_24h: 24 hours expired (~37%, roughly "tomorrow morning")

Missing-historical tests use locations where the forecast is dramatically
outside the climate baseline, making the color difference obvious:
  - honolulu: all 65 hours above historical ave-high (73–84°F vs 47°F avg)
  - boston: most hours below historical ave-low (cold snap: 32–58°F vs 47°F)
"""
import json
from pathlib import Path

import pytest

import network
from station import Station
from render_helpers import compare_or_save

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"

_NO_HISTORICAL = [None, None, None, None]

# ---------------------------------------------------------------------------
# All 16 locations — fresh forecast (offset=0)
# ---------------------------------------------------------------------------

_SCENARIOS = [
    # Original 16
    "albuquerque",
    "austin",
    "boston",
    "dallas",
    "elkhart",
    "everglades",
    "fargo",
    "grand_junction",
    "honolulu",
    "jefferson_wi",
    "phoenix",
    "san_antonio",
    "seattle",
    "soda_springs",
    "somerville",
    "yosemite",
    # New 14 — live forecasts from 2026-05-08
    "anchorage_ak",       # rain showers, 41–49°F; no ACIS history (null)
    "cape_flattery_wa",   # boring: 4°F temp spread, near-zero precip
    "chicago_il",
    "death_valley_ca",    # 97–107°F peak heat
    "denver_co",          # post-snowstorm recovery; storms tomorrow
    "eugene_or",          # 0% precip all 65h
    "evanston_il",
    "franklin_county_ms", # 62–85% thunderstorms, EF3 tornado today
    "ketchikan_ak",       # 100% precip; no ACIS history (null)
    "lebanon_ks",         # geographic center of CONUS
    "miami_fl",           # 85–86°F, sunny
    "mt_washington_nh",   # snow showers at 31°F in May
    "new_orleans_la",     # 40–61% thunderstorms
    "oklahoma_city_ok",   # afternoon thunderstorm potential
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(name):
    path = SAMPLE_DIR / name
    if not path.exists():
        pytest.fail(f"Missing fixture: {path}")
    with open(path) as f:
        return json.load(f)


def _make_station():
    config = {
        "GEOLOCATION_API": "http://test/geo",
        "GRIDPOINT_API":   "https://test/points",
        "HISTORICAL_API":  "https://test/historical",
    }
    s = Station(config)
    s.hourly_url   = "https://test/hourly"
    s.griddata_url = "https://test/griddata"
    s.station_id   = "TEST"
    s.lat          = "39.317"
    s.lon          = "-120.333"
    s.location     = "39.317,-120.333"
    return s


def _load_station(name, monkeypatch):
    """Parse sample JSON for a location through real Station methods.

    Loads hourly, griddata, and historical fixtures.  Fails immediately if
    any fixture file is missing.  The same historical baseline is returned
    for all three calendar-day slots (today/tomorrow/day-after) because the
    3-day window average changes negligibly across consecutive days.
    """
    hourly_json   = _load(f"{name}_hourly.json")
    griddata_json = _load(f"{name}_griddata.json")
    hist_json     = _load(f"{name}_historical.json")

    call_count = {"n": 0}

    def fake_get(url, headers=None):
        call_count["n"] += 1
        return hourly_json if call_count["n"] == 1 else griddata_json

    monkeypatch.setattr(network, "get", fake_get)
    monkeypatch.setattr(network, "post", lambda url, data: hist_json)

    s = _make_station()
    s.get_hourly_forecast()
    s.get_griddata()

    today = s.hourly[0].start[:10]
    for slot in range(4):
        s.get_historical_day(slot, today)

    return s


# ---------------------------------------------------------------------------
# Fresh forecast render tests (offset=0 — full 64-column display)
# ---------------------------------------------------------------------------

class TestForecastRender:
    @pytest.mark.parametrize("location", _SCENARIOS)
    def test_forecast_render(self, sim_display, request, monkeypatch, location):
        """Render a fresh forecast (all 64 columns visible) and compare to reference."""
        station = _load_station(location, monkeypatch)
        current_time = station.hourly[0].start

        sim_display.update_hourly_forecast(
            station.hourly, station.historical, current_time
        )

        compare_or_save(request, sim_display, f"forecast_{location}")


# ---------------------------------------------------------------------------
# Stale forecast render tests
# ---------------------------------------------------------------------------

class TestStaleForecastRender:
    def test_stale_boston_8h(self, sim_display, request, monkeypatch):
        """Boston forecast 8 hours stale: first 8 expired, 57 remaining columns."""
        station = _load_station("boston", monkeypatch)
        current_time = station.hourly[8].start

        sim_display.update_hourly_forecast(
            station.hourly, station.historical, current_time
        )

        compare_or_save(request, sim_display, "forecast_boston_stale_8h")

    def test_stale_fargo_24h(self, sim_display, request, monkeypatch):
        """Fargo forecast 24 hours stale: first 24 expired, 41 remaining columns."""
        station = _load_station("fargo", monkeypatch)
        current_time = station.hourly[24].start

        sim_display.update_hourly_forecast(
            station.hourly, station.historical, current_time
        )

        compare_or_save(request, sim_display, "forecast_fargo_stale_24h")


# ---------------------------------------------------------------------------
# Missing historical render tests
#
# These render the same forecast data but with historical=[None, None, None, None],
# showing what the display looks like before climate baseline data is loaded.
# Chosen for maximum visual contrast: locations where the forecast is
# dramatically outside the historical norm, so the color difference is stark.
# ---------------------------------------------------------------------------

class TestMissingHistoricalRender:
    def test_honolulu_no_history(self, sim_display, request, monkeypatch):
        """Honolulu: all forecast temps above historical ave-high (73–84°F vs 47°F).
        With history: solid orange line.  Without: all neutral gray."""
        station = _load_station("honolulu", monkeypatch)
        current_time = station.hourly[0].start

        sim_display.update_hourly_forecast(
            station.hourly, _NO_HISTORICAL, current_time
        )

        compare_or_save(request, sim_display, "forecast_honolulu_no_history")

    def test_boston_no_history(self, sim_display, request, monkeypatch):
        """Boston cold snap: most temps below historical ave-low (32–58°F vs 47°F).
        With history: cold blue line.  Without: all neutral gray."""
        station = _load_station("boston", monkeypatch)
        current_time = station.hourly[0].start

        sim_display.update_hourly_forecast(
            station.hourly, _NO_HISTORICAL, current_time
        )

        compare_or_save(request, sim_display, "forecast_boston_no_history")
