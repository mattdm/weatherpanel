"""Main scheduler loop orchestrating network, weather data, clock, and display.

Coordinates periodic updates of weather forecasts, historical baselines, and
time synchronization while managing the display and watchdog timer.
"""
import gc
import microcontroller
from watchdog import WatchDogMode, WatchDogTimeout
from time import sleep

from clock import Clock
from display import Display
from station import Station
from statusled import BLUE, CYAN, PURPLE, YELLOW, StatusLED
import network

WATCHDOG_TIMEOUT_S = 60
HOURLY_POLL_INTERVAL = 5
HOURLY_POLL_OFFSET = 4
GRIDDATA_POLL_INTERVAL = 20
GRIDDATA_POLL_OFFSET = 9
RETRY_DELAY_S = 5


def _collect_garbage():
    """Force garbage collection and report memory status."""
    mem_before = gc.mem_free()
    gc.collect()
    print(f"Free memory: {mem_before} → {gc.mem_free()}")


def _ensure_network(display, config, led):
    """Check Wi-Fi and reconnect if needed. Returns True if connected."""
    ssid = network.check()
    if not ssid:
        led.wifi_down()
        display.set_status(label="network", status="failure", text=config['CIRCUITPY_WIFI_SSID'])
        sleep(RETRY_DELAY_S)
        led.working(YELLOW)
        display.set_status(label="network", status="query", text=config['CIRCUITPY_WIFI_SSID'])
        network.connect(config)
        return False
    return True


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
        station.get_station()
        if station.station_id:
            led.success()
            display.set_status(label="station", status="success", text=station.station_id)
            if station.tz and not clock.tz:
                clock.set_tz(station.tz)
            if station.city:
                display.set_status(label="location", status="success", text=station.city)
        else:
            led.failure()
            display.set_status(label="station", status="failure", text="Station?")


def _refresh_historical(display, station, clock, led):
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


def _refresh_forecasts(station, clock, led):
    """Fetch hourly forecast and griddata on their staggered cadences."""
    if not station.station_id:
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

    # Watchdog bounds how long the loop can run without updating the display.
    # Feeds are deliberately placed only at the top level between helpers --
    # NOT inside helpers -- so that long retry loops correctly trigger a reset
    # rather than silently delaying the clock update.
    watchdog = microcontroller.watchdog
    watchdog.timeout = WATCHDOG_TIMEOUT_S

    while True:
        watchdog.mode = WatchDogMode.RESET

        try:
            led.idle()
            watchdog.feed()
            display.update_time(clock)
            print("-" * 78)
            _collect_garbage()

            if not _ensure_network(display, config, led):
                continue

            if not station.hourly:
                display.set_status(label="network", status="success", text=network.check())

            watchdog.feed()

            if not _ensure_location(display, station, clock, led):
                continue

            clock.sync_network_time()
            display.update_time(clock)

            _refresh_historical(display, station, clock, led)

            watchdog.feed()

            _ensure_station(display, station, clock, led)

            watchdog.feed()

            _refresh_forecasts(station, clock, led)

            watchdog.feed()

            if station.hourly:
                display.clear_status()
                display.update_hourly_forecast(station.hourly, station.historical, clock.isotime)

            watchdog.feed()
            clock.wait()

        except WatchDogTimeout:
            print(f"Watchdog Exception: {WATCHDOG_TIMEOUT_S} seconds!")
        # Intentionally no broad except here: unexpected exceptions should
        # propagate so the device either shows a traceback (RELOAD_ON_ERROR=0)
        # or reboots (RELOAD_ON_ERROR=1). Silently swallowing bugs would make
        # them much harder to diagnose on a device with no persistent log.
