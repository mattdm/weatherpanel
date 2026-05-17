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

``COLOR_DEFAULTS`` — baseline color values (all as ``int`` RGB hex); callers
  overlay with values from colors.toml.

``COLOR_KEYS`` — ordered tuple of all color key names.

``load_colors(path)`` — reads a colors.toml file and returns a dict of color
  overrides merged with ``COLOR_DEFAULTS``.  Safe to call when the file is
  absent or partially populated — missing keys fall back to defaults.
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


COLOR_DEFAULTS = {
    # Temperature gradient — three anchor colors; intermediate steps are generated
    # automatically using HSL interpolation with smoothstep easing.
    'TEMP_COLOR_COLD':   0x143cd2,  # extreme cold — deep blue
    'TEMP_COLOR_CENTER': 0xeeeeee,  # at historical average — near-white neutral
    'TEMP_COLOR_WARM':   0xff4800,  # extreme warm — deep orange

    # Comfort zone overlay (the 68–72 °F band on the forecast graph).
    'COMFORT_COLOR': 0x0a3c00,

    # Precipitation bars.
    'RAIN_COLOR_BRIGHT': 0x0000D0,  # heavy rain (>= QPF_HIGH_MM)
    'RAIN_COLOR_MID':    0x000070,  # moderate rain (>= QPF_MID_MM)
    'RAIN_COLOR_DIM':    0x000028,  # light rain (< QPF_MID_MM)
    'SNOW_COLOR_BRIGHT': 0x44bbdd,  # snow
    'SNOW_COLOR_DIM':    0x0d2830,  # trace/dim snow

    # Boot/query screen status labels.
    'STATUS_QUERY_COLOR':   0x4278ff,  # querying
    'STATUS_SUCCESS_COLOR': 0x42ff78,  # success
    'STATUS_FAILURE_COLOR': 0xff6a00,  # failure or error
    'STATUS_STALE_COLOR':   0x8000ff,  # stale data — same visual cue as uncertain clock

    # Clock time-sync confidence.
    'CLOCK_NORMAL_COLOR':    0xffffff,  # synced
    'CLOCK_ERROR_COLOR':     0xff0080,  # sync failure
    'CLOCK_UNCERTAIN_COLOR': 0x8000ff,  # not yet confirmed
}

COLOR_KEYS = tuple(COLOR_DEFAULTS.keys())


def load_settings(path="/settings.toml"):
    """Read settings.toml and return a dict of key → raw string value.

    Handles the two value formats that appear in settings.toml:

    - ``KEY = "value"``  — double-quoted string; returns the inner string.
    - ``KEY = integer``  — bare integer literal; returns it as a string,
      mirroring what CircuitPython's ``os.getenv()`` returns so that
      ``coerce_config()`` works identically on both platforms.

    Comments, blank lines, and unrecognized formats are silently skipped.
    Returns an empty dict when the file is absent or unreadable.
    """
    result = {}
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return result
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, rest = line.partition('=')
        key = key.strip()
        rest = rest.strip()
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            result[key] = rest[1:-1]
        elif rest.lstrip('-').isdigit():
            result[key] = rest  # bare integer — return as string
    return result


def load_colors(path="/colors.toml"):
    """Read colors.toml and return a color dict merged with COLOR_DEFAULTS.

    Parses ``KEY = "value"`` lines (double-quoted strings only — the format
    written by the portal and the template file).  Values must be hex integers
    in ``0xRRGGBB`` or ``#RRGGBB`` format; invalid or unrecognized lines are
    silently skipped and the default for that key is used instead.

    Safe to call when the file is absent — returns COLOR_DEFAULTS in that case.
    """
    colors = dict(COLOR_DEFAULTS)
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return colors
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, rest = line.partition('=')
        key = key.strip()
        rest = rest.strip()
        if key not in COLOR_DEFAULTS:
            continue
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            val_str = rest[1:-1].strip()
            # Accept both 0xRRGGBB and #RRGGBB formats.
            if val_str.startswith('#'):
                val_str = '0x' + val_str[1:]
            try:
                colors[key] = int(val_str, 0)
            except (ValueError, TypeError):
                pass  # keep default
    return colors


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
