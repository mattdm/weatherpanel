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


def _ensure_network(display, config):
    """Check Wi-Fi and reconnect if needed. Returns True if connected."""
    ssid = network.check()
    if not ssid:
        display.set_status(label="network", status="failure", text=config['CIRCUITPY_WIFI_SSID'])
        sleep(RETRY_DELAY_S)
        display.set_status(label="network", status="query", text=config['CIRCUITPY_WIFI_SSID'])
        network.connect(config)
        return False
    return True


def _ensure_location(display, station, clock):
    """Geolocate if needed and check bounds. Returns True if ready to proceed."""
    if not station.location:
        display.set_status(label="location", status="query", text="Locating...")
        station.geolocate()
        if station.location:
            display.set_status(label="location", status="success", text=station.location)
            station.check_bounds()
            if station.tz:
                clock.set_tz(station.tz)
        else:
            display.set_status(label="location", status="failure", text="Location?")

    if station.unsupported:
        display.set_status(label="location", status="failure", text="Area not")
        display.set_status(label="station", status="failure", text="supported")
        clock.wait()
        return False

    return True


def _ensure_station(display, station, clock):
    """Resolve NOAA station metadata if needed."""
    if station.location and not station.station_id:
        display.set_status(label="station", status="query", text="Station?")
        station.get_station()
        if station.station_id:
            display.set_status(label="station", status="success", text=station.station_id)
            if station.tz and not clock.tz:
                clock.set_tz(station.tz)
            if station.city:
                display.set_status(label="location", status="success", text=station.city)
        else:
            display.set_status(label="station", status="failure", text="Station?")


def _refresh_historical(display, station, clock):
    """Fetch historical baseline on new day or when missing."""
    if 'date' in station.historical and clock.today != station.historical['date']:
        print("It's a new day.")
        station.historical = {}

    if station.location and not station.historical and clock.tz:
        display.set_status(label="station", status="query", text="History?")
        station.get_historical(clock.today)
        if station.historical:
            display.set_status(label="station", status="success", text="History.")
        else:
            display.set_status(label="station", status="failure", text="History?")


def _refresh_forecasts(station, clock):
    """Fetch hourly forecast and griddata on their staggered cadences."""
    if not station.station_id:
        return

    hourly_due = clock.minute % HOURLY_POLL_INTERVAL == HOURLY_POLL_OFFSET
    if hourly_due or not station.hourly:
        station.get_hourly_forecast()

    griddata_due = clock.minute % GRIDDATA_POLL_INTERVAL == GRIDDATA_POLL_OFFSET
    if station.hourly and (griddata_due or not station.griddata_updated):
        station.get_griddata()


def run(config):
    """Main event loop: fetch weather data, update display, sync time."""

    network.user_agent = config.get('USER_AGENT')

    display = Display(config)
    clock = Clock(config)
    station = Station(config)

    # Watchdog bounds how long the loop can run without updating the display.
    # Feeds are deliberately placed only at the top level between helpers --
    # NOT inside helpers -- so that long retry loops correctly trigger a reset
    # rather than silently delaying the clock update.
    watchdog = microcontroller.watchdog
    watchdog.timeout = WATCHDOG_TIMEOUT_S

    while True:
        watchdog.mode = WatchDogMode.RESET

        try:
            watchdog.feed()
            display.update_time(clock)
            print("-" * 78)
            _collect_garbage()

            if not _ensure_network(display, config):
                continue

            if not station.hourly:
                display.set_status(label="network", status="success", text=network.check())

            watchdog.feed()

            if not _ensure_location(display, station, clock):
                continue

            clock.sync_network_time()
            display.update_time(clock)

            _refresh_historical(display, station, clock)

            watchdog.feed()

            _ensure_station(display, station, clock)

            watchdog.feed()

            _refresh_forecasts(station, clock)

            watchdog.feed()

            if station.hourly:
                display.clear_status()
                display.update_hourly_forecast(station.hourly, station.historical, clock.isotime)

            watchdog.feed()
            clock.wait()

        except WatchDogTimeout:
            print("Watchdog Exception: 60 seconds!")
