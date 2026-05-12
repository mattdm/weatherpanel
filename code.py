"""CircuitPython entry point for weather panel display.

Loads configuration from defaults merged with environment variables from
settings.toml, then runs the main scheduler loop.

Configuration keys (all set via settings.toml environment variables):

  Network
    CIRCUITPY_WIFI_SSID      str   Wi-Fi network name
    CIRCUITPY_WIFI_PASSWORD  str   Wi-Fi password

  Location (required -- set via the setup portal)
    LATITUDE                 str   Decimal latitude, e.g. "42.39"
    LONGITUDE                str   Decimal longitude, e.g. "-71.13"

  API
    USER_AGENT               str   User-Agent header for API requests
                                   (api.weather.gov requires one)
    GRIDPOINT_API            str   NOAA gridpoint base URL
    HISTORICAL_API           str   RCC ACIS GridData endpoint

  Display
    SWAP_GREEN_BLUE          bool  Set True if panel has G/B pins wired reversed
    AUTO_SCALE               bool  Query ACIS for all-time high/low at startup and use
                                   them as the temperature scale (default True); set False
                                   to use fixed TEMP_MIN / TEMP_MAX instead
    TEMP_MIN                 int   Minimum temperature (°F) at the bottom of the color scale
                                   (default -5; ignored when AUTO_SCALE is True)
    TEMP_MAX                 int   Maximum temperature (°F) at the top of the color scale
                                   (default 105; ignored when AUTO_SCALE is True)
    HISTORY_YEARS            int   Years of PRISM climate data for the record/average
                                   temperature baseline (default 10)

  Clock
    CLOCK_TWENTYFOUR         bool  Set True for 24-hour display (default: 12-hour)
    CLOCK_DELIMITER          str   Hour/minute separator character (default ":")

  Portal (access-point configuration mode)
    AP_SSID                  str   SSID for the config portal access point
                                   (default "WP")
    AP_PASSWORD              str   Password for the portal AP; omit for open network
                                   (default: open network)
    FORCE_PORTAL             bool  Set True to enter portal mode unconditionally
                                   (debug/testing only)

  Error handling
    RELOAD_ON_ERROR          bool  Reload code on unhandled exception (default False,
                                   which leaves traceback on screen until reset)
"""
import gc
gc.collect()
print(f"Free memory: {gc.mem_free()} (at start)")
from os import getenv

import supervisor
from appconfig import DEFAULTS, BOOL_KEYS, INT_KEYS, SECRETS, coerce_config

config = dict(DEFAULTS)

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

# getenv() always returns strings; coerce bool and int keys to their proper types
# so that settings.toml values like SWAP_GREEN_BLUE = 0 are treated as falsy.
config, _config_errors = coerce_config(config, BOOL_KEYS, INT_KEYS)

# sticky_on_error keeps the display showing error traceback until user intervention.
# Set before any further processing so that even a config error produces a visible
# traceback rather than a silent reload loop.
print(f"Setting reload on error to {config['RELOAD_ON_ERROR']}")
supervisor.set_next_code_file(None, reload_on_error=config['RELOAD_ON_ERROR'], sticky_on_error=True)

if _config_errors:
    for _k, _msg in _config_errors.items():
        print(f"Config error: {_msg}")

import board, digitalio
up = digitalio.DigitalInOut(board.BUTTON_UP)
up.switch_to_input(pull=digitalio.Pull.UP)
dn = digitalio.DigitalInOut(board.BUTTON_DOWN)
dn.switch_to_input(pull=digitalio.Pull.UP)
button_held = not up.value or not dn.value

if (_config_errors or config.get('FORCE_PORTAL') or not config.get('CIRCUITPY_WIFI_SSID')
        or not config.get('LATITUDE') or not config.get('LONGITUDE') or button_held):
    import portal
    portal.run(config, config_errors=_config_errors)
else:
    import scheduler
    try:
        scheduler.run(config)
    except scheduler.PortalNeeded:
        print("Wi-Fi unavailable — entering configuration portal")
        import portal
        portal.run(config)
