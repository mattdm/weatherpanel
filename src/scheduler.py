"""Main scheduler loop orchestrating network, weather data, clock, and display.

Coordinates periodic updates of weather forecasts, historical baselines, and
time synchronization while managing the display and watchdog timer.
"""
import gc
import microcontroller
from watchdog import WatchDogMode, WatchDogTimeout
from time import localtime, sleep, monotonic

from appconfig import DEFAULTS
from clock import Clock
from display import Display
from station import Station
from statusled import BLUE, CYAN, PURPLE, YELLOW, StatusLED
import network

# 61 s = one clock minute plus 1 s of jitter margin. The loop body plus
# clock.wait() together consume approximately 60 s by construction, but
# sleep() inaccuracy and function-call overhead can push the actual total
# just past 60 s, triggering a spurious WatchDogTimeout. The extra second
# absorbs that without letting a genuinely stalled loop run for more than
# one display-update cycle undetected.
WATCHDOG_TIMEOUT_S = 61
HOURLY_POLL_INTERVAL = 5
HOURLY_POLL_OFFSET = 4
GRIDDATA_POLL_INTERVAL = 20
GRIDDATA_POLL_OFFSET = 7    # 7%5==2 != HOURLY_POLL_OFFSET(4) — never collides with hourly
RETRY_DELAY_S = 5
SUCCESS_DISPLAY_S = 3
FORECAST_HEADROOM_S = 50    # seconds of headroom needed to start a forecast fetch
GRIDDATA_MIN_BUDGET_S = 20  # minimum watchdog seconds remaining to attempt griddata
PORTAL_THRESHOLD_S = 30


class PortalNeeded(Exception):
    """Raised by scheduler.run() after Wi-Fi failures exceed PORTAL_THRESHOLD_S.

    Caught by code.py, which then imports and runs the configuration portal.
    """


def _collect_garbage():
    """Force garbage collection and report memory status."""
    mem_before = gc.mem_free()
    gc.collect()
    print(f"Memory: {network._fmt_bytes(mem_before)} → {network._fmt_bytes(gc.mem_free())} free")


def _ensure_network(display, config, led):
    """Check Wi-Fi and reconnect if needed. Returns SSID string if connected, else None."""
    ssid = network.check()
    if not ssid:
        led.wifi_down()
        display.set_status(label="network", status="failure", text=config['CIRCUITPY_WIFI_SSID'])
        sleep(RETRY_DELAY_S)
        led.working(YELLOW)
        display.set_status(label="network", status="query", text=config['CIRCUITPY_WIFI_SSID'])
        network.connect(config)
        return None
    return ssid


def _ensure_location(display, station, clock, led):
    """Geolocate if needed and check bounds. Returns True if ready to proceed."""
    if not station.location:
        led.working(CYAN)
        display.set_status(label="location", status="query", text="Locating...")
        station.geolocate()
        if station.location:
            led.success()
            display.set_status(label="location", status="success", text=station.location)
            station.check_bounds()
            if station.tz:
                clock.set_tz(station.tz)
        else:
            led.failure()
            display.set_status(label="location", status="failure", text="Location?")
            return False

    if station.unsupported:
        led.failure()
        display.set_status(label="location", status="failure", text="Area not")
        display.set_status(label="station", status="failure", text="supported")
        clock.wait()
        return False

    return True


def _ensure_station(display, station, clock, led):
    """Resolve NOAA station metadata if needed."""
    if station.location and not station.station_id:
        led.working(CYAN)
        display.set_status(label="station", status="query", text="Station?")
        display.flush()  # show "Station?" before the network call
        station.get_station()
        if station.tz and not clock.tz:
            clock.set_tz(station.tz)
            display.update_time(clock)
        if station.station_id:
            led.success()
            display.set_status(label="station", status="success", text=station.station_id)
            if station.city:
                display.set_status(label="location", status="success", text=station.city)
            # Flush green station name before _ensure_temp_range hides the status group.
            display.flush()
        else:
            led.failure()
            display.set_status(label="station", status="failure", text="Station?")


def _ensure_temp_range(display, station, config, led):
    """Query ACIS for all-time temperature range when AUTO_SCALE is enabled.

    Skips if AUTO_SCALE is False, location is not yet set, or the range
    has already been fetched this session (idempotent).  On success, updates
    the display scale.  The calibration screen is shown only when no hourly
    forecast has loaded yet — if the forecast is already on-screen (e.g.
    because the first ACIS attempt failed and we are retrying), showing the
    calibration would momentarily overlay the live forecast, so we skip it.
    On failure, leaves station.temp_min as None so the next loop iteration
    retries."""
    if not config.get('AUTO_SCALE'):
        return
    if not station.lat or not station.lon:
        return
    if station.temp_min is not None:
        return

    led.working(PURPLE)
    result = station.get_temp_range()
    if result:
        temp_min, temp_max = result
        display.set_temp_range(temp_min, temp_max)
        if not station.hourly:
            display.show_temp_range(station.city, station.station_id)
        led.success()
    else:
        led.failure()


def _refresh_historical(station, clock, led):
    """Fill empty slots in the historical circular buffer.

    Rotates the buffer when the date has changed (midnight), then fetches
    all missing slots in one call. Each fetch is ~250ms so filling all
    four at cold boot takes under a second — well within the watchdog
    budget. On failure a slot stays None and will be retried next
    iteration."""
    if not station.location or not clock.tz or not clock.today:
        return

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


def _refresh_forecasts(station, clock, led, t_feed=None):
    """Fetch hourly forecast and griddata on their staggered cadences.

    Skips all fetches if the second-hand is at or past FORECAST_HEADROOM_S,
    deferring to the next due minute. This mirrors the SUCCESS_DISPLAY_S guard
    and ensures a potentially slow fetch does not start with too little of the
    minute remaining.

    t_feed: monotonic() timestamp from the most recent watchdog.feed() call.
    When provided, the griddata fetch is skipped if fewer than
    GRIDDATA_MIN_BUDGET_S seconds remain in the watchdog budget — a slow
    hourly fetch on a congested network can otherwise leave too little time
    and trigger a watchdog timeout mid-griddata.
    """
    if not station.station_id:
        return

    if 60 - localtime().tm_sec < FORECAST_HEADROOM_S:
        return

    hourly_due = clock.minute % HOURLY_POLL_INTERVAL == HOURLY_POLL_OFFSET
    if hourly_due or not station.hourly:
        led.working(BLUE)
        station.get_hourly_forecast()
        if station.hourly:
            led.success()
        else:
            led.failure()

    griddata_due = clock.minute % GRIDDATA_POLL_INTERVAL == GRIDDATA_POLL_OFFSET
    if station.hourly and (griddata_due or not station.griddata_updated):
        if t_feed is not None:
            remaining = WATCHDOG_TIMEOUT_S - (monotonic() - t_feed)
            if remaining < GRIDDATA_MIN_BUDGET_S:
                print(f"Skipping grid data — only {remaining:.0f} s of watchdog budget remaining")
                return
        led.working(BLUE)
        station.get_griddata()
        if station.griddata_updated:
            led.success()
        else:
            led.failure()


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

    # Watchdog design: ONE feed per loop iteration, at the top of the try
    # block, in WatchDogMode.RAISE.
    #
    # ONE feed: the budget covers the entire loop body plus clock.wait()
    # together. Earlier versions fed the watchdog between each helper call,
    # which let any single section stall for a full 61 s on its own — a hung
    # network fetch could block the clock for many minutes before a reset.
    # With a single feed the 61 s window is shared by everything, so a stall
    # anywhere triggers recovery within one minute.
    #
    # RAISE mode: WatchDogTimeout is injected as a Python exception instead
    # of triggering a hard MCU reset. This lets the except block force-close
    # all tracked sockets and reset the network session, then restart the loop
    # without losing state that took time to acquire (station metadata,
    # historical baselines, hourly forecast). A hard reset would discard all
    # of that and force a full cold-boot sequence on every timeout.
    #
    # Startup reset: adafruit_connection_manager's global socket registry
    # survives CircuitPython soft reloads. A socket left "in use" by a
    # previous run would cause the first get_socket() call for the same host
    # to raise RuntimeError. _reset_session() force-closes all tracked sockets
    # before the loop begins, ensuring a clean slate regardless of how the
    # previous run ended.
    watchdog = microcontroller.watchdog
    watchdog.timeout = WATCHDOG_TIMEOUT_S

    network._reset_session()  # clear any sockets left by a previous run or soft-reload
    _failure_start = None

    while True:
        watchdog.mode = WatchDogMode.RAISE

        try:
            led.idle()
            watchdog.feed()  # sole feed — starts the 61 s budget for this iteration
            t_feed = monotonic()

            display.update_time(clock)

            print("-" * 78)
            _collect_garbage()

            ssid = _ensure_network(display, config, led)
            if not ssid:
                if _failure_start is None:
                    _failure_start = monotonic()
                elif monotonic() - _failure_start >= PORTAL_THRESHOLD_S:
                    raise PortalNeeded()
                continue

            _failure_start = None  # reset: network is up

            if not station.hourly:
                display.set_status(label="network", status="success", text=ssid)

            if not _ensure_location(display, station, clock, led):
                continue

            clock.sync_network_time()
            display.update_time(clock)

            _refresh_historical(station, clock, led)

            _ensure_station(display, station, clock, led)

            _ensure_temp_range(display, station, config, led)

            _refresh_forecasts(station, clock, led, t_feed)

            if station.hourly:
                display.clear_status()
                display.update_hourly_forecast(station.hourly, station.historical, clock.isotime)

            if localtime().tm_sec <= 59 - SUCCESS_DISPLAY_S:
                sleep(SUCCESS_DISPLAY_S)
            led.idle()
            clock.wait()

        except WatchDogTimeout:
            # If the timeout fired before adafruit_requests constructed a Response
            # object (e.g. during the TLS handshake), __exit__ was never called and
            # the socket is stuck "in use" in the connection manager's registry.
            # _reset_session() force-closes all tracked sockets via
            # connection_manager_close_all(), so the next iteration starts clean.
            print(f"Watchdog timeout after {WATCHDOG_TIMEOUT_S} s — resetting network")
            network._reset_session()
        # Intentionally no broad except here: unexpected exceptions should
        # propagate so the device either shows a traceback (RELOAD_ON_ERROR=0)
        # or reboots (RELOAD_ON_ERROR=1). Silently swallowing bugs would make
        # them much harder to diagnose on a device with no persistent log.
