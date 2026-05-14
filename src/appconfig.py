"""Shared configuration schema for code.py (CircuitPython) and bin/simulate (CPython).

Defines the canonical set of config keys, their defaults, their types, and the
coercion function.  Both the on-device entry point (code.py) and the desktop
simulator (bin/simulate) import from here so the schema stays in one place.

``DEFAULTS`` — the baseline config dict; callers copy it and overlay settings
  from their respective sources (CircuitPython os.getenv / tomllib).

``BOOL_KEYS`` / ``INT_KEYS`` — which keys need type coercion after loading.

``SECRETS`` — keys whose values must not be printed in plain text.

``coerce_config(cfg, bool_keys, int_keys)`` — mutates a config dict in place
  and returns ``(cfg, errors)`` where ``errors`` maps key → message for any
  value that could not be converted.
"""

DEFAULTS = {
    'CIRCUITPY_WIFI_SSID':     None,
    'CIRCUITPY_WIFI_PASSWORD': None,
    'USER_AGENT':              "weatherpanel (codeberg.org/mattdm/weatherpanel)",  # recommended but not required by api.weather.gov
    'GRIDPOINT_API':           "https://api.weather.gov/points",
    'HISTORICAL_API':          "https://data.rcc-acis.org/GridData",
    'LATITUDE':                None,
    'LONGITUDE':               None,
    'SWAP_GREEN_BLUE':         False,   # set True if the panel has G/B pins wired reversed
    'RELOAD_ON_ERROR':         False,   # False leaves traceback on screen; True silently reloads
    'AUTO_SCALE':              True,
    'TEMP_MIN':                -5,
    'TEMP_MAX':                105,
    'HISTORY_YEARS':           10,
    'CLOCK_TWENTYFOUR':        False,
    'CLOCK_DELIMITER':         ':',
    'AP_SSID':                 'WP',
    'AP_PASSWORD':             None,    # None = open access-point network
    'FORCE_PORTAL':            False,   # for debug/testing only
}

BOOL_KEYS = ('SWAP_GREEN_BLUE', 'RELOAD_ON_ERROR', 'CLOCK_TWENTYFOUR', 'FORCE_PORTAL',
             'AUTO_SCALE')
INT_KEYS  = ('TEMP_MIN', 'TEMP_MAX', 'HISTORY_YEARS')
SECRETS   = {'CIRCUITPY_WIFI_PASSWORD'}


def coerce_config(cfg, bool_keys=BOOL_KEYS, int_keys=INT_KEYS):
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
