"""CircuitPython entry point for weather panel display.

Loads configuration from defaults merged with environment variables from
settings.toml, then runs the main scheduler loop.

Configuration keys (all set via settings.toml environment variables):

  Network
    CIRCUITPY_WIFI_SSID      str   Wi-Fi network name
    CIRCUITPY_WIFI_PASSWORD  str   Wi-Fi password

  Location (optional -- if omitted, IP geolocation is used)
    LATITUDE                 str   Decimal latitude, e.g. "42.39"
    LONGITUDE                str   Decimal longitude, e.g. "-71.13"

  API
    USER_AGENT               str   User-Agent header for API requests
                                   (api.weather.gov requires one)
    GEOLOCATION_API          str   IP geolocation JSON endpoint
    GRIDPOINT_API            str   NOAA gridpoint base URL
    HISTORICAL_API           str   RCC ACIS GridData endpoint

  Display
    SWAP_GREEN_BLUE          bool  Set True if panel has G/B pins wired reversed
    TEMP_SCALE_RANGE         int   Total °F span of the temperature color scale
                                   (default 110: covers ~-5°F to ~105°F)
    TEMP_MIDPOINT            int   Temperature (°F) mapped to center of scale
                                   (default 50)

  Clock
    CLOCK_TWENTYFOUR         bool  Set True for 24-hour display (default: 12-hour)
    CLOCK_DELIMINATOR        str   Hour/minute separator character (default ":")
                                   Note: key name has a typo; kept for compatibility

  Error handling
    RELOAD_ON_ERROR          bool  Reload code on unhandled exception (default False,
                                   which leaves traceback on screen until reset)
"""
import gc
gc.collect()
print(f"Free memory: {gc.mem_free()} (at start)")
from os import getenv

import supervisor

import scheduler

config = {
          'CIRCUITPY_WIFI_SSID' : 'change me in settings.toml',
          'CIRCUITPY_WIFI_PASSWORD' : 'change me in settings.toml',
          'USER_AGENT': "weatherpanel (codeberg.org/mattdm/weatherpanel)",
          'GEOLOCATION_API': "http://ip-api.com/json/",
          'GRIDPOINT_API': "https://api.weather.gov/points/",
          'HISTORICAL_API': "https://data.rcc-acis.org/GridData",
          'LATITUDE': None,
          'LONGITUDE': None,
          'SWAP_GREEN_BLUE': False,
          'RELOAD_ON_ERROR': False,
          'TEMP_SCALE_RANGE': 110,
          'TEMP_MIDPOINT': 50
         }

SECRETS = {'CIRCUITPY_WIFI_PASSWORD'}

for conf in config:
    val = getenv(conf)
    if val:
        config[conf] = val
        if conf in SECRETS:
            print(f"{conf} = '****'")
        else:
            print(f"{conf} = '{val}'")
    else:
        print(f"{conf} = '{config[conf]}' (default)")

# sticky_on_error keeps the display showing error traceback until user intervention
print(f"Setting reload on error to {config['RELOAD_ON_ERROR']}")
supervisor.set_next_code_file(None,reload_on_error=config['RELOAD_ON_ERROR'],sticky_on_error=True)

scheduler.run(config)
