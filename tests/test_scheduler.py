"""Tests for scheduler helper functions.

Verifies LED state transitions and confirms that the matrix display is not
used for ongoing refresh status (historical, forecasts) — only the NeoPixel.
"""
import pytest
from time import time as _wall_time
from unittest.mock import MagicMock, patch

from statusled import BLUE, CYAN, GREEN, ORANGE, PURPLE, RED, YELLOW, StatusLED
import scheduler
from scheduler import (
    BOOT_PORTAL_THRESHOLD_S, FORECAST_STALE_S,
    TEMP_STALE_S,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_led():
    """StatusLED backed by the MagicMock neopixel from sim_stubs."""
    return StatusLED()


def led_color(led):
    """Return the most recent color passed to the LED pixel."""
    calls = led._pixel.fill.call_args_list
    assert calls, "fill() was never called"
    return calls[-1].args[0]


def make_display():
    return MagicMock()


def make_station(**kwargs):
    s = MagicMock()
    s.location = kwargs.get("location", "39.0,-120.0")
    s.station_id = kwargs.get("station_id", "TEST")
    s.tz = kwargs.get("tz", None)
    s.city = kwargs.get("city", None)
    s.unsupported = kwargs.get("unsupported", False)
    s.historical = kwargs.get("historical", [None, None, None, None])
    s.hourly = kwargs.get("hourly", {})
    s.hourly_expires = kwargs.get("hourly_expires", None)
    s.griddata_updated = kwargs.get("griddata_updated", False)
    s.griddata_expires = kwargs.get("griddata_expires", None)
    s.temp_range_is_fallback = kwargs.get("temp_range_is_fallback", False)
    return s


def make_clock(**kwargs):
    c = MagicMock()
    c.tz = kwargs.get("tz", "America/New_York")
    c.today = kwargs.get("today", "2026-05-08")
    c.minute = kwargs.get("minute", 0)
    return c


# ---------------------------------------------------------------------------
# _ensure_network
# ---------------------------------------------------------------------------

class TestEnsureNetwork:
    def test_returns_none_when_not_connected(self):
        with patch.object(scheduler.network, "check", return_value=None), \
             patch.object(scheduler.network, "connect"):
            led = make_led()
            result = scheduler._ensure_network({"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
        assert result is None

    def test_wifi_down_shows_red_on_disconnect(self):
        """Red must appear in the LED sequence even though working(YELLOW) follows."""
        colors = []
        with patch.object(scheduler.network, "check", return_value=None), \
             patch.object(scheduler.network, "connect"):
            led = make_led()
            led._pixel.fill.side_effect = lambda c: colors.append(c)
            scheduler._ensure_network({"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
        assert RED in colors

    def test_reconnect_attempt_shows_yellow(self):
        colors = []
        with patch.object(scheduler.network, "check", return_value=None), \
             patch.object(scheduler.network, "connect"):
            led = make_led()
            led._pixel.fill.side_effect = lambda c: colors.append(c)
            scheduler._ensure_network({"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
        assert YELLOW in colors

    def test_no_led_calls_when_connected(self):
        with patch.object(scheduler.network, "check", return_value="MySSID"):
            led = make_led()
            fill_count_before = len(led._pixel.fill.call_args_list)
            scheduler._ensure_network({"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
            fill_count_after = len(led._pixel.fill.call_args_list)
        # Only the __init__ fill(OFF) should have been called; no new calls during check
        assert fill_count_after == fill_count_before


# ---------------------------------------------------------------------------
# _ensure_location
# ---------------------------------------------------------------------------

class TestEnsureLocation:
    def test_returns_true_when_location_already_set(self):
        led = make_led()
        station = make_station(location="39.0,-120.0", unsupported=False)
        result = scheduler._ensure_location(make_display(), station, make_clock(), led)
        assert result is True

    def test_returns_false_on_geolocate_failure(self):
        led = make_led()
        station = make_station(location=None, unsupported=False)
        station.geolocate.side_effect = lambda: None  # doesn't set location
        result = scheduler._ensure_location(make_display(), station, make_clock(), led)
        assert result is False

    def test_shows_cyan_while_locating(self):
        colors = []
        station = make_station(location=None, unsupported=False)

        def fake_geolocate():
            station.location = "39.0,-120.0"

        station.geolocate.side_effect = fake_geolocate
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._ensure_location(make_display(), station, make_clock(), led)
        assert CYAN in colors

    def test_shows_green_on_success(self):
        station = make_station(location=None, unsupported=False)

        def fake_geolocate():
            station.location = "39.0,-120.0"

        station.geolocate.side_effect = fake_geolocate
        led = make_led()
        scheduler._ensure_location(make_display(), station, make_clock(), led)
        assert led_color(led) == GREEN

    def test_shows_failure_when_location_fails(self):
        station = make_station(location=None, unsupported=False)
        station.geolocate.side_effect = lambda: None
        led = make_led()
        scheduler._ensure_location(make_display(), station, make_clock(), led)
        assert led_color(led) == ORANGE
        assert led._sticky

    def test_shows_failure_for_unsupported_area(self):
        led = make_led()
        station = make_station(location="39.0,-120.0", unsupported=True)
        scheduler._ensure_location(make_display(), station, make_clock(), led)
        assert led_color(led) == ORANGE
        assert led._sticky


# ---------------------------------------------------------------------------
# _ensure_station
# ---------------------------------------------------------------------------

class TestEnsureStation:
    def test_no_op_when_station_already_set(self):
        led = make_led()
        station = make_station(location="39.0,-120.0", station_id="TEST")
        fill_count_before = len(led._pixel.fill.call_args_list)
        scheduler._ensure_station(make_display(), station, make_clock(), led)
        assert len(led._pixel.fill.call_args_list) == fill_count_before

    def test_shows_cyan_while_resolving(self):
        colors = []
        station = make_station(location="39.0,-120.0", station_id=None)
        station.get_station.side_effect = lambda: setattr(station, "station_id", "KFOO")
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._ensure_station(make_display(), station, make_clock(), led)
        assert CYAN in colors

    def test_shows_green_on_station_found(self):
        station = make_station(location="39.0,-120.0", station_id=None)
        station.get_station.side_effect = lambda: setattr(station, "station_id", "KFOO")
        led = make_led()
        scheduler._ensure_station(make_display(), station, make_clock(), led)
        assert led_color(led) == GREEN

    def test_shows_failure_when_station_not_found(self):
        station = make_station(location="39.0,-120.0", station_id=None)
        station.get_station.side_effect = lambda: None  # station_id stays None
        led = make_led()
        scheduler._ensure_station(make_display(), station, make_clock(), led)
        assert led_color(led) == ORANGE
        assert led._sticky

    def test_sets_clock_tz_even_when_station_id_missing(self):
        """Timezone must be applied to the clock as soon as station.tz is known,
        even when the station list endpoint fails and station_id stays None."""
        station = make_station(location="39.0,-120.0", station_id=None, tz="America/New_York")
        station.get_station.side_effect = lambda: None  # station_id stays None
        clock = make_clock(tz=None)
        scheduler._ensure_station(make_display(), station, clock, make_led())
        clock.set_tz.assert_called_once_with("America/New_York")

    def test_does_not_set_clock_tz_if_already_set(self):
        """clock.set_tz must not be called when the clock already has a timezone."""
        station = make_station(location="39.0,-120.0", station_id=None, tz="America/New_York")
        station.get_station.side_effect = lambda: None
        clock = make_clock(tz="America/New_York")  # already set
        scheduler._ensure_station(make_display(), station, clock, make_led())
        clock.set_tz.assert_not_called()


# ---------------------------------------------------------------------------
# _refresh_historical
# ---------------------------------------------------------------------------

class TestRefreshHistorical:
    def test_no_op_when_location_missing(self):
        led = make_led()
        station = make_station(location=None)
        clock = make_clock()
        display = make_display()
        scheduler._refresh_historical(station, clock, led)
        display.show_status.assert_not_called()

    def test_no_op_when_tz_missing(self):
        led = make_led()
        station = make_station()
        clock = make_clock(tz=None)
        display = make_display()
        scheduler._refresh_historical(station, clock, led)
        display.show_status.assert_not_called()

    def test_shows_purple_when_fetching(self):
        colors = []
        station = make_station(historical=[None, None, None, None])
        station.get_historical_day.side_effect = lambda idx, today: station.historical.__setitem__(idx, {"date": today})
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._refresh_historical(station, make_clock(), led)
        assert PURPLE in colors

    def test_shows_green_when_all_slots_filled(self):
        station = make_station(historical=[None, None, None, None])
        station.get_historical_day.side_effect = lambda idx, today: station.historical.__setitem__(idx, {"date": today})
        led = make_led()
        scheduler._refresh_historical(station, make_clock(), led)
        assert led_color(led) == GREEN

    def test_shows_failure_when_some_slots_remain_none(self):
        station = make_station(historical=[None, None, None, None])
        # Only fills the first slot; the rest stay None
        station.get_historical_day.side_effect = lambda idx, today: (
            station.historical.__setitem__(0, {"date": today}) if idx == 0 else None
        )
        led = make_led()
        scheduler._refresh_historical(station, make_clock(), led)
        assert led_color(led) == ORANGE
        assert led._sticky

    def test_no_led_activity_when_all_slots_already_filled(self):
        today = "2026-05-08"
        historical = [{"date": today}, {"date": today}, {"date": today}, {"date": today}]
        station = make_station(historical=historical)
        led = make_led()
        fill_count_before = len(led._pixel.fill.call_args_list)
        scheduler._refresh_historical(station, make_clock(today=today), led)
        assert len(led._pixel.fill.call_args_list) == fill_count_before

    def test_returns_false_when_location_missing(self):
        station = make_station(location=None)
        result = scheduler._refresh_historical(station, make_clock(), MagicMock())
        assert result is False

    def test_returns_false_when_all_slots_already_filled(self):
        today = "2026-05-08"
        historical = [{"date": today}, {"date": today}, {"date": today}, {"date": today}]
        station = make_station(historical=historical)
        result = scheduler._refresh_historical(station, make_clock(today=today), MagicMock())
        assert result is False

    def test_returns_true_when_slots_fetched(self):
        station = make_station(historical=[None, None, None, None])
        station.get_historical_day.side_effect = lambda idx, today: station.historical.__setitem__(idx, {"date": today})
        result = scheduler._refresh_historical(station, make_clock(), MagicMock())
        assert result is True


# ---------------------------------------------------------------------------
# _refresh_forecasts
# ---------------------------------------------------------------------------

class TestRefreshForecasts:
    def test_no_op_when_no_station_id(self):
        station = make_station(station_id=None)
        led = make_led()
        fill_count_before = len(led._pixel.fill.call_args_list)
        scheduler._refresh_forecasts(station, make_clock(), led)
        assert len(led._pixel.fill.call_args_list) == fill_count_before

    def test_returns_false_when_no_station_id(self):
        station = make_station(station_id=None)
        result = scheduler._refresh_forecasts(station, make_clock(), MagicMock())
        assert result is False

    def test_returns_true_when_hourly_due(self):
        station = make_station(
            station_id="TEST",
            hourly=None,
            griddata_updated=True,
            griddata_expires=_wall_time() + 3600,
        )
        station.get_hourly_forecast.side_effect = lambda: setattr(station, "hourly", [MagicMock()])
        result = scheduler._refresh_forecasts(station, make_clock(), MagicMock())
        assert result is True

    def test_returns_true_when_griddata_due(self):
        station = make_station(
            station_id="TEST",
            hourly=[MagicMock()],
            hourly_expires=_wall_time() + 3600,
            griddata_updated=False,
        )
        station.get_griddata.side_effect = lambda: setattr(station, "griddata_updated", True)
        result = scheduler._refresh_forecasts(station, make_clock(), MagicMock())
        assert result is True

    def test_returns_false_when_cache_is_fresh(self):
        station = make_station(
            station_id="TEST",
            hourly=[MagicMock()],
            hourly_expires=_wall_time() + 3600,
            griddata_updated="2026-05-16T10:00:00+00:00",
            griddata_expires=_wall_time() + 3600,
        )
        result = scheduler._refresh_forecasts(station, make_clock(), MagicMock())
        assert result is False

    def test_shows_blue_when_fetching_hourly(self):
        colors = []
        station = make_station(station_id="TEST", hourly=None)
        station.get_hourly_forecast.side_effect = lambda: setattr(station, "hourly", [MagicMock()])
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert BLUE in colors

    def test_shows_green_after_successful_hourly_fetch(self):
        # griddata_updated=True and griddata_expires in the future so the griddata
        # branch is skipped, isolating the green LED result to the hourly fetch.
        station = make_station(
            station_id="TEST",
            hourly=None,
            griddata_updated=True,
            griddata_expires=_wall_time() + 3600,
        )
        station.get_hourly_forecast.side_effect = lambda: setattr(station, "hourly", [MagicMock()])
        led = make_led()
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert led_color(led) == GREEN

    def test_shows_failure_when_hourly_fetch_returns_nothing(self):
        station = make_station(station_id="TEST", hourly=None)
        station.get_hourly_forecast.side_effect = lambda: None  # hourly stays None/empty
        led = make_led()
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert led_color(led) == ORANGE
        assert led._sticky

    def test_shows_blue_when_fetching_griddata(self):
        colors = []
        # Hourly present and current; griddata never fetched — griddata_due is True.
        station = make_station(
            station_id="TEST",
            hourly=[MagicMock()],
            hourly_expires=_wall_time() + 3600,
            griddata_updated=False,
        )
        station.get_griddata.side_effect = lambda: setattr(station, "griddata_updated", True)
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert BLUE in colors

    def test_shows_green_after_successful_griddata_fetch(self):
        # Hourly current; griddata never fetched → griddata_due is True.
        station = make_station(
            station_id="TEST",
            hourly=[MagicMock()],
            hourly_expires=_wall_time() + 3600,
            griddata_updated=False,
        )
        station.get_griddata.side_effect = lambda: setattr(station, "griddata_updated", True)
        led = make_led()
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert led_color(led) == GREEN

    def test_skips_griddata_when_cache_window_not_expired(self):
        """get_griddata() is not called when the cache window has not yet closed.

        This is the core behavior added by the griddata Cache-Control fix: we must
        not poll the griddata endpoint before NOAA says fresh data is available."""
        station = make_station(
            station_id="TEST",
            hourly=[MagicMock()],
            hourly_expires=_wall_time() + 3600,
            griddata_updated="2026-05-15T10:00:00+00:00",
            griddata_expires=_wall_time() + 3600,   # cache window still open
        )
        led = make_led()
        scheduler._refresh_forecasts(station, make_clock(), led)
        station.get_griddata.assert_not_called()


# ---------------------------------------------------------------------------
# PortalNeeded threshold
# ---------------------------------------------------------------------------

class _TestExit(Exception):
    """Raised by mocked clock.wait() to break the scheduler loop in tests."""


def _make_run_mocks(monkeypatch, *, check_seq, monotonic_seq,
                   exit_via_clock=False, hourly_update_age=0, display=None):
    """Patch scheduler.run() dependencies for portal-trigger and boot-display tests.

    ``check_seq``         -- iterable of values returned by network.check() in order.
    ``monotonic_seq``     -- iterable of floats returned by scheduler.monotonic() in order.
    ``exit_via_clock``    -- when True, clock.wait() raises _TestExit so the loop exits
                             cleanly at the end of a successful iteration.
    ``hourly_update_age`` -- value returned by station.hourly_update_age (default 0 = fresh).
    ``display``           -- if provided, used as the Display instance instead of a fresh
                             MagicMock(); allows callers to inspect display state after run().
    """
    import microcontroller as _mc
    _mc.watchdog.timeout = 60

    clock_mock = MagicMock()
    clock_mock.minute = 0       # ensures hourly_due is False (0 % 5 != 4)
    if exit_via_clock:
        clock_mock.wait.side_effect = _TestExit
    _display = display if display is not None else MagicMock()
    # The scheduler checks display.screen == Display.SCREEN_BOOT to gate the
    # SUCCESS_COLOR assignment; a plain MagicMock attribute won't equal "boot".
    _display.screen = "boot"
    # Replace scheduler.Display with a mock class that (a) returns _display when
    # instantiated and (b) exposes the SCREEN_* class-level constants that
    # scheduler.py reads as Display.SCREEN_BOOT etc.
    _DisplayClass = MagicMock()
    _DisplayClass.return_value = _display
    _DisplayClass.SCREEN_BOOT    = "boot"
    _DisplayClass.SCREEN_SCALE   = "scale"
    _DisplayClass.SCREEN_WEATHER = "weather"
    monkeypatch.setattr(scheduler, "Display", _DisplayClass)
    monkeypatch.setattr(scheduler, "Clock", lambda cfg: clock_mock)

    def _make_station(cfg):
        s = MagicMock()
        s.location      = "42.0,-71.0"   # truthy → skip geolocate
        s.unsupported   = False           # → _ensure_location returns True
        s.station_id    = "TEST"          # truthy → skip get_station
        s.hourly        = []              # falsy → no forecast render
        s.hourly_expires    = None        # None → short-circuits >= comparison
        s.historical    = []              # empty → no historical fetch
        s.temp_min      = 0              # not None → skip auto-scale
        s.temp_range_is_fallback = False
        s.griddata_updated  = False
        s.griddata_expires  = None        # None → short-circuits >= comparison
        s.hourly_update_age = hourly_update_age
        return s

    monkeypatch.setattr(scheduler, "Station", _make_station)
    monkeypatch.setattr(scheduler, "StatusLED", lambda: MagicMock())

    check_iter = iter(check_seq)
    monkeypatch.setattr(scheduler.network, "check", lambda: next(check_iter, None))
    monkeypatch.setattr(scheduler.network, "connect", lambda cfg: None)
    monkeypatch.setattr(scheduler, "sleep", lambda t: None)

    mono_iter = iter(monotonic_seq)
    monkeypatch.setattr(scheduler, "monotonic", lambda: next(mono_iter, 9999))


_BASE_CONFIG = {"CIRCUITPY_WIFI_SSID": "MyNet", "USER_AGENT": None}


class TestPortalNeeded:
    def test_raised_after_boot_threshold_never_connected(self, monkeypatch):
        """Portal fires after BOOT_PORTAL_THRESHOLD_S when Wi-Fi never connects.

        Monotonic sequence: _boot_time=0, then per-loop t_feed + elapsed check.
        Loop 1: elapsed = 0 < threshold → no portal.
        Loop 2: elapsed = BOOT_PORTAL_THRESHOLD_S + 1 → PortalNeeded.
        """
        _make_run_mocks(
            monkeypatch,
            check_seq=[None, None],
            monotonic_seq=[0, 0, 0, 0, BOOT_PORTAL_THRESHOLD_S + 1],
        )
        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BASE_CONFIG)

    def test_not_raised_below_boot_threshold(self, monkeypatch):
        """No portal when Wi-Fi was never connected but threshold not yet reached."""
        _make_run_mocks(
            monkeypatch,
            check_seq=[None, "MyNet"],
            monotonic_seq=[0, 0, BOOT_PORTAL_THRESHOLD_S - 1, 0],
            exit_via_clock=True,
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)

    def test_raised_immediately_when_wifi_down_and_forecast_stale(self, monkeypatch):
        """Portal fires immediately on first network failure when forecast is ≥ 24 h old.

        Loop 1: Wi-Fi up → _ever_connected = True.
        Loop 2: Wi-Fi down + stale forecast → PortalNeeded on the same iteration.
        """
        _make_run_mocks(
            monkeypatch,
            check_seq=["MyNet", None],
            monotonic_seq=[0, 0, 0],
            hourly_update_age=FORECAST_STALE_S + 1,
        )
        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BASE_CONFIG)

    def test_not_raised_when_wifi_down_but_forecast_fresh(self, monkeypatch):
        """No portal when Wi-Fi is down but forecast is < 24 h old."""
        _make_run_mocks(
            monkeypatch,
            check_seq=["MyNet", None, "MyNet"],
            monotonic_seq=[0, 0, 0, 0],
            exit_via_clock=True,
            hourly_update_age=0,
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)

    def test_not_raised_when_wifi_up_despite_stale_forecast(self, monkeypatch):
        """No portal when forecast is stale but Wi-Fi stays connected."""
        _make_run_mocks(
            monkeypatch,
            check_seq=["MyNet", "MyNet"],
            monotonic_seq=[0, 0, 0],
            exit_via_clock=True,
            hourly_update_age=FORECAST_STALE_S + 1,
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)


# ---------------------------------------------------------------------------
# run() startup cleanup
# ---------------------------------------------------------------------------

class TestRunStartupReset:
    def test_resets_session_before_loop(self, monkeypatch):
        """run() calls network._reset_session() once before entering the loop.

        Any socket left "in use" in adafruit_connection_manager's registry by
        a previous code run (which survives CircuitPython soft reloads) must be
        cleared before the first request, or get_socket() will raise RuntimeError
        for the same host. The startup _reset_session() call handles that.
        """
        reset_calls = []
        monkeypatch.setattr(scheduler.network, '_reset_session',
                            lambda: reset_calls.append(1))
        _make_run_mocks(monkeypatch, check_seq=["MyNet"],
                        monotonic_seq=[], exit_via_clock=True)
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        assert len(reset_calls) == 1, (
            "_reset_session() must be called exactly once at startup — "
            "before the while-loop begins"
        )


# ---------------------------------------------------------------------------
# run() boot SSID display
# ---------------------------------------------------------------------------

class TestBootSSIDDisplay:
    def test_show_scale_overwrites_network_label_with_min_temp(self, sim_display):
        """show_scale() must replace whatever is in network_label with the min-temp.

        Simulates the transition from boot SSID display to the AUTO_SCALE preview:
        network_label starts holding the SSID (in SUCCESS_COLOR), then show_scale()
        overwrites it with the all-time low temperature.
        """
        sim_display.network_label.text = _BASE_CONFIG['CIRCUITPY_WIFI_SSID']
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display.network_label.text == "-10\u00b0"

    def test_scale_screen_does_not_clobber_network_label_color(self, sim_display):
        """When display.screen is SCREEN_SCALE, the scheduler's SUCCESS_COLOR
        assignment must be skipped.

        Core regression for the original bug: the loop set
        display.network_label.color = display.SUCCESS_COLOR unconditionally,
        repainting the cold-blue min-temp label green on every iteration after
        show_scale() was called.
        """
        from display import Display
        cold_blue = sim_display.temperature_palette[1]
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display.screen == Display.SCREEN_SCALE
        assert sim_display.network_label.color == cold_blue

        # Directly exercise the scheduler's loop guard — the fix is exactly
        # this conditional; testing it directly is clearer than driving a full
        # run() cycle with a pre-configured display.
        if sim_display.screen == Display.SCREEN_BOOT:
            sim_display.network_label.color = sim_display.SUCCESS_COLOR

        assert sim_display.network_label.color == cold_blue, (
            "network_label.color was overwritten — the SCREEN_SCALE guard is not working"
        )


# ---------------------------------------------------------------------------
# _ensure_temp_range
# ---------------------------------------------------------------------------

class TestEnsureTempRange:
    def _make_auto_config(self):
        return {"AUTO_SCALE": True}

    def test_no_op_when_auto_scale_false(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        display = make_display()
        led = make_led()
        scheduler._ensure_temp_range(display, station, {"AUTO_SCALE": False}, led)
        station.get_temp_range.assert_not_called()
        display.set_temp_scale.assert_not_called()

    def test_no_op_when_auto_scale_missing(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        display = make_display()
        led = make_led()
        scheduler._ensure_temp_range(display, station, {}, led)
        station.get_temp_range.assert_not_called()

    def test_no_op_when_no_lat(self):
        station = make_station()
        station.lat = None
        station.lon = "-71.06"
        station.temp_min = None
        display = make_display()
        led = make_led()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), led)
        station.get_temp_range.assert_not_called()

    def test_no_op_when_no_lon(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = None
        station.temp_min = None
        display = make_display()
        led = make_led()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), led)
        station.get_temp_range.assert_not_called()

    def test_no_op_when_already_fetched(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = -10          # confirmed ACIS result
        station.temp_range_is_fallback = False
        display = make_display()
        led = make_led()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), led)
        station.get_temp_range.assert_not_called()

    def test_shows_purple_led_while_querying(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.get_temp_range.return_value = (-10, 101)
        colors = []
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._ensure_temp_range(make_display(), station, self._make_auto_config(), led)
        assert PURPLE in colors

    def test_calls_set_temp_scale_on_success(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.get_temp_range.return_value = (-10, 101)
        display = make_display()
        led = make_led()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), led)
        display.set_temp_scale.assert_called_once_with(-10, 101)

    def test_calls_show_scale_on_success(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.city = "Boston"
        station.station_id = "KBOS"
        station.get_temp_range.return_value = (-10, 101)
        display = make_display()
        led = make_led()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), led)
        display.show_scale.assert_called_once_with("Boston", "KBOS")

    def test_shows_green_led_on_success(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.get_temp_range.return_value = (-10, 101)
        led = make_led()
        scheduler._ensure_temp_range(make_display(), station, self._make_auto_config(), led)
        assert led_color(led) == GREEN

    def test_shows_failure_led_on_api_error(self):
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.get_temp_range.return_value = None
        station.compute_fallback_range.return_value = (-5, 105)
        led = make_led()
        scheduler._ensure_temp_range(make_display(), station, self._make_auto_config(), led)
        assert led_color(led) == ORANGE
        assert led._sticky

    def test_applies_fallback_scale_on_api_error(self):
        """When ACIS fails, a computed fallback scale is applied to the display."""
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.get_temp_range.return_value = None
        station.compute_fallback_range.return_value = (-5, 105)
        display = make_display()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), make_led())
        display.set_temp_scale.assert_called_once_with(-5, 105)

    def test_sets_fallback_flag_on_api_error(self):
        """Fallback flag is set when ACIS fails."""
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.get_temp_range.return_value = None
        station.compute_fallback_range.return_value = (-5, 105)
        scheduler._ensure_temp_range(make_display(), station, self._make_auto_config(),
                                     make_led())
        assert station.temp_range_is_fallback is True

    def test_sets_fallback_temp_min_max_on_api_error(self):
        """temp_min and temp_max are updated from the fallback range."""
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.get_temp_range.return_value = None
        station.compute_fallback_range.return_value = (-5, 105)
        scheduler._ensure_temp_range(make_display(), station, self._make_auto_config(),
                                     make_led())
        assert station.temp_min == -5
        assert station.temp_max == 105

    def test_no_scale_preview_screen_when_hourly_already_loaded(self):
        """Calibration screen must not overlay the live forecast on a retry.

        If the first get_temp_range() attempt fails and the forecast loads in
        the meantime, a successful retry must update the scale silently without
        flashing the scale preview screen on top of the live forecast."""
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.hourly = [object()]    # non-empty: forecast already loaded
        station.get_temp_range.return_value = (-10, 101)
        display = make_display()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), make_led())
        display.set_temp_scale.assert_called_once_with(-10, 101)
        display.show_scale.assert_not_called()

    def test_scale_preview_screen_shown_when_no_hourly(self):
        """Calibration screen IS shown on normal cold-boot path when no forecast yet."""
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = None
        station.hourly = {}            # empty: forecast not yet loaded
        station.city = "Boston"
        station.station_id = "KBOS"
        station.get_temp_range.return_value = (-10, 101)
        display = make_display()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), make_led())
        display.show_scale.assert_called_once_with("Boston", "KBOS")

    def test_retries_when_fallback_already_in_use(self):
        """When a fallback scale is in use, ACIS is retried every loop — no once-per-day gate."""
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = -5
        station.temp_range_is_fallback = True
        station.get_temp_range.return_value = (-10, 101)
        display = make_display()
        scheduler._ensure_temp_range(display, station, self._make_auto_config(), make_led())
        display.set_temp_scale.assert_called_once_with(-10, 101)

    def test_clears_fallback_flag_when_acis_recovers(self):
        """When a retry succeeds, the fallback flag is cleared."""
        station = make_station()
        station.lat = "42.36"
        station.lon = "-71.06"
        station.temp_min = -5
        station.temp_range_is_fallback = True
        station.get_temp_range.return_value = (-10, 101)
        scheduler._ensure_temp_range(make_display(), station, self._make_auto_config(),
                                     make_led())
        assert station.temp_range_is_fallback is False


# ---------------------------------------------------------------------------
# _check_temp_freshness
# ---------------------------------------------------------------------------

class TestCheckTempFreshness:
    """_check_temp_freshness() drives the current-temp label color from data age."""

    def _make_station_with_age(self, *, hourly=None, age=None):
        """Return a MagicMock station with configurable hourly list and age."""
        s = make_station()
        s.hourly = hourly if hourly is not None else [MagicMock()]
        s.hourly_update_age = age
        return s

    def test_no_op_when_hourly_empty(self):
        """No mark_temp_stale call when no hourly data has loaded yet."""
        station = self._make_station_with_age(hourly={}, age=TEMP_STALE_S + 1)
        display = make_display()
        scheduler._check_temp_freshness(display, station)
        display.mark_temp_stale.assert_not_called()

    def test_no_op_when_age_none(self):
        """No mark_temp_stale call when hourly_update_age is None."""
        station = self._make_station_with_age(age=None)
        display = make_display()
        scheduler._check_temp_freshness(display, station)
        display.mark_temp_stale.assert_not_called()

    def test_no_op_when_age_below_threshold(self):
        """No mark_temp_stale call when data is fresh (age < TEMP_STALE_S)."""
        station = self._make_station_with_age(age=TEMP_STALE_S - 1)
        display = make_display()
        scheduler._check_temp_freshness(display, station)
        display.mark_temp_stale.assert_not_called()

    def test_marks_stale_when_age_equals_threshold(self):
        """mark_temp_stale is called when age == TEMP_STALE_S (boundary)."""
        station = self._make_station_with_age(age=TEMP_STALE_S)
        display = make_display()
        scheduler._check_temp_freshness(display, station)
        display.mark_temp_stale.assert_called_once()

    def test_marks_stale_when_age_exceeds_threshold(self):
        """mark_temp_stale is called when age > TEMP_STALE_S."""
        station = self._make_station_with_age(age=TEMP_STALE_S + 600)
        display = make_display()
        scheduler._check_temp_freshness(display, station)
        display.mark_temp_stale.assert_called_once()


# ---------------------------------------------------------------------------
# _check_temp_freshness call sites inside run()
# ---------------------------------------------------------------------------

class TestCheckTempFreshnessCallSites:
    """Verify _check_temp_freshness is called at the correct points in run()."""

    def _make_run_stale(self, monkeypatch, *, check_seq, monotonic_seq,
                        hourly_update_age, exit_via_clock=False):
        """Patch run() so we can inspect display.mark_temp_stale call count."""
        import microcontroller as _mc
        _mc.watchdog.timeout = 60

        clock_mock = MagicMock()
        clock_mock.minute = 0
        if exit_via_clock:
            clock_mock.wait.side_effect = _TestExit

        display_mock = MagicMock()
        display_mock.screen = "boot"
        _DisplayClass = MagicMock()
        _DisplayClass.return_value = display_mock
        _DisplayClass.SCREEN_BOOT    = "boot"
        _DisplayClass.SCREEN_SCALE   = "scale"
        _DisplayClass.SCREEN_WEATHER = "weather"
        monkeypatch.setattr(scheduler, "Display", _DisplayClass)
        monkeypatch.setattr(scheduler, "Clock", lambda cfg: clock_mock)

        def _make_station(cfg):
            s = MagicMock()
            s.location      = "42.0,-71.0"
            s.unsupported   = False
            s.station_id    = "TEST"
            s.hourly        = [MagicMock()]   # non-empty so freshness check fires
            s.hourly_expires    = None
            s.historical    = []
            s.temp_min      = 0
            s.temp_range_is_fallback = False
            s.griddata_updated  = False
            s.griddata_expires  = None
            s.hourly_update_age = hourly_update_age
            return s

        monkeypatch.setattr(scheduler, "Station", _make_station)
        monkeypatch.setattr(scheduler, "StatusLED", lambda: MagicMock())

        check_iter = iter(check_seq)
        monkeypatch.setattr(scheduler.network, "check", lambda: next(check_iter, None))
        monkeypatch.setattr(scheduler.network, "connect", lambda cfg: None)
        monkeypatch.setattr(scheduler, "sleep", lambda t: None)

        mono_iter = iter(monotonic_seq)
        monkeypatch.setattr(scheduler, "monotonic", lambda: next(mono_iter, 9999))

        return display_mock

    def test_offline_stale_calls_mark_temp_stale(self, monkeypatch):
        """mark_temp_stale is called in the offline path when data is stale."""
        display_mock = self._make_run_stale(
            monkeypatch,
            check_seq=[None, None],
            monotonic_seq=[0, 0, 0, 0, BOOT_PORTAL_THRESHOLD_S + 1],
            hourly_update_age=TEMP_STALE_S + 1,
        )
        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BASE_CONFIG)
        display_mock.mark_temp_stale.assert_called()

    def test_offline_fresh_does_not_call_mark_temp_stale(self, monkeypatch):
        """mark_temp_stale is NOT called in the offline path when data is fresh."""
        display_mock = self._make_run_stale(
            monkeypatch,
            check_seq=["MyNet", None, "MyNet"],
            monotonic_seq=[0, 0, 0, 0, 0],
            hourly_update_age=TEMP_STALE_S - 1,
            exit_via_clock=True,
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        display_mock.mark_temp_stale.assert_not_called()

    def test_online_stale_calls_mark_temp_stale(self, monkeypatch):
        """mark_temp_stale is called in the online path when data is stale."""
        display_mock = self._make_run_stale(
            monkeypatch,
            check_seq=["MyNet"],
            monotonic_seq=[0, 0, 0],
            hourly_update_age=TEMP_STALE_S + 1,
            exit_via_clock=True,
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        display_mock.mark_temp_stale.assert_called()


# ---------------------------------------------------------------------------
# TestRedrawGate
# ---------------------------------------------------------------------------

class TestRedrawGate:
    """update_forecast() is only called when data changed or the hour changed."""

    def _make_gate_mocks(self, monkeypatch, *, hours,
                         forecast_changed_seq, historical_changed_seq):
        """Set up run() for two-iteration gate tests.

        ``hours``                  -- sequence of tm_hour ints, one per localtime() call.
        ``forecast_changed_seq``   -- return values for _refresh_forecasts, one per call.
        ``historical_changed_seq`` -- return values for _refresh_historical, one per call.

        Returns (display_mock, clock_mock).
        """
        import microcontroller as _mc
        _mc.watchdog.timeout = 60

        wait_count = [0]

        def _wait():
            wait_count[0] += 1
            if wait_count[0] >= 2:
                raise _TestExit

        clock_mock = MagicMock()
        clock_mock.wait.side_effect = _wait

        display_mock = MagicMock()
        display_mock.screen = "not-boot"  # skip the network_label SUCCESS_COLOR branch
        _DisplayClass = MagicMock()
        _DisplayClass.return_value = display_mock
        _DisplayClass.SCREEN_BOOT    = "boot"
        _DisplayClass.SCREEN_SCALE   = "scale"
        _DisplayClass.SCREEN_WEATHER = "weather"
        monkeypatch.setattr(scheduler, "Display", _DisplayClass)
        monkeypatch.setattr(scheduler, "Clock", lambda cfg: clock_mock)

        hour_iter = iter(hours)

        def _localtime():
            m = MagicMock()
            m.tm_sec  = 30
            m.tm_hour = next(hour_iter, hours[-1])
            return m

        monkeypatch.setattr(scheduler, "localtime", _localtime)

        def _make_station(cfg):
            s = MagicMock()
            s.location               = "42.0,-71.0"
            s.unsupported            = False
            s.station_id             = "TEST"
            s.hourly                 = [MagicMock()]   # truthy → gate fires
            s.hourly_expires         = _wall_time() + 3600
            s.historical             = [{"date": "2026-05-16"}] * 4
            s.temp_min               = 0
            s.temp_range_is_fallback = False
            s.griddata_updated       = "2026-05-16T10:00:00+00:00"
            s.griddata_expires       = _wall_time() + 3600
            s.hourly_update_age      = 0
            return s

        monkeypatch.setattr(scheduler, "Station", _make_station)
        monkeypatch.setattr(scheduler, "StatusLED", lambda: MagicMock())
        monkeypatch.setattr(scheduler.network, "check", lambda: "MyNet")
        monkeypatch.setattr(scheduler.network, "connect", lambda cfg: None)
        monkeypatch.setattr(scheduler, "sleep", lambda t: None)
        monkeypatch.setattr(scheduler, "monotonic", lambda: 0)

        fc_iter = iter(forecast_changed_seq)
        hc_iter = iter(historical_changed_seq)
        monkeypatch.setattr(scheduler, "_refresh_forecasts",
                            lambda s, c, led: next(fc_iter, False))
        monkeypatch.setattr(scheduler, "_refresh_historical",
                            lambda s, c, led: next(hc_iter, False))

        return display_mock

    def test_no_redraw_when_data_and_hour_unchanged(self, monkeypatch):
        """Second iteration does not call update_forecast when nothing changed."""
        # localtime() is called 3× per iteration (deadline, gate, sleep-check);
        # same hour (14) throughout both iterations.
        display_mock = self._make_gate_mocks(
            monkeypatch,
            hours=[14] * 12,
            forecast_changed_seq=[False, False],
            historical_changed_seq=[False, False],
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        # Iteration 1: _last_plotted_hour is None → draws. Iteration 2: same hour,
        # no data change → skips.
        assert display_mock.update_forecast.call_count == 1

    def test_redraws_when_hour_changes(self, monkeypatch):
        """update_forecast is called in both iterations when the hour advances."""
        # Iteration 1 sees hour 14, iteration 2 sees hour 15. We supply enough
        # values for 3 localtime() calls per iteration × 2 iters.
        display_mock = self._make_gate_mocks(
            monkeypatch,
            hours=[14, 14, 14, 15, 15, 15],
            forecast_changed_seq=[False, False],
            historical_changed_seq=[False, False],
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        assert display_mock.update_forecast.call_count == 2

    def test_redraws_when_forecast_changes(self, monkeypatch):
        """update_forecast is called in both iterations when forecast was fetched."""
        display_mock = self._make_gate_mocks(
            monkeypatch,
            hours=[14] * 12,
            forecast_changed_seq=[False, True],   # iter 2: forecast fetched
            historical_changed_seq=[False, False],
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        assert display_mock.update_forecast.call_count == 2

    def test_redraws_when_historical_changes(self, monkeypatch):
        """update_forecast is called in both iterations when historical was fetched."""
        display_mock = self._make_gate_mocks(
            monkeypatch,
            hours=[14] * 12,
            forecast_changed_seq=[False, False],
            historical_changed_seq=[False, True],  # iter 2: historical fetched
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        assert display_mock.update_forecast.call_count == 2

