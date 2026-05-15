"""Full-cycle scheduler integration test.

Drives scheduler.run() end-to-end for one complete cycle using fixture-backed
network calls (no live HTTP), exits cleanly after the first display render,
and compares the output against a committed reference PNG.

This covers the path that unit tests miss:
    scheduler.run()
      → _ensure_network        (mocked network.check)
      → _ensure_location       (fixed lat/lon bypasses geolocation)
      → _ensure_station        (network.request GET → fixture points + stations)
      → clock.sync_network_time (fake NTP)
      → _refresh_historical    (4 × network.request POST → fixture)
      → _refresh_forecasts     (network.get_stream → hourly, network.request GET → griddata)
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
    """Return (fake_stream, fake_request) backed by fixture files.

    The points fixture now includes forecastHourly and forecastGridData URLs.
    The router reads those real URLs from the fixture and routes them to the
    corresponding hourly/griddata fixture files.

    fake_stream:   get_stream mock — handles the hourly URL via adafruit_json_stream
    fake_request:  request mock — handles GET (griddata, stations, points) and
                   POST (historical baseline and temp-range queries)
    """
    points_data   = _load_fixture(f"{location}_points.json")
    griddata_data = _load_fixture(f"{location}_griddata.json")
    stations_data = _load_fixture(f"{location}_stations.json")
    historical    = _load_fixture(f"{location}_historical.json")

    props        = points_data["properties"]
    griddata_url = props["forecastGridData"]
    stations_url = props["observationStations"]

    fake_stream = make_hourly_stream(f"{location}_hourly.json")

    temp_range = _load_fixture(f"{location}_temp_range.json") if (
        (_SAMPLE_DIR / f"{location}_temp_range.json").exists()
    ) else None

    def fake_request(verb, url, body=None, headers=None):
        if verb == "POST":
            # 2-elem query → get_temp_range(); 4-elem query → get_historical_day().
            if temp_range is not None and len(body.get("elems", [])) == 2:
                return temp_range
            return historical
        # GET routing — strip query string for matching.
        base = url.split("?")[0]
        if base == griddata_url:
            return griddata_data
        if base == stations_url or base.startswith(stations_url):
            return stations_data
        # Gridpoint/points lookup — return the full points fixture.
        if "points" in url or "gridpoints" in url.lower():
            return points_data
        return None

    return fake_stream, fake_request


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
# set_temp_scale() once the ACIS fixture is returned by fake_request.
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


class TestSchedulerFullCycle:
    def test_boston_one_cycle(self, monkeypatch, request):
        """scheduler.run() completes one full cycle with Boston fixture data.

        Verifies that every stage — network check, geolocation, station
        discovery, historical baseline, hourly/griddata fetch, and display
        render — executes without error and produces a non-blank display.
        """
        import clock as _clock_mod
        import matrix as _matrix_mod
        import matrix_sim
        import station as _station_mod

        # --- Display capture: intercept matrix.display_set_root ----------
        _captured = {"sim_disp": None}
        _orig_dsr = _matrix_mod.display_set_root

        def _capturing_dsr(root_group, **kw):
            sim_disp = matrix_sim.display_set_root(root_group, **kw)
            _captured["sim_disp"] = sim_disp
            return sim_disp

        monkeypatch.setattr(_matrix_mod, "display_set_root", _capturing_dsr)

        # --- Network shims -----------------------------------------------
        fake_stream, fake_request = _make_network_router("boston")

        monkeypatch.setattr(network, "get_stream", fake_stream)
        monkeypatch.setattr(network, "request",    fake_request)
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
        # Freeze station._time (time.time) so hourly_update_age is computed
        # relative to the fixture timestamp rather than real wall time.
        # Without this, the 3-day-old Boston fixture looks stale and the
        # current-temp label turns purple, breaking the pixel reference.
        monkeypatch.setattr(_station_mod, "_time", lambda: _FIXED_CLOCK_TS)
        # Suppress SUCCESS_DISPLAY_S sleep — _FAKE_LOCALTIME has tm_sec=0, so
        # the condition (tm_sec <= 56) is always True without this patch.
        monkeypatch.setattr(scheduler, "sleep", lambda _: None)

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
        compare_or_save(request, sim_disp.render_to_image(scale=8),
                        "scheduler_full_cycle_boston")


class TestAutoScaleFullCycle:
    def test_boston_auto_scale_one_cycle(self, monkeypatch, request):
        """scheduler.run() with AUTO_SCALE=True fetches the ACIS range and uses it.

        Verifies the full AUTO_SCALE pipeline end-to-end:
          _ensure_temp_range() → network.request POST (2-elem query)
          → station.temp_min / station.temp_max set from fixture (-10 / 101)
          → display.set_temp_scale() called
          → display.temp_min / display.temp_max updated
          → hourly forecast rendered with the new scale

        The boston_temp_range.json fixture returns {"smry": [-10, 101]},
        so the scale shifts from the defaults (-5/105) to (-10/101).
        """
        import clock as _clock_mod
        import matrix as _matrix_mod
        import matrix_sim
        import display as _display_mod
        import station as _station_mod

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
        _rt = {"station": None, "display": None, "scale_args": None, "scale_image": None}

        _OrigDisplay = _display_mod.Display
        _OrigStation = _station_mod.Station

        class _TrackDisplay(_OrigDisplay):
            def __init__(self, cfg):
                super().__init__(cfg)
                _rt["display"] = self

            def show_scale(self, city, station_id):
                super().show_scale(city, station_id)
                # Capture the display image immediately after the refresh() inside
                # show_scale(), before _refresh_forecasts() overwrites it.
                _rt["scale_args"]  = (city, station_id)
                _rt["scale_image"] = _captured["sim_disp"].render_to_image(scale=8)

        class _TrackStation(_OrigStation):
            def __init__(self, cfg):
                super().__init__(cfg)
                _rt["station"] = self

        monkeypatch.setattr(scheduler, "Display", _TrackDisplay)
        monkeypatch.setattr(scheduler, "Station", _TrackStation)

        # --- Network shims ---------------------------------------------------
        fake_stream, fake_request = _make_network_router("boston")

        monkeypatch.setattr(network, "get_stream", fake_stream)
        monkeypatch.setattr(network, "request",    fake_request)
        monkeypatch.setattr(network, "check",   lambda: "simulated")
        monkeypatch.setattr(network, "connect", lambda cfg: None)
        monkeypatch.setattr(network, "ntp",     lambda: _FakeNTP())

        # --- Time shims ------------------------------------------------------
        monkeypatch.setattr(scheduler, "localtime",  lambda: _FAKE_LOCALTIME)
        monkeypatch.setattr(scheduler, "monotonic",  lambda: 0.0)
        monkeypatch.setattr(_clock_mod.time, "time", lambda: _FIXED_CLOCK_TS)
        # Freeze station._time (time.time) so hourly_update_age is computed
        # relative to the fixture timestamp rather than real wall time.
        monkeypatch.setattr(_station_mod, "_time", lambda: _FIXED_CLOCK_TS)
        # Suppress SUCCESS_DISPLAY_S sleep — _FAKE_LOCALTIME has tm_sec=0, so
        # the condition (tm_sec <= 56) is always True without this patch.
        monkeypatch.setattr(scheduler, "sleep", lambda _: None)

        # station.localtime() is used by get_temp_range() for the edate.
        monkeypatch.setattr(_station_mod, "localtime", lambda: _FAKE_LOCALTIME)

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

        # --- Pixel reference comparison (final forecast) ---------------------
        compare_or_save(request, sim_disp.render_to_image(scale=8),
                        "scheduler_full_cycle_boston_auto_scale")

        # --- Scale preview was shown -----------------------------------------
        # show_scale() must be called during the boot cycle, before the first
        # forecast loads.  _TrackDisplay captures both the call args and a pixel
        # snapshot of the display state at the moment show_scale() refreshed.
        assert _rt["scale_args"] is not None, (
            "show_scale() was never called during AUTO_SCALE boot — "
            "the scale preview screen was skipped"
        )
        scale_city, scale_station_id = _rt["scale_args"]
        assert scale_station_id == "KBOS", (
            f"show_scale() called with wrong station_id: {scale_station_id!r}"
        )

        # Pixel reference comparison for the scale preview state.  This covers
        # the scheduler-driven path (labels populated from real station metadata)
        # separately from the direct-call tests in test_auto_scale_render.py.
        compare_or_save(
            request,
            _rt["scale_image"],
            "scheduler_auto_scale_preview_boston",
        )


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


class _TooManyIterations(Exception):
    """Safety valve raised by a check() stub when the loop spins more than expected.

    Used in 'no portal' tests to confirm PortalNeeded was never raised.
    """


class TestPortalNeeded:
    """Verify the two PortalNeeded trigger conditions under broken or flaky wi-fi.

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

    def _apply_patches(self, monkeypatch, check_fn, hourly_update_age=0):
        """Apply patches shared by all tests in this class.

        ``hourly_update_age`` sets station.hourly_update_age on the mock Station
        so each test can control whether the forecast is considered fresh or stale.
        """
        monkeypatch.setattr(network, "check",   check_fn)
        monkeypatch.setattr(network, "connect", lambda cfg: None)
        monkeypatch.setattr(scheduler, "sleep",     lambda _: None)
        monkeypatch.setattr(scheduler, "localtime", lambda: _FROZEN_LOCALTIME)
        def _make_display(cfg):
            m = MagicMock()
            m.screen = "boot"
            return m
        _DisplayClass = MagicMock()
        _DisplayClass.side_effect = _make_display
        _DisplayClass.SCREEN_BOOT    = "boot"
        _DisplayClass.SCREEN_SCALE   = "scale"
        _DisplayClass.SCREEN_WEATHER = "weather"
        monkeypatch.setattr(scheduler, "Display", _DisplayClass)
        monkeypatch.setattr(scheduler, "Clock",     lambda cfg: MagicMock())

        def _make_station(cfg):
            s = MagicMock()
            s.location            = "42.0,-71.0"
            s.unsupported         = False
            s.station_id          = "TEST"
            s.hourly              = []
            s.historical          = []
            s.temp_min            = 0
            s.temp_range_is_fallback = False
            s.hourly_update_age   = hourly_update_age
            return s

        monkeypatch.setattr(scheduler, "Station", _make_station)

    def test_portal_fires_at_boot_threshold_never_connected(self, monkeypatch):
        """PortalNeeded is raised after BOOT_PORTAL_THRESHOLD_S when Wi-Fi never connects."""
        monkeypatch.setattr(scheduler, "monotonic", self._monotonic_counter())
        self._apply_patches(monkeypatch, lambda: None)

        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BROKEN_WIFI_CONFIG)

    def test_portal_fires_immediately_when_wifi_down_and_forecast_stale(self, monkeypatch):
        """PortalNeeded fires on the very first failure loop after a stale forecast.

        Loop 1: Wi-Fi up → _ever_connected becomes True.
        Loop 2: Wi-Fi down + hourly_update_age ≥ FORECAST_STALE_S → portal fires
                on that same iteration, with no additional wait.
        """
        monkeypatch.setattr(scheduler, "monotonic", self._monotonic_counter())

        check_seq = iter(["SimSSID", None] + [None] * 20)
        self._apply_patches(
            monkeypatch,
            lambda: next(check_seq, None),
            hourly_update_age=scheduler.FORECAST_STALE_S + 1,
        )
        # Short-circuit location/station setup so the connected iteration
        # completes without needing NTP, historical, or forecast mocks.
        monkeypatch.setattr(scheduler, "_ensure_location", lambda *a: False)

        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BROKEN_WIFI_CONFIG)

    def test_no_portal_when_wifi_down_transiently_with_fresh_forecast(self, monkeypatch):
        """No PortalNeeded when Wi-Fi dips but forecast is under 24 h old.

        The stale+offline trigger requires hourly_update_age ≥ FORECAST_STALE_S.
        A fresh forecast (age=0) must never trigger the portal, even with a
        transient disconnection.
        """
        monkeypatch.setattr(scheduler, "monotonic", self._monotonic_counter())

        call_n = [0]
        pattern = ["SimSSID", None, "SimSSID"]

        def _check():
            call_n[0] += 1
            if call_n[0] > 15:
                raise _TooManyIterations()
            return pattern[min(call_n[0] - 1, len(pattern) - 1)]

        self._apply_patches(monkeypatch, _check, hourly_update_age=0)
        monkeypatch.setattr(scheduler, "_ensure_location", lambda *a: False)

        with pytest.raises(_TooManyIterations):
            scheduler.run(_BROKEN_WIFI_CONFIG)

    def test_no_portal_when_wifi_up_regardless_of_forecast_age(self, monkeypatch):
        """No PortalNeeded when Wi-Fi stays connected, even with a stale forecast.

        The portal trigger requires Wi-Fi to be down; a stale forecast alone
        is not sufficient.
        """
        monkeypatch.setattr(scheduler, "monotonic", self._monotonic_counter())

        call_n = [0]

        def _check():
            call_n[0] += 1
            if call_n[0] > 10:
                raise _TooManyIterations()
            return "SimSSID"

        self._apply_patches(
            monkeypatch,
            _check,
            hourly_update_age=scheduler.FORECAST_STALE_S + 1,
        )
        monkeypatch.setattr(scheduler, "_ensure_location", lambda *a: False)

        with pytest.raises(_TooManyIterations):
            scheduler.run(_BROKEN_WIFI_CONFIG)
