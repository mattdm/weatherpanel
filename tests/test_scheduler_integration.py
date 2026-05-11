"""Full-cycle scheduler integration test.

Drives scheduler.run() end-to-end for one complete cycle using fixture-backed
network calls (no live HTTP), exits cleanly after the first display render,
and compares the output against a committed reference PNG.

This covers the path that unit tests miss:
    scheduler.run()
      → _ensure_network        (mocked network.check)
      → _ensure_location       (fixed lat/lon bypasses geolocation)
      → clock.sync_network_time (fake NTP)
      → _refresh_historical    (4 × network.post → fixture)
      → _ensure_station        (network.get → fixture points + stations)
      → _refresh_forecasts     (network.get → hourly + griddata fixtures)
      → display.update_hourly_forecast
      → clock.wait()           (raises _FullCycleDone to exit)
"""
import json
import time
from pathlib import Path

import pytest

import network
import scheduler
from render_helpers import compare_or_save

_SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"
_FONTS_DIR  = Path(__file__).parent.parent / "fonts"


# ---------------------------------------------------------------------------
# Sentinel exception — raised by the mocked Clock.wait() to exit the loop.
# ---------------------------------------------------------------------------

class _FullCycleDone(Exception):
    """Raised by the mocked Clock.wait() to break the scheduler loop."""


# ---------------------------------------------------------------------------
# NTP stub
# ---------------------------------------------------------------------------

class _FakeNTP:
    """Minimal NTP stub whose datetime property returns a valid struct_time."""

    @property
    def datetime(self):
        return time.localtime()


# ---------------------------------------------------------------------------
# Fixture-backed network router
# ---------------------------------------------------------------------------

def _load_fixture(name):
    with open(_SAMPLE_DIR / name) as f:
        return json.load(f)


def _make_network_router(location):
    """Return (fake_get, fake_post) backed by fixture files for the location.

    The points fixture now includes forecastHourly and forecastGridData URLs.
    The router reads those real URLs from the fixture and routes them to the
    corresponding hourly/griddata fixture files.
    """
    points_data   = _load_fixture(f"{location}_points.json")
    hourly_data   = _load_fixture(f"{location}_hourly.json")
    griddata_data = _load_fixture(f"{location}_griddata.json")
    stations_data = _load_fixture(f"{location}_stations.json")
    historical    = _load_fixture(f"{location}_historical.json")

    props        = points_data["properties"]
    hourly_url   = props["forecastHourly"]
    griddata_url = props["forecastGridData"]
    stations_url = props["observationStations"]

    def fake_get(url, headers=None):
        # Strip any query string for matching.
        base = url.split("?")[0]
        if base == hourly_url:
            return hourly_data
        if base == griddata_url:
            return griddata_data
        if base == stations_url or base.startswith(stations_url):
            return stations_data
        # Gridpoint/points lookup — return the full points fixture.
        if "points" in url or "gridpoints" in url.lower():
            return points_data
        return None

    def fake_post(url, querydata):
        # All four historical slots use the same baseline fixture.
        return historical

    return fake_get, fake_post


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

# Config mirrors what code.py would build from boston_settings.toml.
_BOSTON_CONFIG = {
    "CIRCUITPY_WIFI_SSID":     "simulated",
    "CIRCUITPY_WIFI_PASSWORD": "",
    "USER_AGENT":              "weatherpanel-test (codeberg.org/mattdm/weatherpanel)",
    "GRIDPOINT_API":           "https://api.weather.gov/points",
    "GEOLOCATION_API":         "http://test/geo",
    "HISTORICAL_API":          "https://data.rcc-acis.org/GridData",
    "LATITUDE":                "42.3601",
    "LONGITUDE":               "-71.0589",
    "SWAP_GREEN_BLUE":         False,
    "TEMP_SCALE_RANGE":        110,
    "TEMP_MIDPOINT":           50,
    "CLOCK_TWENTYFOUR":        False,
    "CLOCK_DELIMITER":         ":",
}

# Frozen localtime: tm_sec=0 ensures both scheduler guards pass —
#   FORECAST_HEADROOM_S gate: 60 - 0 = 60 >= 50  → fetches proceed
#   SUCCESS_DISPLAY_S gate:   0 <= 56             → clock.wait() is called
_FAKE_LOCALTIME = time.struct_time((2026, 5, 11, 10, 30, 0, 6, 131, 1))


class _DisplayAdapter:
    """Wraps a SimDisplay so compare_or_save can call ._display.render_to_image."""

    def __init__(self, sim_disp):
        self._display = sim_disp


class TestSchedulerFullCycle:
    def test_boston_one_cycle(self, monkeypatch, request):
        """scheduler.run() completes one full cycle with Boston fixture data.

        Verifies that every stage — network check, geolocation, station
        discovery, historical baseline, hourly/griddata fetch, and display
        render — executes without error and produces a non-blank display.
        """
        import adafruit_bitmap_font.bitmap_font as _bmp_font
        import clock as _clock_mod
        import matrix as _matrix_mod
        import matrix_sim

        # --- Font redirect -----------------------------------------------
        _orig_load = _bmp_font.load_font
        monkeypatch.setattr(
            _bmp_font, "load_font",
            lambda path: _orig_load(str(_FONTS_DIR / Path(path).name)),
        )

        # --- Display capture: intercept matrix.display_set_root ----------
        _captured = {"sim_disp": None}
        _orig_dsr = _matrix_mod.display_set_root

        def _capturing_dsr(root_group, **kw):
            sim_disp = matrix_sim.display_set_root(root_group, **kw)
            _captured["sim_disp"] = sim_disp
            return sim_disp

        monkeypatch.setattr(_matrix_mod, "display_set_root", _capturing_dsr)

        # --- Network shims -----------------------------------------------
        fake_get, fake_post = _make_network_router("boston")

        monkeypatch.setattr(network, "get",     fake_get)
        monkeypatch.setattr(network, "post",    fake_post)
        monkeypatch.setattr(network, "check",   lambda: "simulated")
        monkeypatch.setattr(network, "connect", lambda cfg: None)
        monkeypatch.setattr(network, "ntp",     lambda: _FakeNTP())

        # --- Time shims --------------------------------------------------
        # Freeze localtime so both scheduler second-hand gates always pass.
        monkeypatch.setattr(scheduler, "localtime",  lambda: _FAKE_LOCALTIME)
        # Constant monotonic prevents PortalNeeded from ever triggering.
        monkeypatch.setattr(scheduler, "monotonic",  lambda: 0.0)

        # --- Loop exit: Clock.wait raises _FullCycleDone -----------------
        monkeypatch.setattr(_clock_mod.Clock, "wait",
                            lambda self: (_ for _ in ()).throw(_FullCycleDone()))

        # --- Run ---------------------------------------------------------
        with pytest.raises(_FullCycleDone):
            scheduler.run(_BOSTON_CONFIG)

        # --- Assertions --------------------------------------------------
        sim_disp = _captured["sim_disp"]
        assert sim_disp is not None, "matrix.display_set_root was never called"

        pixels = sim_disp.render_to_pixels()
        non_black = sum(1 for row in pixels for px in row if any(px))
        assert non_black > 100, (
            f"Display appears nearly blank ({non_black} lit pixels) — "
            "forecast was probably not rendered"
        )

        # --- Pixel reference comparison ----------------------------------
        compare_or_save(request, _DisplayAdapter(sim_disp),
                        "scheduler_full_cycle_boston")
