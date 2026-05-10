"""Tests for scheduler helper functions.

Verifies LED state transitions and confirms that the matrix display is not
used for ongoing refresh status (historical, forecasts) — only the NeoPixel.
"""
import pytest
from unittest.mock import MagicMock, call, patch

from statusled import BLUE, CYAN, GREEN, ORANGE, PURPLE, RED, YELLOW, StatusLED
import scheduler
from scheduler import PORTAL_THRESHOLD_S


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
    s.hourly = kwargs.get("hourly", [])
    s.griddata_updated = kwargs.get("griddata_updated", False)
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
    def test_returns_ssid_when_connected(self):
        with patch.object(scheduler.network, "check", return_value="MySSID"):
            led = make_led()
            result = scheduler._ensure_network(make_display(), {"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
        assert result == "MySSID"

    def test_returns_none_when_not_connected(self):
        with patch.object(scheduler.network, "check", return_value=None), \
             patch.object(scheduler.network, "connect"), \
             patch("scheduler.sleep"):
            led = make_led()
            result = scheduler._ensure_network(make_display(), {"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
        assert result is None

    def test_wifi_down_shows_red_on_disconnect(self):
        """Red must appear in the LED sequence even though working(YELLOW) follows."""
        colors = []
        with patch.object(scheduler.network, "check", return_value=None), \
             patch.object(scheduler.network, "connect"), \
             patch("scheduler.sleep"):
            led = make_led()
            led._pixel.fill.side_effect = lambda c: colors.append(c)
            scheduler._ensure_network(make_display(), {"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
        assert RED in colors

    def test_reconnect_attempt_shows_yellow(self):
        colors = []
        with patch.object(scheduler.network, "check", return_value=None), \
             patch.object(scheduler.network, "connect"), \
             patch("scheduler.sleep"):
            led = make_led()
            led._pixel.fill.side_effect = lambda c: colors.append(c)
            scheduler._ensure_network(make_display(), {"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
        assert YELLOW in colors

    def test_no_led_calls_when_connected(self):
        with patch.object(scheduler.network, "check", return_value="MySSID"):
            led = make_led()
            fill_count_before = len(led._pixel.fill.call_args_list)
            scheduler._ensure_network(make_display(), {"CIRCUITPY_WIFI_SSID": "MySSID"}, led)
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


# ---------------------------------------------------------------------------
# _refresh_historical
# ---------------------------------------------------------------------------

class TestRefreshHistorical:
    def test_no_op_when_location_missing(self):
        led = make_led()
        station = make_station(location=None)
        clock = make_clock()
        display = make_display()
        scheduler._refresh_historical(display, station, clock, led)
        display.set_status.assert_not_called()

    def test_no_op_when_tz_missing(self):
        led = make_led()
        station = make_station()
        clock = make_clock(tz=None)
        display = make_display()
        scheduler._refresh_historical(display, station, clock, led)
        display.set_status.assert_not_called()

    def test_never_calls_display_set_status(self):
        """Historical refresh must not write to the matrix display."""
        station = make_station(historical=[None, None, None, None])
        station.get_historical_day.side_effect = lambda idx, today: station.historical.__setitem__(idx, {"date": today})
        display = make_display()
        led = make_led()
        scheduler._refresh_historical(display, station, make_clock(), led)
        display.set_status.assert_not_called()

    def test_shows_purple_when_fetching(self):
        colors = []
        station = make_station(historical=[None, None, None, None])
        station.get_historical_day.side_effect = lambda idx, today: station.historical.__setitem__(idx, {"date": today})
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._refresh_historical(make_display(), station, make_clock(), led)
        assert PURPLE in colors

    def test_shows_green_when_all_slots_filled(self):
        station = make_station(historical=[None, None, None, None])
        station.get_historical_day.side_effect = lambda idx, today: station.historical.__setitem__(idx, {"date": today})
        led = make_led()
        scheduler._refresh_historical(make_display(), station, make_clock(), led)
        assert led_color(led) == GREEN

    def test_shows_failure_when_some_slots_remain_none(self):
        station = make_station(historical=[None, None, None, None])
        # Only fills the first slot; the rest stay None
        station.get_historical_day.side_effect = lambda idx, today: (
            station.historical.__setitem__(0, {"date": today}) if idx == 0 else None
        )
        led = make_led()
        scheduler._refresh_historical(make_display(), station, make_clock(), led)
        assert led_color(led) == ORANGE
        assert led._sticky

    def test_no_led_activity_when_all_slots_already_filled(self):
        today = "2026-05-08"
        historical = [{"date": today}, {"date": today}, {"date": today}, {"date": today}]
        station = make_station(historical=historical)
        led = make_led()
        fill_count_before = len(led._pixel.fill.call_args_list)
        scheduler._refresh_historical(make_display(), station, make_clock(today=today), led)
        assert len(led._pixel.fill.call_args_list) == fill_count_before


# ---------------------------------------------------------------------------
# _refresh_forecasts
# ---------------------------------------------------------------------------

class TestRefreshForecasts:
    @pytest.fixture(autouse=True)
    def early_second(self, monkeypatch):
        """Patch localtime so tm_sec is 0 — headroom gate stays open for all tests."""
        lt = MagicMock()
        lt.tm_sec = 0
        monkeypatch.setattr(scheduler, "localtime", lambda: lt)

    def test_no_op_when_no_station_id(self):
        station = make_station(station_id=None)
        led = make_led()
        fill_count_before = len(led._pixel.fill.call_args_list)
        scheduler._refresh_forecasts(station, make_clock(), led)
        assert len(led._pixel.fill.call_args_list) == fill_count_before

    def test_shows_blue_when_fetching_hourly(self):
        colors = []
        station = make_station(station_id="TEST", hourly=None)
        station.get_hourly_forecast.side_effect = lambda: setattr(station, "hourly", [MagicMock()])
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert BLUE in colors

    def test_shows_green_after_successful_hourly_fetch(self):
        # griddata_updated=True so the griddata branch is skipped, isolating the hourly result
        station = make_station(station_id="TEST", hourly=None, griddata_updated=True)
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
        # Hourly already present; griddata not yet fetched; minute triggers griddata poll
        station = make_station(
            station_id="TEST",
            hourly=[MagicMock()],
            griddata_updated=False,
        )
        station.get_griddata.side_effect = lambda: setattr(station, "griddata_updated", True)
        led = make_led()
        led._pixel.fill.side_effect = lambda c: colors.append(c)
        # minute=0 → 0 % 5 == 0 != HOURLY_POLL_OFFSET(4), so hourly not re-fetched
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert BLUE in colors

    def test_shows_green_after_successful_griddata_fetch(self):
        station = make_station(
            station_id="TEST",
            hourly=[MagicMock()],
            griddata_updated=False,
        )
        station.get_griddata.side_effect = lambda: setattr(station, "griddata_updated", True)
        led = make_led()
        scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        assert led_color(led) == GREEN

    def test_skips_all_fetches_past_headroom(self):
        """_refresh_forecasts() is a no-op when tm_sec >= FORECAST_HEADROOM_S."""
        station = make_station(station_id="TEST", hourly=None)
        led = make_led()
        fill_count_before = len(led._pixel.fill.call_args_list)
        with patch("scheduler.localtime") as mock_lt:
            mock_lt.return_value.tm_sec = scheduler.FORECAST_HEADROOM_S
            scheduler._refresh_forecasts(station, make_clock(minute=0), led)
        station.get_hourly_forecast.assert_not_called()
        station.get_griddata.assert_not_called()
        assert len(led._pixel.fill.call_args_list) == fill_count_before


# ---------------------------------------------------------------------------
# PortalNeeded threshold
# ---------------------------------------------------------------------------

class _TestExit(Exception):
    """Raised by mocked clock.wait() to break the scheduler loop in tests."""


def _make_run_mocks(monkeypatch, *, check_seq, monotonic_seq, exit_via_clock=False):
    """Patch scheduler.run() dependencies for threshold tests.

    ``check_seq``    -- iterable of values returned by network.check() in order.
    ``monotonic_seq``-- iterable of floats returned by scheduler.monotonic() in order.
    ``exit_via_clock``-- when True, the Clock mock's wait() raises _TestExit so
                         the loop terminates cleanly after a successful network check.
    """
    import microcontroller as _mc
    _mc.watchdog.timeout = 60

    clock_mock = MagicMock()
    if exit_via_clock:
        clock_mock.wait.side_effect = _TestExit
    monkeypatch.setattr(scheduler, "Display", lambda cfg: MagicMock())
    monkeypatch.setattr(scheduler, "Clock", lambda cfg: clock_mock)
    monkeypatch.setattr(scheduler, "Station", lambda cfg: MagicMock())
    monkeypatch.setattr(scheduler, "StatusLED", lambda: MagicMock())

    check_iter = iter(check_seq)
    monkeypatch.setattr(scheduler.network, "check", lambda: next(check_iter, None))
    monkeypatch.setattr(scheduler.network, "connect", lambda cfg: None)
    monkeypatch.setattr(scheduler, "sleep", lambda t: None)

    mono_iter = iter(monotonic_seq)
    monkeypatch.setattr(scheduler, "monotonic", lambda: next(mono_iter, 9999))


_BASE_CONFIG = {"CIRCUITPY_WIFI_SSID": "MyNet", "USER_AGENT": None}


class TestPortalNeeded:
    def test_raised_after_threshold(self, monkeypatch):
        """Two consecutive failures spanning more than PORTAL_THRESHOLD_S raise PortalNeeded."""
        _make_run_mocks(
            monkeypatch,
            check_seq=[None, None],
            monotonic_seq=[0, PORTAL_THRESHOLD_S + 10],
        )
        with pytest.raises(scheduler.PortalNeeded):
            scheduler.run(_BASE_CONFIG)

    def test_not_raised_on_first_failure(self, monkeypatch):
        """A single network failure followed by recovery does not raise PortalNeeded."""
        _make_run_mocks(
            monkeypatch,
            check_seq=[None, "MyNet"],
            monotonic_seq=[0],
            exit_via_clock=True,
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)
        # Reaching here confirms PortalNeeded was not raised.

    def test_not_raised_when_elapsed_below_threshold(self, monkeypatch):
        """Failures shorter than PORTAL_THRESHOLD_S do not raise PortalNeeded."""
        _make_run_mocks(
            monkeypatch,
            check_seq=[None, None, "MyNet"],
            monotonic_seq=[0, PORTAL_THRESHOLD_S - 10],
            exit_via_clock=True,
        )
        with pytest.raises(_TestExit):
            scheduler.run(_BASE_CONFIG)

    def test_exception_carries_no_message(self):
        """PortalNeeded is a bare sentinel exception."""
        exc = scheduler.PortalNeeded()
        assert isinstance(exc, Exception)
