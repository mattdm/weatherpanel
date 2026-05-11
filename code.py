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
    TEMP_MIN                 int   Minimum temperature (°F) at the bottom of the color scale
                                   (default -5)
    TEMP_MAX                 int   Maximum temperature (°F) at the top of the color scale
                                   (default 105)
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

config = {
          'CIRCUITPY_WIFI_SSID' : None,
          'CIRCUITPY_WIFI_PASSWORD' : None,
          'USER_AGENT': "weatherpanel (codeberg.org/mattdm/weatherpanel)",
          'GEOLOCATION_API': "http://ip-api.com/json/",
          'GRIDPOINT_API': "https://api.weather.gov/points",
          'HISTORICAL_API': "https://data.rcc-acis.org/GridData",
          'LATITUDE': None,
          'LONGITUDE': None,
          'SWAP_GREEN_BLUE': False,
          'RELOAD_ON_ERROR': False,
          'TEMP_MIN': -5,
          'TEMP_MAX': 105,
          'HISTORY_YEARS': 10,
          'CLOCK_TWENTYFOUR': False,
          'CLOCK_DELIMITER': ':',
          'AP_SSID': 'WP',
          'AP_PASSWORD': None,
          'FORCE_PORTAL': False
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

# getenv() always returns strings; coerce bool and int keys to their proper types
# so that settings.toml values like SWAP_GREEN_BLUE = 0 are treated as falsy.
_BOOL_KEYS = ('SWAP_GREEN_BLUE', 'RELOAD_ON_ERROR', 'CLOCK_TWENTYFOUR', 'FORCE_PORTAL')
_INT_KEYS  = ('TEMP_MIN', 'TEMP_MAX', 'HISTORY_YEARS')


def _coerce_config(cfg, bool_keys, int_keys):
    """Coerce bool and int config keys to their proper Python types.

    Bool keys: any string not in ('0', 'false', 'no', '') is truthy.
    Int keys: converted with int(); invalid values are collected as errors
    rather than raising, so callers can route to the portal with context.

    Returns a ``(coerced_cfg, errors)`` tuple where ``errors`` is a dict
    mapping key name to a human-readable error string.  An empty dict means
    all conversions succeeded.

    The input dict is mutated in place and also returned for convenience.
    """
    errors = {}
    for key in bool_keys:
        v = cfg[key]
        if isinstance(v, str):
            cfg[key] = v.lower() not in ('0', 'false', 'no', '')
    for key in int_keys:
        try:
            cfg[key] = int(cfg[key])
        except (ValueError, TypeError):
            errors[key] = f'{key} must be a whole number; got {cfg[key]!r}'
    return cfg, errors


config, _config_errors = _coerce_config(config, _BOOL_KEYS, _INT_KEYS)

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

if _config_errors or config.get('FORCE_PORTAL') or not config.get('CIRCUITPY_WIFI_SSID') or button_held:
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
