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
      → _refresh_forecasts     (network.get_stream → hourly, network.get → griddata)
      → display.update_forecast
      → clock.wait()           (raises _FullCycleDone to exit)

Also tests the PortalNeeded escalation state machine under sustained wi-fi
failure, including the reset that occurs when connectivity briefly recovers.
"""
import calendar as _calendar
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import network
import scheduler
from stream_helpers import make_hourly_stream
from render_helpers import compare_or_save

_SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"
_FONTS_DIR  = Path(__file__).parent.parent / "fonts"

# Fixed UTC timestamp: 2026-05-11T04:30:00 UTC = 2026-05-11T00:30:00 EDT.
# Placed 30 minutes BEFORE the first fixture period (which starts at 01:00 EDT)
# so all 65 hourly periods are always active regardless of when the test runs.
# This makes the reference PNG fully deterministic.
_FIXED_CLOCK_TS = float(_calendar.timegm((2026, 5, 11, 4, 30, 0, 0, 0, 0)))


# ---------------------------------------------------------------------------
# Sentinel exception — raised by the mocked Clock.wait() to exit the loop.
# ---------------------------------------------------------------------------

class _FullCycleDone(Exception):
    """Raised by the mocked Clock.wait() to break the scheduler loop."""


# ---------------------------------------------------------------------------
# NTP stub
# ---------------------------------------------------------------------------

class _FakeNTP:
    """Minimal NTP stub returning the same fixed time as the patched clock."""

    @property
    def datetime(self):
        return time.localtime(_FIXED_CLOCK_TS)


# ---------------------------------------------------------------------------
# Fixture-backed network router
# ---------------------------------------------------------------------------

def _load_fixture(name):
    with open(_SAMPLE_DIR / name) as f:
        return json.load(f)


def _make_network_router(location):
    """Return (fake_stream, fake_get, fake_post) backed by fixture files.

    The points fixture now includes forecastHourly and forecastGridData URLs.
    The router reads those real URLs from the fixture and routes them to the
    corresponding hourly/griddata fixture files.

    fake_stream: get_stream mock — handles the hourly URL via adafruit_json_stream
    fake_get:    get mock — handles griddata, stations, and points lookups
    fake_post:   post mock — handles all historical baseline requests
    """
    points_data   = _load_fixture(f"{location}_points.json")
    griddata_data = _load_fixture(f"{location}_griddata.json")
    stations_data = _load_fixture(f"{location}_stations.json")
    historical    = _load_fixture(f"{location}_historical.json")

    props        = points_data["properties"]
    griddata_url = props["forecastGridData"]
    stations_url = props["observationStations"]

    fake_stream = make_hourly_stream(f"{location}_hourly.json")

    def fake_get(url, headers=None):
        # Strip any query string for matching.
        base = url.split("?")[0]
        if base == griddata_url:
            return griddata_data
        if base == stations_url or base.startswith(stations_url):
            return stations_data
        # Gridpoint/points lookup — return the full points fixture.
        if "points" in url or "gridpoints" in url.lower():
            return points_data
        return None

    temp_range = _load_fixture(f"{location}_temp_range.json") if (
        (_SAMPLE_DIR / f"{location}_temp_range.json").exists()
    ) else None

    def fake_post(url, querydata):
        # 2-elem query → get_temp_range(); 4-elem query → get_historical_day().
        if temp_range is not None and len(querydata.get("elems", [])) == 2:
            return temp_range
        return historical

    return fake_stream, fake_get, fake_post


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

# Config mirrors what code.py would build from boston_settings.toml.
_BOSTON_CONFIG = {
    "CIRCUITPY_WIFI_SSID":     "simulated",
    "CIRCUITPY_WIFI_PASSWORD": "",
    "USER_AGENT":              "weatherpanel-test (codeberg.org/mattdm/weatherpanel)",
    "GRIDPOINT_API":           "https://api.weather.gov/points",
    "HISTORICAL_API":          "https://data.rcc-acis.org/GridData",
    "LATITUDE":                "42.3601",
    "LONGITUDE":               "-71.0589",
    "SWAP_GREEN_BLUE":         False,
    "AUTO_SCALE":              False,
    "TEMP_MIN":                -5,
    "TEMP_MAX":                105,
    "CLOCK_TWENTYFOUR":        False,
    "CLOCK_DELIMITER":         ":",
}

# Same as _BOSTON_CONFIG but with AUTO_SCALE enabled.  TEMP_MIN/TEMP_MAX are
# kept as fallback defaults; get_temp_range() will override them via
# set_temp_scale() once the ACIS fixture is returned by fake_post.
_BOSTON_AUTO_SCALE_CONFIG = {
    **_BOSTON_CONFIG,
    "AUTO_SCALE": True,
}

# Frozen localtime matching _FIXED_CLOCK_TS (2026-05-11T00:30:00 EDT).
# tm_sec=0 ensures both scheduler second-hand guards always pass:
#   FORECAST_HEADROOM_S gate: 60 - 0 = 60 >= 50  → fetches proceed
#   SUCCESS_DISPLAY_S gate:   0 <= 56             → clock.wait() is called
# tm_wday=0 (Monday), tm_yday=131, tm_isdst=1 (EDT)
_FAKE_LOCALTIME = time.struct_time((2026, 5, 11, 0, 30, 0, 0, 131, 1))


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
        fake_stream, fake_get, fake_post = _make_network_router("boston")

        monkeypatch.setattr(network, "get_stream", fake_stream)
        monkeypatch.setattr(network, "get",        fake_get)
        monkeypatch.setattr(network, "post",       fake_post)
        monkeypatch.setattr(network, "check",   lambda: "simulated")
        monkeypatch.setattr(network, "connect", lambda cfg: None)
        monkeypatch.setattr(network, "ntp",     lambda: _FakeNTP())

        # --- Time shims --------------------------------------------------
        # Freeze localtime so both scheduler second-hand gates always pass.
        monkeypatch.setattr(scheduler, "localtime",  lambda: _FAKE_LOCALTIME)
        # Constant monotonic prevents PortalNeeded from ever triggering.
        monkeypatch.setattr(scheduler, "monotonic",  lambda: 0.0)
        # Freeze clock.time.time so clock.isotime is always the fixed timestamp
        # regardless of when the test runs.  Without this, hourly periods expire
        # over the course of the day, changing the rendered output and breaking
        # the pixel reference comparison.
        monkeypatch.setattr(_clock_mod.time, "time", lambda: _FIXED_CLOCK_TS)

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


class TestAutoScaleFullCycle:
    def test_boston_auto_scale_one_cycle(self, monkeypatch, request):
        """scheduler.run() with AUTO_SCALE=True fetches the ACIS range and uses it.

        Verifies the full AUTO_SCALE pipeline end-to-end:
          _ensure_temp_range() → network.post() (2-elem query)
          → station.temp_min / station.temp_max set from fixture (-10 / 101)
          → display.set_temp_scale() called
          → display.temp_min / display.temp_max updated
          → hourly forecast rendered with the new scale

        The boston_temp_range.json fixture returns {"smry": [-10, 101]},
        so the scale shifts from the defaults (-5/105) to (-10/101).
        """
        import adafruit_bitmap_font.bitmap_font as _bmp_font
        import clock as _clock_mod
        import matrix as _matrix_mod
        import matrix_sim
        import display as _display_mod
        import station as _station_mod

        # --- Font redirect ---------------------------------------------------
        _orig_load = _bmp_font.load_font
        monkeypatch.setattr(
            _bmp_font, "load_font",
            lambda path: _orig_load(str(_FONTS_DIR / Path(path).name)),
        )

        # --- Display capture -------------------------------------------------
        _captured = {"sim_disp": None}
        _orig_dsr = _matrix_mod.display_set_root

        def _capturing_dsr(root_group, **kw):
            sim_disp = matrix_sim.display_set_root(root_group, **kw)
            _captured["sim_disp"] = sim_disp
            return sim_disp

        monkeypatch.setattr(_matrix_mod, "display_set_root", _capturing_dsr)

        # --- Station and Display tracking ------------------------------------
        # Wrap scheduler's references so we can inspect the live objects after run().
        _rt = {"station": None, "display": None}

        _OrigDisplay = _display_mod.Display
        _OrigStation = _station_mod.Station

        class _TrackDisplay(_OrigDisplay):
            def __init__(self, cfg):
                super().__init__(cfg)
                _rt["display"] = self

        class _TrackStation(_OrigStation):
            def __init__(self, cfg):
                super().__init__(cfg)
                _rt["station"] = self

        monkeypatch.setattr(scheduler, "Display", _TrackDisplay)
        monkeypatch.setattr(scheduler, "Station", _TrackStation)

        # --- Network shims ---------------------------------------------------
        fake_stream, fake_get, fake_post = _make_network_router("boston")

        monkeypatch.setattr(network, "get_stream", fake_stream)
        monkeypatch.setattr(network, "get",        fake_get)
        monkeypatch.setattr(network, "post",       fake_post)
        monkeypatch.setattr(network, "check",   lambda: "simulated")
        monkeypatch.setattr(network, "connect", lambda cfg: None)
        monkeypatch.setattr(network, "ntp",     lambda: _FakeNTP())

        # --- Time shims ------------------------------------------------------
        monkeypatch.setattr(scheduler, "localtime",  lambda: _FAKE_LOCALTIME)
        monkeypatch.setattr(scheduler, "monotonic",  lambda: 0.0)
        monkeypatch.setattr(_clock_mod.time, "time", lambda: _FIXED_CLOCK_TS)

        # station.localtime() is used by get_temp_range() for the edate.
        import station as _station_module
        monkeypatch.setattr(_station_module, "localtime", lambda: _FAKE_LOCALTIME)

        # --- Loop exit -------------------------------------------------------
        monkeypatch.setattr(_clock_mod.Clock, "wait",
                            lambda self: (_ for _ in ()).throw(_FullCycleDone()))

        # --- Run -------------------------------------------------------------
        with pytest.raises(_FullCycleDone):
            scheduler.run(_BOSTON_AUTO_SCALE_CONFIG)

        # --- Core assertions: scale was fetched and applied ------------------
        station = _rt["station"]
        display = _rt["display"]
        assert station is not None, "Station was never created"
        assert display is not None,  "Display was never created"

        # boston_temp_range.json fixture returns {"smry": [-10, 101]}
        assert station.temp_min == -10, (
            f"station.temp_min should be -10 from ACIS fixture, got {station.temp_min}"
        )
        assert station.temp_max == 101, (
            f"station.temp_max should be 101 from ACIS fixture, got {station.temp_max}"
        )
        assert display.temp_min == -10, (
            f"display.temp_min should be -10 after set_temp_scale(), got {display.temp_min}"
        )
        assert display.temp_max == 101, (
            f"display.temp_max should be 101 after set_temp_scale(), got {display.temp_max}"
        )

        # --- Forecast was rendered -------------------------------------------
        sim_disp = _captured["sim_disp"]
        assert sim_disp is not None, "matrix.display_set_root was never called"

        pixels = sim_disp.render_to_pixels()
        non_black = sum(1 for row in pixels for px in row if any(px))
        assert non_black > 100, (
            f"Display appears nearly blank ({non_black} lit pixels) — "
            "forecast was probably not rendered"
        )

        # --- Scale actually differs from default: auto renders != default ----
        # Re-render the same forecast with the default scale for comparison.
        _default_disp = _display_mod.Display({
            'TEMP_MIN': -5, 'TEMP_MAX': 105, 'SWAP_GREEN_BLUE': False,
        })
        _default_disp.update_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )
        pixels_default = _default_disp._display.render_to_pixels()
        assert pixels_default != pixels, (
            "AUTO_SCALE render should differ from default-scale render for Boston, "
            "but the pixel arrays are identical"
        )

        # --- Pixel reference comparison --------------------------------------
        compare_or_save(request, _DisplayAdapter(sim_disp),
                        "scheduler_full_cycle_boston_auto_scale")


# ---------------------------------------------------------------------------
# PortalNeeded escalation tests
# ---------------------------------------------------------------------------

# Minimal config: only the keys scheduler.run() reads before entering the loop.
_BROKEN_WIFI_CONFIG = {
    "CIRCUITPY_WIFI_SSID":     "TestNet",
    "CIRCUITPY_WIFI_PASSWORD": "",
    "AUTO_SCALE":              False,
    "TEMP_MIN":                0,
    "TEMP_MAX":                100,
}

# Frozen localtime: second=0 so the SUCCESS_DISPLAY_S guard never fires.
_FROZEN_LOCALTIME = time.struct_time((2026, 5, 11, 0, 30, 0, 0, 131, 1))


class TestSchedulerBrokenWifi:
    """Verify the PortalNeeded escalation state machine under broken wi-fi.

    Uses scheduler.run() directly with MagicMock display/clock/station so no
    fonts, hardware, or real network calls are needed.
    """

    def _monotonic_counter(self, step=5):
        """Return a callable that yields 0, step, 2*step, … on each call."""
        t = [0]
        def _mono():
            val = t[0]
            t[0] += step
            return val
        return _mono

    def _apply_base_patches(self, monkeypatch, check_fn):
        """Apply patches shared by all tests in this class."""
        monkeypatch.setattr(network, "check",   check_fn)
        monkeypatch.setattr(network, "connect", lambda cfg: None)
        monkeypatch.setattr(scheduler, "sleep",     lambda _: None)
        monkeypatch.setattr(scheduler, "localtime", lambda: _FROZEN_LOCALTIME)
        # Use lambdas so the config argument is consumed but a plain MagicMock
        # instance (not a dict-spec'd mock) is returned.
        monkeypatch.setattr(scheduler, "Display",   lambda cfg: MagicMock())
        monkeypatch.setattr(scheduler, "Clock",     lambda cfg: MagicMock())
        monkeypatch.setattr(scheduler, "Station",   lambda cfg: MagicMock())

    def test_portal_needed_raised_after_sustained_failure(self, monkeypatch):
        """scheduler.run() raises PortalNeeded after PORTAL_THRESHOLD_S of failure."""
        monkeypatch.setattr(scheduler, "monotonic", self._monotonic_counter())
        self._apply_base_patches(monkeypatch, lambda: None)

        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BROKEN_WIFI_CONFIG)

    def test_failure_timer_resets_after_recovery(self, monkeypatch):
        """A transient recovery resets the failure timer.

        check() returns: [None, None, "SimSSID", None, None, …].

        Monotonic increments by 5 per call.  With PORTAL_THRESHOLD_S=30:

        Without the reset:
          • _failure_start is set at call 2 (value 5)
          • By post-recovery iter 2, monotonic() - 5 crosses 30 → fires after
            ~5 check() calls total.

        With the reset:
          • _failure_start is cleared during the recovery iteration
          • The post-recovery timer restarts from a later value
          • PortalNeeded fires only after ~7 check() calls total.

        Asserting check_calls >= 6 distinguishes the two cases.
        """
        monkeypatch.setattr(scheduler, "monotonic", self._monotonic_counter())

        # When wifi comes back for one iteration the loop reaches
        # _ensure_location.  Patch it to return False immediately so we
        # continue without needing any NTP or forecast mocks.
        monkeypatch.setattr(scheduler, "_ensure_location", lambda *a: False)

        check_calls = [0]
        check_seq = iter([None, None, "SimSSID"] + [None] * 20)
        def _check():
            check_calls[0] += 1
            return next(check_seq)

        self._apply_base_patches(monkeypatch, _check)

        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BROKEN_WIFI_CONFIG)

        assert check_calls[0] >= 6, (
            f"PortalNeeded fired after only {check_calls[0]} check() call(s) — "
            "suggests _failure_start was not reset on recovery"
        )

    def test_retry_delay_skipped_on_first_failure(self, monkeypatch):
        """sleep(RETRY_DELAY_S) must not fire on the very first connect attempt.

        With N check() failures before PortalNeeded, sleep should be called
        exactly N-1 times — the first failure sets _failure_start and skips
        straight to continue with no sleep.
        """
        monkeypatch.setattr(scheduler, "monotonic", self._monotonic_counter())

        check_calls = [0]
        sleep_calls = [0]

        def _check():
            check_calls[0] += 1
            return None

        self._apply_base_patches(monkeypatch, _check)
        # Override sleep after _apply_base_patches so our counter wins.
        monkeypatch.setattr(scheduler, "sleep",
                            lambda t: sleep_calls.__setitem__(0, sleep_calls[0] + 1))

        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BROKEN_WIFI_CONFIG)

        # First failure: sets _failure_start, no sleep.
        # Middle failures: elapsed < threshold, sleep each time.
        # Last failure: elapsed >= threshold, PortalNeeded — no sleep.
        # → sleep is called (N - 2) times for N total check() failures.
        assert sleep_calls[0] == check_calls[0] - 2, (
            f"Expected {check_calls[0] - 2} sleep call(s) (first and last skipped), "
            f"got {sleep_calls[0]} — RETRY_DELAY_S may be firing on first attempt"
        )
