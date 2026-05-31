"""Main scheduler loop orchestrating network, weather data, clock, and display.

Coordinates periodic updates of weather forecasts, historical baselines, and
time synchronization while managing the display and watchdog timer.
"""
import gc
import microcontroller
from watchdog import WatchDogMode, WatchDogTimeout
from time import localtime, sleep, monotonic, time as _wall_time

from appconfig import DEFAULTS
from clock import Clock
from display import Display
from station import Station
from statusled import BLUE, CYAN, PURPLE, YELLOW, StatusLED
import network

# 61 s = one clock minute plus 1 s of jitter margin. The loop body plus
# clock.wait() together consume approximately 60 s by construction, but
# sleep() inaccuracy and function-call overhead can push the actual total
# just past 60 s. The extra second absorbs that without letting a genuinely
# stalled loop run for more than one full iteration undetected.
WATCHDOG_TIMEOUT_SECONDS = 61
RETRY_DELAY_SECONDS = 5
SUCCESS_DISPLAY_SECONDS = 3
NETWORK_DEADLINE_MARGIN_SECONDS = 5   # breathing room for display update and clock.wait() setup
BOOT_PORTAL_THRESHOLD_MINUTES = 1    # 1 min: portal if Wi-Fi never connects at boot
FORECAST_STALE_MINUTES = 1440        # 24 h: NOAA forecast too old to be meteorologically useful
TEMP_STALE_MINUTES = 720            # 12 h: current-temp label turns purple if NOAA model is this old.
                                # NOAA's model cadence is ~6 h, so 12 h means at least two
                                # consecutive update cycles were missed — genuinely exceptional.


class PortalNeeded(Exception):
    """Raised by scheduler.run() when Wi-Fi is unavailable and cannot be recovered.

    Two conditions trigger this:
    - Wi-Fi has never connected this session and BOOT_PORTAL_THRESHOLD_MINUTES * 60 has elapsed.
    - Wi-Fi is currently down AND the NOAA forecast is at least FORECAST_STALE_MINUTES * 60 old.

    Caught by code.py, which then imports and runs the configuration portal.
    """


def _collect_garbage():
    """Force garbage collection and report memory status."""
    mem_before = gc.mem_free()
    gc.collect()
    print(f"Memory: {network.fmt_bytes(mem_before)} → {network.fmt_bytes(gc.mem_free())} free")


def _ensure_network(config, led):
    """Check Wi-Fi and reconnect if needed. Returns SSID string if connected, else None.

    Note: this only reads wifi.radio.connected, so broken DNS or a dead ISP still
    returns the SSID. Those failures surface later as transport errors in
    network.request() / network.get_stream().
    """
    ssid = network.check()
    if not ssid:
        led.wifi_down()
        led.working(YELLOW)
        network.connect(config)
        return None
    return ssid


def _ensure_location(display, station, clock, led):
    """Geolocate if needed and check bounds. Returns True if ready to proceed."""
    if not station.location:
        led.working(CYAN)
        display.set_location("Locating...", display.QUERY_COLOR)
        display.show_status()
        station.geolocate()
        if station.location:
            led.success()
            display.set_location(station.location, display.SUCCESS_COLOR)
            station.check_bounds()
            if station.tz:
                clock.set_tz(station.tz)
        else:
            led.failure()
            display.set_location("Location?", display.FAILURE_COLOR)
            return False

    if station.unsupported:
        led.failure()
        display.set_location("Area not", display.FAILURE_COLOR)
        display.station_label.text   = "supported"
        display.station_label.color  = display.FAILURE_COLOR
        display.show_status()
        clock.wait()
        return False

    return True


def _ensure_station(display, station, clock, led):
    """Resolve NOAA station metadata if needed."""
    if station.location and not station.station_id:
        led.working(CYAN)
        display.station_label.text  = "Station?"
        display.station_label.color = display.QUERY_COLOR
        display.show_status()
        display.flush()  # show "Station?" before the network call
        station.get_station()
        if station.tz and not clock.tz:
            clock.set_tz(station.tz)
            display.update_clock(clock)
        if station.station_id:
            led.success()
            display.station_label.text  = station.station_id
            display.station_label.color = display.SUCCESS_COLOR
            if station.city:
                display.set_location(station.city, display.SUCCESS_COLOR)
            # Flush before show_scale() takes over the status group.
            display.flush()
        else:
            led.failure()
            display.station_label.text  = "Station?"
            display.station_label.color = display.FAILURE_COLOR


def _ensure_temp_range(display, station, config, led):
    """Query ACIS for all-time temperature range when AUTO_SCALE is enabled.

    Skips if AUTO_SCALE is False or location is not yet set.

    If a confirmed ACIS result is already stored (``temp_range_is_fallback``
    is False), the function is idempotent — no further queries are made.

    If a computed fallback scale is in use (``temp_range_is_fallback`` is
    True), the call retries every loop iteration until it gets a real ACIS
    result. Budget-constrained skips are handled transparently by
    ``network.request()`` (via ``min_budget_s`` at the call site in station),
    so a tight budget in one iteration does not block the next.

    On success, updates the display scale and clears the fallback flag.  On
    ACIS failure, calls ``station.compute_fallback_range()`` to derive a
    hard-default scale and sets the fallback flag. The scale preview screen is
    shown only when no hourly forecast has loaded yet — if the forecast is
    already on-screen, overlaying the scale preview would be disruptive."""
    if not config.get('AUTO_SCALE'):
        return
    if not station.lat or not station.lon:
        return

    # Already have a confirmed ACIS result — nothing to do.
    if station.temp_min is not None and not station.temp_range_is_fallback:
        return

    led.working(PURPLE)
    result = station.get_temp_range()
    if result:
        temp_min, temp_max = result
        station.temp_range_is_fallback = False
        display.set_temp_scale(temp_min, temp_max)
        if not station.hourly_model_updated:
            display.show_scale(station.city, station.station_id)
        led.success()
    else:
        temp_min, temp_max = station.compute_fallback_range()
        station.temp_min = temp_min
        station.temp_max = temp_max
        station.temp_range_is_fallback = True
        display.set_temp_scale(temp_min, temp_max)
        print(f"Fallback scale: {temp_min}°F – {temp_max}°F")
        led.failure()


def _refresh_historical(station, clock, led):
    """Fill empty slots in the historical circular buffer.

    Rotates the buffer when the date has changed (midnight), then fetches
    all missing slots in one call. Each fetch is ~250ms so filling all
    four at cold boot takes under a second — well within the watchdog
    budget. On failure a slot stays None and will be retried next
    iteration.

    Returns True if any fetch was attempted, False otherwise. The caller
    uses this to decide whether to redraw the display.
    """
    if not station.location or not clock.tz or not clock.today:
        return False

    station.rotate_historical(clock.today)

    fetched_any = False
    for slot_index, slot in enumerate(station.historical):
        if slot is not None:
            continue
        if not fetched_any:
            led.working(PURPLE)
            fetched_any = True
        station.get_historical_day(slot_index, clock.today)

    if fetched_any:
        if any(s is None for s in station.historical):
            led.failure()
        else:
            led.success()

    return fetched_any


def _fmt_ttl(expires):
    """Format time until a Cache-Control expiry as a human-readable string."""
    if expires is None:
        return "unknown"
    remaining = expires - _wall_time()
    if remaining <= 0:
        return "due now"
    if remaining < 60:
        return f"{remaining:.0f}s"
    return f"{remaining / 60:.0f}m"


def _fmt_age(age_s):
    """Format the age of NOAA model data as a human-readable string.

    Returns seconds for sub-minute ages and minutes otherwise — no hours
    conversion, so the number is always immediately readable without mental
    arithmetic.
    """
    if age_s is None:
        return "unknown"
    if age_s < 60:
        return f"{age_s:.0f}s"
    return f"{int(age_s // 60)}m"


def _refresh_forecasts(station, clock, led):
    """Fetch hourly forecast and griddata aligned with NOAA's cache windows.

    Both endpoints are fetched whenever their NOAA Cache-Control window has
    expired, rather than on fixed polling intervals. This means we fetch as
    soon as NOAA says new data may be available — never before (wasted
    bandwidth) and at most one loop tick (~1 minute) after.

    Calls that start with insufficient budget are skipped transparently by
    network.request() and network.get_stream() — no separate guard is needed
    here.

    Returns True if any fetch was attempted (hourly or griddata), False
    otherwise. The caller uses this to decide whether to redraw the display.
    """
    if not station.station_id:
        return False

    print(f"Forecast age:   hourly {_fmt_age(station.hourly_update_age)}")
    print(f"Forecast cache: hourly {_fmt_ttl(station.hourly_expires)}, "
          f"griddata {_fmt_ttl(station.griddata_expires)}")

    now = _wall_time()
    hourly_due = (
        station.hourly_expires is None
        or now >= station.hourly_expires
    )
    fetched = False
    if hourly_due:
        fetched = True
        led.working(BLUE)
        station.get_hourly_forecast()
        if station.hourly_complete_for:
            led.success()
        else:
            led.failure()

    griddata_due = (
        station.griddata_expires is None
        or now >= station.griddata_expires
    )
    if station.hourly_model_updated and griddata_due:
        fetched = True
        led.working(BLUE)
        station.get_griddata()
        if station.griddata_complete_for:
            led.success()
        else:
            led.failure()

    return fetched


def _check_temp_freshness(display, station):
    """Turn the current-temp label purple when the NOAA forecast model is stale.

    hourly_update_age measures time since NOAA last ran the forecast model
    (from the updateTime field), not since we last fetched. The threshold is
    intentionally high — it should only fire when NOAA has genuinely missed
    multiple update cycles, not during the normal 6-hour cadence.

    No-op when no hourly data has been loaded yet (leaves the initial gray
    label alone). When fresh, the caller relies on update_forecast() having
    already set the correct palette color — this function only overrides in
    the stale direction, never in the fresh direction."""
    if not station.hourly_model_updated:
        return
    age = station.hourly_update_age
    if age is not None and age >= TEMP_STALE_MINUTES * 60:
        print(f"Forecast model is {age // 3600:.0f}h old — marking current temp stale")
        display.mark_temp_stale()


def run(config):
    """Main event loop: fetch weather data, update display, sync time."""

    network.user_agent = config.get('USER_AGENT')

    display = Display(config)
    clock = Clock(config)
    station = Station(config)
    led = StatusLED()

    if config.get('AUTO_SCALE'):
        print("Temperature scale: auto — will query ACIS for all-time range")
    else:
        print(f"Temperature scale: fixed — "
              f"min={config.get('TEMP_MIN', DEFAULTS['TEMP_MIN'])}°F, "
              f"max={config.get('TEMP_MAX', DEFAULTS['TEMP_MAX'])}°F")

    # Watchdog: one feed per loop iteration in WatchDogMode.RAISE.  The 61 s
    # window is shared across the entire loop body so no single section can
    # stall indefinitely.  On WatchDogTimeout, _reset_session() force-closes
    # all pooled sockets (same cleanup as startup) and the loop restarts.
    # Per-request socket timeouts are the primary backstop; the watchdog only
    # fires if a server accepts the connection but stalls before sending headers.
    # _reset_session() also runs at startup because adafruit_connection_manager's
    # socket registry survives soft reloads — stale sockets would raise
    # RuntimeError on the first get_socket() call for a reused host.
    watchdog = microcontroller.watchdog
    watchdog.timeout = WATCHDOG_TIMEOUT_SECONDS

    network._reset_session()  # clear any sockets left by a previous run or soft-reload
    _boot_time      = monotonic()
    _ever_connected = False
    _last_plotted_hour = None

    display.network_label.text  = config.get('CIRCUITPY_WIFI_SSID', '')
    display.network_label.color = display.QUERY_COLOR

    while True:
        watchdog.mode = WatchDogMode.RAISE

        led.idle()
        watchdog.feed()  # sole feed — starts the 61 s budget for this iteration
        t_feed = monotonic()
        network.set_iteration_deadline(t_feed + (60 - localtime().tm_sec) - NETWORK_DEADLINE_MARGIN_SECONDS)

        try:
            display.update_clock(clock)

            print("-" * 78)
            _collect_garbage()

            ssid = _ensure_network(config, led)
            if not ssid:
                if not _ever_connected:
                    if monotonic() - _boot_time >= BOOT_PORTAL_THRESHOLD_MINUTES * 60:
                        raise PortalNeeded()
                else:
                    age = station.hourly_update_age
                    if age is None or age >= FORECAST_STALE_MINUTES * 60:
                        raise PortalNeeded()
                # NTP cannot run without a network — mark the clock uncertain so
                # the display signals that the displayed time may be drifting.
                # Recovers to white when sync_network_time() next succeeds.
                clock.uncertain()
                _check_temp_freshness(display, station)
                sleep(RETRY_DELAY_SECONDS)
                continue

            _ever_connected = True
            if display.screen == Display.SCREEN_BOOT:
                display.network_label.color = display.SUCCESS_COLOR

            if not _ensure_location(display, station, clock, led):
                continue

            _ensure_station(display, station, clock, led)

            clock.sync_network_time()
            display.update_clock(clock)

            _ensure_temp_range(display, station, config, led)

            historical_changed = _refresh_historical(station, clock, led)
            forecast_changed   = _refresh_forecasts(station, clock, led)

            current_hour = localtime().tm_hour
            if station.hourly_model_updated and (
                forecast_changed
                or historical_changed
                or current_hour != _last_plotted_hour
            ):
                display.show_weather()
                hist_dates = [s['date'] if s is not None else "None"
                              for s in station.historical]
                print(f"Historical slots: {hist_dates}")
                display.update_forecast(station.hourly, station.historical, clock.isotime)
                _check_temp_freshness(display, station)
                _last_plotted_hour = current_hour

            if (historical_changed or forecast_changed) and localtime().tm_sec <= 59 - SUCCESS_DISPLAY_SECONDS:
                sleep(SUCCESS_DISPLAY_SECONDS)
            led.idle()
            clock.wait()
        except WatchDogTimeout:
            print("Watchdog timeout — resetting session and skipping iteration")
            network._reset_session()
        # Unexpected exceptions propagate so the device either shows a
        # traceback (RELOAD_ON_ERROR=0) or reboots (RELOAD_ON_ERROR=1).
        # Silently swallowing bugs would make them much harder to diagnose
        # on a device with no persistent log.
