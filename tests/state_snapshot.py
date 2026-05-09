"""Utilities for embedding and reading weatherpanel state in PNG metadata.

State from Station, Clock, Display, and StatusLED is collected into a
TOML-serializable dict, serialized with tomli_w, and stored in a PNG iTXt
chunk under the keyword 'weatherpanel:state'. Every test reference image and
simulator output frame is thereby self-describing.

Functions:
    snapshot_state()  -- build a dict from runtime objects
    make_png_info()   -- serialize the dict to a Pillow PngInfo
    read_state()      -- read and deserialize state from a saved PNG
"""
import tomllib

import tomli_w
from PIL import PngImagePlugin

_CHUNK_KEY = "weatherpanel:state"


def snapshot_state(station=None, clock=None, display=None, led=None,
                   hourly=None, historical=None):
    """Build a TOML-serializable state dict from the given objects.

    All parameters are optional — missing objects produce no section.
    None-valued fields within objects are also omitted.

    The ``hourly`` and ``historical`` kwargs override whatever ``station``
    would contribute for those sections, making it easy to pass synthetic
    lists from test code that constructs Hour objects directly rather than
    via a Station.

    Args:
        station:    A Station instance (provides station metadata, hourly,
                    and historical unless the explicit overrides are given).
        clock:      A Clock instance.
        display:    A Display instance.
        led:        A StatusLED instance that exposes a ``.color`` property
                    (e.g. ``statusled_sim.StatusLED``).
        hourly:     Explicit list of Hour-like objects; overrides station.hourly.
        historical: Explicit list of historical slot dicts (as stored in
                    station.historical, i.e. None entries allowed); overrides
                    station.historical.
    """
    state = {}

    # --- Station metadata -------------------------------------------------
    if station is not None:
        s = {}
        for attr in ('lat', 'lon', 'city', 'state', 'tz', 'station_id',
                     'hourly_updated', 'griddata_updated'):
            v = getattr(station, attr, None)
            if v is not None:
                s[attr] = str(v)
        if s:
            state['station'] = s

    # --- Historical baselines (station or explicit override) --------------
    hist_source = historical if historical is not None else (
        getattr(station, 'historical', None) if station is not None else None
    )
    if hist_source is not None:
        hist_list = []
        for i, slot in enumerate(hist_source):
            if slot is None:
                continue
            hist_list.append({
                'slot':     i,
                'date':     str(slot['date']),
                'low':      float(slot['low']),
                'ave_low':  float(slot['ave-low']),
                'ave_high': float(slot['ave-high']),
                'high':     float(slot['high']),
            })
        if hist_list:
            state['historical'] = hist_list

    # --- Hourly forecast (station or explicit override) -------------------
    hourly_source = hourly if hourly is not None else (
        getattr(station, 'hourly', None) if station is not None else None
    )
    if hourly_source is not None:
        hourly_list = []
        for h in hourly_source:
            entry = {}
            for attr in ('start', 'end', 'forecast'):
                v = getattr(h, attr, None)
                if v is not None:
                    entry[attr] = str(v)
            for attr in ('temperature', 'precipitation'):
                v = getattr(h, attr, None)
                if v is not None:
                    entry[attr] = int(v)
            v = getattr(h, 'snow_fraction', None)
            if v is not None:
                entry['snow_fraction'] = float(v)
            if entry:
                hourly_list.append(entry)
        if hourly_list:
            state['hourly'] = hourly_list

    # --- Clock ------------------------------------------------------------
    if clock is not None:
        c = {}
        for attr in ('tz',):
            v = getattr(clock, attr, None)
            if v:
                c[attr] = str(v)
        # isotime / today / pretty_time may raise if the timezone is unset
        for attr in ('isotime', 'today', 'pretty_time'):
            try:
                v = getattr(clock, attr, None)
                if v:
                    c[attr] = str(v)
            except Exception:
                pass
        if hasattr(clock, 'color'):
            c['color'] = int(clock.color)
        if hasattr(clock, 'twentyfour'):
            c['twentyfour'] = bool(clock.twentyfour)
        if c:
            state['clock'] = c

    # --- Display ----------------------------------------------------------
    if display is not None:
        d = {}
        if hasattr(display, 'temp_scale_range'):
            d['temp_scale_range'] = int(display.temp_scale_range)
        if hasattr(display, 'temp_midpoint'):
            d['temp_midpoint'] = int(display.temp_midpoint)
        if hasattr(display, 'timetemp_group'):
            d['timetemp_y'] = int(display.timetemp_group.y)
        if hasattr(display, 'status_group'):
            d['status_hidden'] = bool(display.status_group.hidden)
        if d:
            state['display'] = d

    # --- NeoPixel LED -----------------------------------------------------
    if led is not None:
        l = {}  # noqa: E741
        if hasattr(led, 'color'):
            l['color'] = list(int(c) for c in led.color)
        if hasattr(led, '_sticky'):
            l['sticky'] = bool(led._sticky)
        if l:
            state['led'] = l

    return state


def make_png_info(state_dict):
    """Serialize ``state_dict`` as TOML and return a ``PngInfo`` object.

    Pass the returned object as the ``pnginfo`` argument to ``Image.save()``.
    """
    toml_str = tomli_w.dumps(state_dict)
    info = PngImagePlugin.PngInfo()
    info.add_itxt(_CHUNK_KEY, toml_str, lang="", tkey="")
    return info


def read_state(path):
    """Read embedded TOML state from a PNG file.

    Returns the parsed state dict, or an empty dict if no metadata is present.
    """
    from PIL import Image
    with Image.open(path) as img:
        raw = img.text.get(_CHUNK_KEY) or img.info.get(_CHUNK_KEY, "")
    if not raw:
        return {}
    return tomllib.loads(raw)
