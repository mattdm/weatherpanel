"""Render tests using real sample forecast data from NOAA JSON fixtures.

Parses each location's hourly + griddata (+ optional historical) through the
real Station methods, then renders the display at three "current times" and
compares against committed reference PNGs.

Time offsets represent how many hours of the forecast have expired:
  t00h — full display (all 64+ hours visible)
  t16h — 16 hours expired, ~48 columns visible
  t40h — 40 hours expired, ~25 columns visible
"""
import json
from pathlib import Path

import pytest

import network
from station import Station
from render_helpers import compare_or_save

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"

# ---------------------------------------------------------------------------
# Locations and time offsets to parametrize
# ---------------------------------------------------------------------------

# (location_name, has_historical)
_SCENARIOS = [
    ("boston",       True),
    ("fargo",        True),
    ("honolulu",     True),
    ("soda_springs", False),
]

_TIME_OFFSETS = [0, 16, 40]

_PARAMS = [
    pytest.param(loc, hist, offset, id=f"{loc}_t{offset:02d}h")
    for loc, hist in _SCENARIOS
    for offset in _TIME_OFFSETS
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
# Parametrized render tests
# ---------------------------------------------------------------------------

class TestForecastRender:
    @pytest.mark.parametrize("location,has_historical,offset", _PARAMS)
    def test_forecast_render(self, sim_display, request, monkeypatch,
                             location, has_historical, offset):
        """Render a real forecast at a given time offset and compare to reference."""
        station = _load_station(location, monkeypatch, has_historical)

        # Use the start of hour[offset] as current_time so hours 0..offset-1
        # have already ended and will be skipped by the renderer.
        current_time = station.hourly[offset].start

        sim_display.update_hourly_forecast(
            station.hourly, station.historical, current_time
        )

        compare_or_save(request, sim_display, f"forecast_{location}_t{offset:02d}h")
