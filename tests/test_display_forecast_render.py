"""Render tests using real sample forecast data from NOAA JSON fixtures.

Parses each location's hourly + griddata (+ optional historical) through the
real Station methods, renders the display, and compares against committed
reference PNGs.

The main parametrized test uses offset=0 (fresh forecast), which fills all 64
display columns — as it always looks in normal operation, since the scheduler
fetches 65 hours specifically to guarantee a full display.

One additional stale-data test uses boston at offset=8 to document what the
display looks like when 8 hours have expired since the last forecast fetch.
"""
import json
from pathlib import Path

import pytest

import network
from station import Station
from render_helpers import compare_or_save

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"

# ---------------------------------------------------------------------------
# Locations: fresh forecast only (offset=0)
# ---------------------------------------------------------------------------

# (location_name, has_historical)
_SCENARIOS = [
    ("boston",       True),
    ("fargo",        True),
    ("honolulu",     True),
    ("soda_springs", False),
]

_PARAMS = [
    pytest.param(loc, hist, id=loc)
    for loc, hist in _SCENARIOS
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(name):
    with open(SAMPLE_DIR / name) as f:
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


def _load_station(name, monkeypatch, has_historical):
    """Parse sample JSON for a location through real Station methods.

    Returns a Station with hourly, griddata, and (optionally) historical data
    populated — ready to pass directly to Display.update_hourly_forecast.
    """
    hourly_json   = _load(f"{name}_hourly.json")
    griddata_json = _load(f"{name}_griddata.json")

    call_count = {"n": 0}

    def fake_get(url, headers=None):
        call_count["n"] += 1
        return hourly_json if call_count["n"] == 1 else griddata_json

    monkeypatch.setattr(network, "get", fake_get)

    if has_historical:
        hist_json = _load(f"{name}_historical.json")
        monkeypatch.setattr(network, "post", lambda url, data: hist_json)

    s = _make_station()
    s.get_hourly_forecast()
    s.get_griddata()

    if has_historical:
        today = s.hourly[0].start[:10]
        for slot in range(3):
            s.get_historical_day(slot, today)

    return s


# ---------------------------------------------------------------------------
# Fresh forecast render tests (offset=0 — full 64-column display)
# ---------------------------------------------------------------------------

class TestForecastRender:
    @pytest.mark.parametrize("location,has_historical", _PARAMS)
    def test_forecast_render(self, sim_display, request, monkeypatch,
                             location, has_historical):
        """Render a fresh forecast (all 64 columns visible) and compare to reference."""
        station = _load_station(location, monkeypatch, has_historical)
        current_time = station.hourly[0].start

        sim_display.update_hourly_forecast(
            station.hourly, station.historical, current_time
        )

        compare_or_save(request, sim_display, f"forecast_{location}")


# ---------------------------------------------------------------------------
# Stale forecast render test (offset=8 — documents what happens when 8 hours
# have elapsed since the last fetch, leaving 57 of 64 columns filled)
# ---------------------------------------------------------------------------

class TestStaleForecastRender:
    def test_stale_boston_8h(self, sim_display, request, monkeypatch):
        """Boston forecast 8 hours stale: first 8 expired, 57 remaining columns."""
        station = _load_station("boston", monkeypatch, has_historical=True)
        current_time = station.hourly[8].start

        sim_display.update_hourly_forecast(
            station.hourly, station.historical, current_time
        )

        compare_or_save(request, sim_display, "forecast_boston_stale_8h")
