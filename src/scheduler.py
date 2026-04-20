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

def collect_garbage():
        """Force garbage collection and report memory status.
        
        CircuitPython's limited heap requires explicit GC to prevent fragmentation."""
        mem_before = gc.mem_free()
        gc.collect()
        print(f"Free memory: {mem_before} → {gc.mem_free()}")


def run(config):
    """Main event loop: fetch weather data, update display, sync time.
    
    Loop phases:
    - Check network connectivity
    - Geolocate and fetch station metadata (once)
    - Sync NTP time
    - Fetch historical baseline (daily, for temperature color-coding)
    - Fetch hourly forecast (every 5 minutes)
    - Fetch griddata QPF/snowfall (every 20 minutes)
    - Update display
    - Wait for next minute
    """

    network.user_agent = config.get('USER_AGENT')

    display = Display(config)
    clock = Clock(config)
    station = Station(config)

    watchdog = microcontroller.watchdog
    watchdog.timeout = WATCHDOG_TIMEOUT_S


    while True:

        watchdog.mode = WatchDogMode.RESET 

        try:
            
            watchdog.feed()

            display.update_time(clock)

            print("-" * 78)


            collect_garbage()


            ssid = network.check()
            if not ssid:
                display.set_status(label="network",status="failure",text=config['CIRCUITPY_WIFI_SSID'])
                sleep(RETRY_DELAY_S)
                display.set_status(label="network",status="query",text=config['CIRCUITPY_WIFI_SSID'])
                network.connect(config)
                continue
            elif not station.hourly:
                display.set_status(label="network",status="success",text=ssid)
                watchdog.feed()



            if not station.location:
                display.set_status(label="location",status="query",text="Locating...")
                station.geolocate()
                if station.location:
                    display.set_status(label="location",status="success",text=station.location)
                    station.check_bounds()
                    if station.tz:
                        clock.set_tz(station.tz)
                    watchdog.feed()
                else:
                    display.set_status(label="location",status="failure",text=station.location)

            if station.unsupported:
                display.set_status(label="location",status="failure",text="Area not")
                display.set_status(label="station",status="failure",text="supported")
                clock.wait()
                continue


            clock.sync_network_time()
            display.update_time(clock)


            if 'date' in station.historical and clock.today != station.historical['date']:
                print ("It's a new day.")
                station.historical={}


            if station.location and not station.historical and clock.tz:
                display.set_status(label="station",status="query",text="History?")
                station.get_historical(clock.today)
                if station.historical:
                    display.set_status(label="station",status="success",text="History.")
                    watchdog.feed()
                else:
                    display.set_status(label="station",status="failure",text="History?")

            if station.location and not station.station_id:
                display.set_status(label="station",status="query",text="Station?")
                station.get_station()
                if station.station_id:
                    display.set_status(label="station",status="success",text=station.station_id)
                    if station.tz and not clock.tz:
                        clock.set_tz(station.tz)
                    if station.city:
                        display.set_status(label="location",status="success",text=station.city)
                        watchdog.feed()
                else:
                    display.set_status(label="station",status="failure",text="Station?")


            # Staggered poll cadences to avoid simultaneous memory-intensive fetches.
            # Known gap: stale hourly data (>6 hours old) is not evicted between refreshes.
            if station.station_id and (clock.minute % HOURLY_POLL_INTERVAL == HOURLY_POLL_OFFSET or not station.hourly):
                station.get_hourly_forecast()
                watchdog.feed()

            if station.station_id and station.hourly and (clock.minute % GRIDDATA_POLL_INTERVAL == GRIDDATA_POLL_OFFSET or not station.griddata_updated):
                station.get_griddata()
                watchdog.feed()

            if station.hourly:
                display.clear_status()                
                display.update_hourly_forecast(station.hourly,station.historical,clock.isotime)
                watchdog.feed()

            watchdog.feed()

            clock.wait()  # Sleep until the minute changes


        except WatchDogTimeout:
            print("Watchdog Exception: 60 seconds!")
            
