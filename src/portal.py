"""Wi-Fi configuration portal for initial setup and recovery.

Runs as an independent program path (invoked from code.py), completely
separate from the weather scheduler.  Owns its own matrix init, displayio
tree, and event loop.  Uses bit_depth=1 so the QR code is a clean
on/off signal with no PWM strobing -- cameras can scan it reliably.
"""
import displayio
import microcontroller
import socketpool
import storage
import supervisor
from time import sleep, monotonic
from watchdog import WatchDogMode

import adafruit_miniqr
from adafruit_httpserver import Server, Request, Response, GET, POST

from base_display import BaseDisplay
from appconfig import COLOR_DEFAULTS, load_settings
import network
import wifi

QR_BORDER_PX = 1
WATCHDOG_TIMEOUT_SECONDS = 60
CLIENT_CHECK_INTERVAL_S = 1  # how often to check stations_ap
SETUP_TIMEOUT_S = 60         # revert to URL QR if no browser activity
INTERSTITIAL_S = 1.5
AP_CYCLE_S = 30              # Wi-Fi retry interval (seconds) when credentials are configured
MAX_POST_BODY_BYTES = 1024   # calculated max: ~453 bytes (settings) + ~400 bytes (16 color fields) = ~853 bytes
SAVE_COUNTDOWN_S = 5         # seconds before reboot after saving settings
# Countdown palette sampled from the temperature scale (orange → neutral → blue).
# Index 0 = color for "5", index 4 = color for "1".
_COUNTDOWN_COLORS = [0xff6a00, 0xffa872, 0xeeeeee, 0x6f9dff, 0x174afd]

LABEL_WIFI = ["Scan", "for", "WiFi"]
LABEL_URL  = ["Link", "to", "Setup"]
LABEL_USB_WARNING = ["Edit", "CIRCUITPY", "settings", ".toml"]
USB_WARNING_COLOR = 0xFF8800  # warm orange — "attention needed", not "emergency"

# Palette indices
QR_BLACK = 0
QR_WHITE = 1


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

FIELD_TO_KEY = {
    "ssid":             "CIRCUITPY_WIFI_SSID",
    "password":         "CIRCUITPY_WIFI_PASSWORD",
    "lat":              "LATITUDE",
    "lon":              "LONGITUDE",
    "auto_scale":       "AUTO_SCALE",
    "temp_min":         "TEMP_MIN",
    "temp_max":         "TEMP_MAX",
    "history_years":    "HISTORY_YEARS",
    "swap_green_blue":  "SWAP_GREEN_BLUE",
    "clock_twentyfour": "CLOCK_TWENTYFOUR",
}

KEY_TO_FIELD = {v: k for k, v in FIELD_TO_KEY.items()}

# Canonical order for keys appended to a fresh or partial settings.toml.
# Keeps the file readable: credentials first, location, display tuning, then flags.
_PREFERRED_KEY_ORDER = (
    "CIRCUITPY_WIFI_SSID",
    "CIRCUITPY_WIFI_PASSWORD",
    "LATITUDE",
    "LONGITUDE",
    "AUTO_SCALE",
    "TEMP_MIN",
    "TEMP_MAX",
    "HISTORY_YEARS",
    "SWAP_GREEN_BLUE",
    "CLOCK_TWENTYFOUR",
)


COLORS_FIELD_TO_KEY = {
    "temp_color_cold":        "TEMP_COLOR_COLD",
    "temp_color_center":      "TEMP_COLOR_CENTER",
    "temp_color_warm":        "TEMP_COLOR_WARM",
    "comfort_color":          "COMFORT_COLOR",
    "rain_color_bright":      "RAIN_COLOR_BRIGHT",
    "rain_color_mid":         "RAIN_COLOR_MID",
    "rain_color_dim":         "RAIN_COLOR_DIM",
    "snow_color_bright":      "SNOW_COLOR_BRIGHT",
    "snow_color_dim":         "SNOW_COLOR_DIM",
    "status_query_color":     "STATUS_QUERY_COLOR",
    "status_success_color":   "STATUS_SUCCESS_COLOR",
    "status_failure_color":   "STATUS_FAILURE_COLOR",
    "status_stale_color":     "STATUS_STALE_COLOR",
    "clock_normal_color":     "CLOCK_NORMAL_COLOR",
    "clock_error_color":      "CLOCK_ERROR_COLOR",
    "clock_uncertain_color":  "CLOCK_UNCERTAIN_COLOR",
}

COLORS_KEY_TO_FIELD = {v: k for k, v in COLORS_FIELD_TO_KEY.items()}

_COLORS_KEY_ORDER = (
    "TEMP_COLOR_COLD",
    "TEMP_COLOR_CENTER",
    "TEMP_COLOR_WARM",
    "COMFORT_COLOR",
    "RAIN_COLOR_BRIGHT",
    "RAIN_COLOR_MID",
    "RAIN_COLOR_DIM",
    "SNOW_COLOR_BRIGHT",
    "SNOW_COLOR_DIM",
    "STATUS_QUERY_COLOR",
    "STATUS_SUCCESS_COLOR",
    "STATUS_FAILURE_COLOR",
    "STATUS_STALE_COLOR",
    "CLOCK_NORMAL_COLOR",
    "CLOCK_ERROR_COLOR",
    "CLOCK_UNCERTAIN_COLOR",
)


def _0x_to_html(val_str):
    """Convert a stored '0xrrggbb' color string to '#rrggbb' for HTML color inputs.

    Also accepts integer defaults — converts them via hex formatting.
    """
    if isinstance(val_str, int):
        return f"#{val_str:06x}"
    s = val_str.strip().lower()
    if s.startswith('0x'):
        s = s[2:]
    return '#' + s.zfill(6)


def _html_to_0x(html_color):
    """Convert a '#rrggbb' color from an HTML form field to '0xrrggbb' for storage."""
    s = html_color.strip()
    if s.startswith('#'):
        return '0x' + s[1:].lower()
    return s.lower()


def merge_settings(form_data, old_content):
    """Merge form field values into existing settings.toml text.

    Pure function — no I/O.  Updates ``KEY = "value"`` lines for each
    form field that has a non-empty value, preserves all other lines
    (comments, unrelated keys), and appends any keys not already present.
    Returns the new file content as a string.
    """
    updates = {}
    for field, key in FIELD_TO_KEY.items():
        val = (form_data.get(field) or "").strip()
        if val:
            updates[key] = val

    lines = old_content.splitlines(keepends=True)
    found = set()
    result = []
    for line in lines:
        stripped = line.strip()
        matched_key = None
        for key in updates:
            if stripped.startswith(key) and "=" in stripped:
                # Make sure we matched the whole key name, not a prefix.
                rest = stripped[len(key):].lstrip()
                if rest.startswith("="):
                    matched_key = key
                    break
        if matched_key:
            result.append(f'{matched_key} = "{_toml_escape(updates[matched_key])}"\n')
            found.add(matched_key)
        else:
            result.append(line)

    for key in _PREFERRED_KEY_ORDER:
        if key in updates and key not in found:
            result.append(f'{key} = "{_toml_escape(updates[key])}"\n')

    return "".join(result)


def save_settings(form_data, path="/settings.toml"):
    """Read settings.toml, merge form data, and write back.

    Uses ``storage.remount`` to temporarily make the filesystem writable.
    Raises ``RuntimeError`` (re-raised from ``storage.remount``) when the
    USB drive is mounted — the caller should catch this and return an error
    page instructing the user to disconnect USB.

    Skips the write entirely when the merged content is identical to the
    original, avoiding unnecessary flash wear.

    Note: the write is not atomic — CircuitPython's filesystem does not
    support rename-over, so a power loss mid-write can corrupt settings.toml.

    Returns the merged content string.
    """
    try:
        with open(path) as f:
            old_content = f.read()
    except OSError:
        old_content = ""

    new_content = merge_settings(form_data, old_content)

    if new_content == old_content:
        return new_content

    storage.remount("/", readonly=False)
    with open(path, "w") as f:
        f.write(new_content)
    storage.remount("/", readonly=True)

    return new_content


def merge_colors(form_data, old_content):
    """Merge color form field values into existing colors.toml text.

    Pure function — no I/O.  Operates like merge_settings but uses
    COLORS_FIELD_TO_KEY and _COLORS_KEY_ORDER.  Form values arrive as
    '#rrggbb' (HTML color input format) and are normalised to '0xrrggbb'
    before writing.

    Returns the new file content as a string.
    """
    updates = {}
    for field, key in COLORS_FIELD_TO_KEY.items():
        val = (form_data.get(field) or "").strip()
        if val:
            updates[key] = _html_to_0x(val)

    lines = old_content.splitlines(keepends=True)
    found = set()
    result = []
    for line in lines:
        stripped = line.strip()
        matched_key = None
        for key in updates:
            if stripped.startswith(key) and "=" in stripped:
                rest = stripped[len(key):].lstrip()
                if rest.startswith("="):
                    matched_key = key
                    break
        if matched_key:
            result.append(f'{matched_key} = "{_toml_escape(updates[matched_key])}"\n')
            found.add(matched_key)
        else:
            result.append(line)

    for key in _COLORS_KEY_ORDER:
        if key in updates and key not in found:
            result.append(f'{key} = "{_toml_escape(updates[key])}"\n')

    return "".join(result)


def save_all(settings_form_data, colors_form_data,
             settings_path="/settings.toml", colors_path="/colors.toml"):
    """Save settings and colors in a single filesystem remount.

    Computes both merged files before mounting, then writes only the files
    whose content actually changed — minimising flash wear and the writable
    window.  Raises ``RuntimeError`` when USB is connected (same as
    ``save_settings``).

    Returns the merged settings.toml content string.
    """
    try:
        with open(settings_path) as f:
            old_settings = f.read()
    except OSError:
        old_settings = ""

    try:
        with open(colors_path) as f:
            old_colors = f.read()
    except OSError:
        old_colors = ""

    new_settings = merge_settings(settings_form_data, old_settings)
    new_colors = merge_colors(colors_form_data, old_colors)

    settings_changed = new_settings != old_settings
    colors_changed   = new_colors   != old_colors

    if not settings_changed and not colors_changed:
        return new_settings

    storage.remount("/", readonly=False)
    if settings_changed:
        with open(settings_path, "w") as f:
            f.write(new_settings)
    if colors_changed:
        with open(colors_path, "w") as f:
            f.write(new_colors)
    storage.remount("/", readonly=True)

    return new_settings


# ---------------------------------------------------------------------------
# URL decoding
# ---------------------------------------------------------------------------

def _url_decode(s):
    """Decode an application/x-www-form-urlencoded percent-encoded string.

    Handles ``+`` → space and ``%XX`` → character.  adafruit_httpserver does
    not URL-decode form values, so browsers send ``#`` as ``%23``, ``"`` as
    ``%22``, ``&`` as ``%26``, etc.  This function recovers the original
    characters before they are validated and written to settings.toml.

    Invalid ``%`` sequences (truncated or non-hex) are passed through
    unchanged.
    """
    s = s.replace('+', ' ')
    parts = s.split('%')
    result = [parts[0]]
    for part in parts[1:]:
        if len(part) >= 2:
            try:
                result.append(chr(int(part[:2], 16)))
                result.append(part[2:])
            except ValueError:
                result.append('%')
                result.append(part)
        else:
            result.append('%')
            result.append(part)
    return ''.join(result)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _toml_escape(s):
    """Escape a string for use inside a TOML basic (double-quoted) string.

    Escapes ``"`` and ``\\`` as required by the TOML spec, and encodes all
    control characters (including newlines and null bytes) so they cannot
    inject additional TOML keys or corrupt the settings file.
    """
    out = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\b':
            out.append('\\b')
        elif ch == '\f':
            out.append('\\f')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f'\\u{ord(ch):04x}')
        else:
            out.append(ch)
    return ''.join(out)


def _has_control_chars(s):
    """Return True if ``s`` contains any control character (< 0x20 or 0x7F).

    Control characters have no place in WiFi credentials and could indicate
    an injection attempt.
    """
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in s)


def _validate_form_data(form_data):
    """Validate POST form data for safety and correctness.

    Returns a ``dict`` mapping field names to error message strings.
    An empty dict means all submitted fields are acceptable.

    Validation is server-side (cannot be bypassed by disabling JavaScript).
    """
    errors = {}

    ssid = (form_data.get('ssid') or '').strip()
    password = (form_data.get('password') or '').strip()
    lat = (form_data.get('lat') or '').strip()
    lon = (form_data.get('lon') or '').strip()

    if not ssid:
        errors['ssid'] = 'Network name is required.'
    elif len(ssid.encode('utf-8')) > 32:
        errors['ssid'] = 'Network name must be 32 bytes or fewer (802.11 limit).'
    elif _has_control_chars(ssid):
        errors['ssid'] = 'Network name contains invalid control characters.'

    if password:
        if len(password) < 8:
            errors['password'] = 'WPA2 password must be at least 8 characters.'
        elif len(password) > 63:
            errors['password'] = 'WPA2 password must be 63 characters or fewer.'
        elif _has_control_chars(password):
            errors['password'] = 'Password contains invalid control characters.'

    if not lat:
        errors['lat'] = 'Latitude is required.'
    else:
        try:
            lat_f = float(lat)
        except ValueError:
            errors['lat'] = 'Latitude must be a number.'
        else:
            if not (17.0 <= lat_f <= 72.0):
                errors['lat'] = 'Latitude must be between 17 and 72 (US coverage area).'

    if not lon:
        errors['lon'] = 'Longitude is required.'
    else:
        try:
            lon_f = float(lon)
        except ValueError:
            errors['lon'] = 'Longitude must be a number.'
        else:
            if not (-180.0 <= lon_f <= -64.0):
                errors['lon'] = 'Longitude must be between -180 and -64 (US coverage area).'

    for field, label, lo_bound, hi_bound in (
        ('temp_min',      'Minimum temperature',       -100, 149),
        ('temp_max',      'Maximum temperature',        -99, 150),
        # history_years max 45: PRISM daily data begins 1981; 2026-1981=45
        ('history_years', 'Historical baseline years',    1,  45),
    ):
        val = (form_data.get(field) or '').strip()
        if val:
            try:
                v = int(val)
            except ValueError:
                errors[field] = f'{label} must be a whole number.'
            else:
                if not (lo_bound <= v <= hi_bound):
                    errors[field] = f'{label} must be between {lo_bound} and {hi_bound}.'

    # temp_max - temp_min max 200: Montana's all-time swing is 187°F (widest in the US)
    temp_min_val = (form_data.get('temp_min') or '').strip()
    temp_max_val = (form_data.get('temp_max') or '').strip()
    if temp_min_val and temp_max_val and 'temp_min' not in errors and 'temp_max' not in errors:
        span = int(temp_max_val) - int(temp_min_val)
        if span < 32:
            errors['temp_max'] = 'Maximum temperature must be at least 32°F above minimum (1°F per pixel).'
        elif span > 200:
            errors['temp_max'] = 'Temperature range (max − min) must not exceed 200°F.'

    for field, label in (
        ('auto_scale',       'Auto scale'),
        ('swap_green_blue',  'Green/blue panel swap'),
        ('clock_twentyfour', '24-hour clock'),
    ):
        val = (form_data.get(field) or '').strip()
        if val and val not in ('0', '1'):
            errors[field] = f'{label} must be 0 or 1.'

    for field in COLORS_FIELD_TO_KEY:
        val = (form_data.get(field) or '').strip()
        if val:
            # Accept '#rrggbb' (HTML color input) or '0xrrggbb' (manual entry).
            s = val.lower()
            if s.startswith('#'):
                hex_part = s[1:]
            elif s.startswith('0x'):
                hex_part = s[2:]
            else:
                hex_part = ''
            if len(hex_part) != 6 or not all(c in '0123456789abcdef' for c in hex_part):
                errors[field] = f'{COLORS_FIELD_TO_KEY[field]} must be a 6-digit hex color (e.g. #143cd2).'

    return errors


def _validation_error_html(errors):
    """Return an error page listing all validation failures."""
    items = ''.join(f'<li>{_html_escape(msg)}</li>' for msg in errors.values())
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Error</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:2em auto;padding:0 1em}}.warn{{color:#c00}}</style>
</head>
<body>
<h2 class="warn">Please fix these errors</h2>
<ul>{items}</ul>
<p><a href="/">&#8592; Back to form</a></p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# QR data helpers
# ---------------------------------------------------------------------------

def _wifi_escape(s):
    """Escape special characters in a WIFI: QR URI field value.

    The WIFI: URI format (used by iOS and Android QR scanning) requires
    backslash, semicolon, comma, double-quote, and colon to be escaped
    with a backslash.
    """
    for ch in ('\\', ';', ',', '"', ':'):
        s = s.replace(ch, '\\' + ch)
    return s


def wifi_qr_data(ssid, password=None):
    """Build a Wi-Fi QR code data string.

    Uses the de-facto ``WIFI:`` URI scheme recognised by iOS and Android
    camera apps.  Special characters in the SSID and password are escaped
    per the format spec.  Returns the raw string (caller encodes to bytes
    for the QR library).
    """
    ssid = _wifi_escape(ssid)
    if password:
        return f"WIFI:T:WPA;S:{ssid};P:{_wifi_escape(password)};;"
    return f"WIFI:T:nopass;S:{ssid};;"


def url_qr_data(ip):
    """Build a URL QR code data string pointing at the portal web server.

    Port 80 is included explicitly to prevent browsers from redirecting
    to HTTPS.
    """
    return f"http://{ip}:80"


def make_qr_bitmap(data):
    """Generate a monochrome ``displayio.Bitmap`` from a data string.

    Uses QR Version 2 (25×25 modules) + a 1-pixel border = 27×27 bitmap,
    which fits the 32-pixel display height.  Error correction L supports
    up to 26 bytes.  Index 0 = dark module (QR_BLACK), index 1 = light
    module (QR_WHITE).
    """
    qr = adafruit_miniqr.QRCode(qr_type=2, error_correct=adafruit_miniqr.L)
    qr.add_data(data.encode("utf-8"))
    qr.make()

    mat = qr.matrix
    size = mat.width + 2 * QR_BORDER_PX
    bitmap = displayio.Bitmap(size, size, 2)

    for y in range(size):
        for x in range(size):
            bitmap[x, y] = QR_WHITE

    for y in range(mat.height):
        for x in range(mat.width):
            bitmap[x + QR_BORDER_PX, y + QR_BORDER_PX] = QR_BLACK if mat[x, y] else QR_WHITE

    return bitmap


# ---------------------------------------------------------------------------
# Web server helpers
# ---------------------------------------------------------------------------

def _html_escape(s):
    """Minimal HTML escaping for attribute values and text content."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _ssid_options(networks, configured_ssid=None):
    """Return HTML <option> elements for a list of (ssid, rssi) tuples.

    If ``configured_ssid`` is given and is not present in ``networks``, it is
    prepended as the selected option with a ``(not visible)`` marker so the
    user knows their saved network is out of range or hidden.
    """
    scan_ssids = {ssid for ssid, _ in networks}
    out = []
    if configured_ssid and configured_ssid not in scan_ssids:
        esc = _html_escape(configured_ssid)
        out.append(f'<option value="{esc}" selected>(not visible) {esc}</option>')
    for ssid, rssi in networks:
        selected = ' selected' if ssid == configured_ssid else ''
        out.append(
            f'<option value="{_html_escape(ssid)}"{selected}>'
            f'{_html_escape(ssid)} ({rssi} dBm)</option>'
        )
    return "".join(out)


def _field_attrs(field, current_values, config_errors):
    """Return ``(value_str, error_str, style_attr)`` for a form field.

    ``value_str`` is the current saved value (empty string if absent).
    ``error_str`` is the config-error message for this field (empty if none).
    ``style_attr`` is a non-empty inline style string when there's an error,
    ready to drop into an HTML attribute.
    """
    val = _html_escape(current_values.get(field, ''))
    err = config_errors.get(field, '')
    style = ' style="outline:2px solid #c00"' if err else ''
    return val, _html_escape(err), style


def _form_html(networks, current_values=None, config_errors=None, current_colors=None):
    """Return the full config form HTML page.

    ``current_values`` is a dict of ``{field_name: raw_value_string}`` used to
    pre-populate the form with existing saved settings.

    ``config_errors`` is a dict of ``{field_name: error_message}`` for values
    that failed type coercion at startup — shown as a banner plus inline marks.

    ``current_colors`` is a dict of ``{field_name: raw_value_string}`` from
    colors.toml, used to pre-populate the color picker inputs.
    """
    if current_values is None:
        current_values = {}
    if config_errors is None:
        config_errors = {}
    if current_colors is None:
        current_colors = {}

    configured_ssid = current_values.get('ssid') or None
    options = _ssid_options(networks, configured_ssid)

    # Warning shown below the SSID dropdown when the saved network isn't visible.
    ssid_warning = ''
    if configured_ssid and configured_ssid not in {s for s, _ in networks}:
        esc = _html_escape(configured_ssid)
        ssid_warning = (
            f'<p class="warn">Your saved network \u201c{esc}\u201d is not visible. '
            'It may be out of range or hidden.</p>'
        )

    # Red banner listing startup config errors.
    banner = ''
    if config_errors:
        items = ''.join(
            f'<li>{_html_escape(msg)}</li>'
            for msg in config_errors.values()
        )
        banner = (
            '<div class="banner">'
            '<strong>Settings have errors \u2014 please correct them below:</strong>'
            f'<ul>{items}</ul></div>'
        )

    lat_val, _, lat_style = _field_attrs('lat', current_values, config_errors)
    lon_val, _, lon_style = _field_attrs('lon', current_values, config_errors)

    temp_min_val, temp_min_err, temp_min_style = _field_attrs('temp_min', current_values, config_errors)
    temp_max_val, temp_max_err, temp_max_style = _field_attrs('temp_max', current_values, config_errors)
    hist_val, hist_err, hist_style = _field_attrs('history_years', current_values, config_errors)

    temp_min_default = temp_min_val or '-5'
    temp_max_default = temp_max_val or '105'
    hist_default = hist_val or '10'

    # AUTO_SCALE defaults to True — checked unless explicitly saved as "0".
    auto_scale_checked = ' checked' if current_values.get('auto_scale', '1') != '0' else ''
    swap_checked = ' checked' if current_values.get('swap_green_blue') == '1' else ''
    clock24_checked = ' checked' if current_values.get('clock_twentyfour') == '1' else ''

    # Open Advanced section automatically if there are errors in those fields.
    adv_open = ' open' if any(f in config_errors for f in (
        'auto_scale', 'temp_min', 'temp_max', 'history_years')) else ''

    # Open Colors section automatically if there are errors in any color field.
    col_open = ' open' if any(f in config_errors for f in COLORS_FIELD_TO_KEY) else ''

    # Build '#rrggbb' strings for each color input, falling back to COLOR_DEFAULTS.
    def _cv(field):
        key = COLORS_FIELD_TO_KEY[field]
        raw = current_colors.get(field)
        if raw:
            return _0x_to_html(raw)
        return _0x_to_html(COLOR_DEFAULTS.get(key, 0))

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WeatherPanel Setup</title>
<style>
body{{font-family:sans-serif;max-width:480px;margin:2em auto;padding:0 1em}}
label{{display:block;margin-top:1em}}
input,select{{width:100%;padding:.4em;box-sizing:border-box}}
.hint{{color:#666;font-size:.85em}}
.err{{color:#c00;font-size:.85em;display:block;margin-top:.2em}}
.warn{{color:#c00;font-size:.9em}}
.banner{{background:#fff0f0;border:1px solid #c00;border-radius:4px;padding:.6em 1em;margin-bottom:1em}}
.banner ul{{margin:.3em 0 0;padding-left:1.4em}}
.pw-row{{display:flex;gap:.4em}}
.pw-row input{{flex:1}}
.pw-toggle{{white-space:nowrap;padding:.4em .8em;margin-top:0;width:auto;font-size:.9em}}
button{{margin-top:1.5em;width:100%;padding:.6em;font-size:1em}}
details{{margin-top:1.5em}}summary{{cursor:pointer;color:#444}}
input[type=checkbox]{{width:auto;padding:0;margin:0}}
.cb-label{{display:flex;align-items:center;gap:.6em;cursor:pointer}}
.color-row{{display:flex;align-items:center;gap:.7em;margin-top:.6em}}
input[type=color]{{width:2.5em;height:2em;padding:.1em;flex-shrink:0;cursor:pointer;border:1px solid #ccc}}
.color-group{{font-weight:bold;margin-top:1.2em;margin-bottom:0}}
</style>
</head>
<body>
<h2>WeatherPanel Setup</h2>
{banner}
<form method="POST" action="/">
<label>Wi-Fi network
<div class="pw-row">
<select name="ssid" id="ssid" style="flex:1">
{options}
</select>
<button type="button" class="pw-toggle"
  onclick="this.textContent='...';fetch('/scan').then(r=>r.text()).then(h=>{{document.getElementById('ssid').innerHTML=h;this.textContent='Scan'}})">Scan</button>
</div></label>
{ssid_warning}
<label>Password <span class="hint">(leave blank to keep current)</span>
<div class="pw-row">
<input type="password" name="password" id="pw" oninput="_vPw(this.value)">
<button type="button" class="pw-toggle"
  onclick="var i=document.getElementById('pw');i.type=i.type=='password'?'text':'password';this.textContent=i.type=='password'?'Show':'Hide'">Show</button>
</div>
<span id="pw-e" class="err"></span></label>
<label>Latitude <span class="hint">(required)</span>
<input type="text" name="lat" id="lat" placeholder="e.g. 42.39" value="{lat_val}"{lat_style} oninput="_vLat(this.value)">
<span id="lat-e" class="err"></span></label>
<label>Longitude <span class="hint">(required)</span>
<input type="text" name="lon" id="lon" placeholder="e.g. -71.13" value="{lon_val}"{lon_style} oninput="_vLon(this.value)">
<span id="lon-e" class="err"></span></label>
<p class="hint">Find your coordinates: open <a href="https://www.openstreetmap.org" target="_blank">OpenStreetMap</a>, click \u201cShow My Location\u201d, and read the numbers from the URL \u2014 it will look like <code>openstreetmap.org/#map=14/<em>lat</em>/<em>lon</em></code>.</p>
<details{adv_open}>
<summary>Advanced</summary>
<label class="cb-label"><input type="checkbox" name="auto_scale" id="auto_scale" value="1" onchange="_vAutoScale(this.checked)"{auto_scale_checked}><input type="hidden" name="auto_scale" value="0">
Auto scale <span class="hint">(query ACIS for all-time high/low at startup \u2014 ignores min/max below)</span></label>
<label>Minimum temperature (\u00b0F) <span class="hint">(bottom of the color scale)</span>
<input type="number" name="temp_min" id="temp_min" placeholder="-5" value="{temp_min_default}"{temp_min_style}>
<span class="err">{temp_min_err}</span></label>
<label>Maximum temperature (\u00b0F) <span class="hint">(top of the color scale)</span>
<input type="number" name="temp_max" id="temp_max" placeholder="105" value="{temp_max_default}"{temp_max_style}>
<span class="err">{temp_max_err}</span></label>
<label>Historical baseline years <span class="hint">(years of PRISM climate data for record/average temps)</span>
<input type="number" name="history_years" placeholder="10" value="{hist_default}" min="1"{hist_style}>
<span class="err">{hist_err}</span></label>
<label class="cb-label"><input type="checkbox" name="swap_green_blue" value="1"{swap_checked}><input type="hidden" name="swap_green_blue" value="0">
Green/blue panel swap <span class="hint">(enable if panel colors look reversed)</span></label>
<label class="cb-label"><input type="checkbox" name="clock_twentyfour" value="1"{clock24_checked}><input type="hidden" name="clock_twentyfour" value="0">
24-hour clock</label>
</details>
<details{col_open}>
<summary>Colors</summary>
<p class="hint">Customize the colors used on the panel display. Saved to colors.toml.</p>
<p class="color-group">Temperature gradient</p>
<label class="color-row"><input type="color" name="temp_color_cold" value="{_cv('temp_color_cold')}">
Extreme cold <span class="hint">— at all-time record lows</span></label>
<label class="color-row"><input type="color" name="temp_color_center" value="{_cv('temp_color_center')}">
Average <span class="hint">— within normal historical range</span></label>
<label class="color-row"><input type="color" name="temp_color_warm" value="{_cv('temp_color_warm')}">
Extreme warm <span class="hint">— at all-time record highs</span></label>
<label class="color-row"><input type="color" name="comfort_color" value="{_cv('comfort_color')}">
Comfort zone band <span class="hint">— 68–72 \u00b0F overlay</span></label>
<p class="color-group">Precipitation</p>
<label class="color-row"><input type="color" name="rain_color_bright" value="{_cv('rain_color_bright')}">Heavy rain</label>
<label class="color-row"><input type="color" name="rain_color_mid" value="{_cv('rain_color_mid')}">Moderate rain</label>
<label class="color-row"><input type="color" name="rain_color_dim" value="{_cv('rain_color_dim')}">Light rain</label>
<label class="color-row"><input type="color" name="snow_color_bright" value="{_cv('snow_color_bright')}">Snow</label>
<label class="color-row"><input type="color" name="snow_color_dim" value="{_cv('snow_color_dim')}">Trace snow</label>
<p class="color-group">Status labels</p>
<label class="color-row"><input type="color" name="status_query_color" value="{_cv('status_query_color')}">
Querying <span class="hint">— fetching data at startup</span></label>
<label class="color-row"><input type="color" name="status_success_color" value="{_cv('status_success_color')}">Success</label>
<label class="color-row"><input type="color" name="status_failure_color" value="{_cv('status_failure_color')}">Failure / error</label>
<label class="color-row"><input type="color" name="status_stale_color" value="{_cv('status_stale_color')}">
Stale data <span class="hint">— hourly data not recently refreshed</span></label>
<p class="color-group">Clock</p>
<label class="color-row"><input type="color" name="clock_normal_color" value="{_cv('clock_normal_color')}">Synced</label>
<label class="color-row"><input type="color" name="clock_error_color" value="{_cv('clock_error_color')}">Sync error</label>
<label class="color-row"><input type="color" name="clock_uncertain_color" value="{_cv('clock_uncertain_color')}">
Uncertain <span class="hint">— timezone not yet confirmed</span></label>
</details>
<button type="submit">Save &amp; Connect</button>
</form>
<script>
function _ve(id,msg){{var e=document.getElementById(id+'-e');if(e)e.textContent=msg;var i=document.getElementById(id);if(i)i.style.outline=msg?'2px solid #c00':''}}
function _vPw(v){{_ve('pw',v.length>0&&v.length<8?'WPA2 needs 8+ chars.':v.length>63?'Max 63 chars.':'')}}
function _vLat(v){{if(!v)return _ve('lat','Required.');var n=parseFloat(v);_ve('lat',isNaN(n)?'Must be a number.':n<17||n>72?'Outside US range (17\u201372)':'')}}
function _vLon(v){{if(!v)return _ve('lon','Required.');var n=parseFloat(v);_ve('lon',isNaN(n)?'Must be a number.':n<-180||n>-64?'Outside US range (-180 to -64)':'')}}
function _vAutoScale(c){{var mn=document.getElementById('temp_min'),mx=document.getElementById('temp_max');mn.disabled=c;mx.disabled=c;}}
_vAutoScale(document.getElementById('auto_scale').checked);
</script>
</body>
</html>"""


_PASSWORD_MASK = "\u00b7" * 10  # fixed-length mask; does not leak actual password length


def _mask_password(content):
    """Replace the CIRCUITPY_WIFI_PASSWORD value in settings.toml text with bullets.

    Operates line-by-line so only the password key is affected; all other
    lines are preserved verbatim.  Uses a fixed-length mask regardless of the
    actual password length so the display is safe to share or screenshot.
    """
    lines = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("CIRCUITPY_WIFI_PASSWORD") and "=" in stripped:
            rest = stripped[len("CIRCUITPY_WIFI_PASSWORD"):].lstrip()
            if rest.startswith("="):
                lines.append(f'CIRCUITPY_WIFI_PASSWORD = "{_PASSWORD_MASK}"\n')
                continue
        lines.append(line)
    return "".join(lines)


def _success_html(content):
    """Return the brief success page shown after settings are saved.

    ``content`` is the full text of the written settings file, displayed
    verbatim so the user can confirm what was persisted.  The Wi-Fi password
    value is replaced with a fixed-length bullet mask regardless of the actual
    password length, so the page is safe to share or screenshot.
    """
    masked = _mask_password(content)
    safe = masked.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Settings saved</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:4em auto}}
pre{{background:#f4f4f4;padding:1em;overflow-x:auto;font-size:.85em}}
.hint{{color:#666;font-size:.9em}}</style>
</head>
<body>
<h2>Settings saved!</h2>
<p>WeatherPanel is restarting. Reconnect to your Wi-Fi network to continue.</p>
<pre><code>{safe}</code></pre>
<p class="hint">To reconfigure, press Reset and hold Up or Down while the panel restarts.</p>
</body>
</html>"""


def _usb_error_html():
    """Return the error page shown when storage.remount raises RuntimeError."""
    return """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Cannot save</title>
<style>body{font-family:sans-serif;max-width:480px;margin:4em auto;text-align:center}
.warn{color:#c00}</style>
</head>
<body>
<h2 class="warn">Cannot save</h2>
<p>The WeatherPanel must be plugged into a USB power supply, not a computer.
Eject the CIRCUITPY drive, then try submitting the form again.</p>
<p>Or, instead of using the web configuration interface, look at the
CIRCUITPY drive and edit <code>settings.toml</code> directly.</p>
</body>
</html>"""


def _make_server(ip, initial_networks, current_values=None, config_errors=None,
                 current_colors=None):
    """Create and start the HTTP server bound to all interfaces.

    ``initial_networks`` is a pre-scanned list of (ssid, rssi) tuples used
    for the initial form render — scanning inside a request handler can
    interfere with the AP radio and drop the client connection.

    ``current_values`` is a dict of ``{field_name: value}`` pre-read from
    settings.toml, used to pre-populate the form.

    ``config_errors`` is a dict of ``{field_name: message}`` for values that
    failed type coercion at startup, shown as a banner in the initial form.
    Config errors are cleared after the first render — they were for the
    old settings, not for a fresh submission.

    ``current_colors`` is a dict of ``{field_name: value}`` pre-read from
    colors.toml, used to pre-populate the color picker inputs.

    Returns ``(server, state)`` where ``state['last_request_t']`` is updated
    on each incoming request so the main loop can track browser activity.
    """
    pool = socketpool.SocketPool(wifi.radio)
    server = Server(pool)

    _networks = [initial_networks]
    _current_values = [current_values or {}]
    _config_errors = [config_errors or {}]
    _current_colors = [current_colors or {}]
    state = {'last_request_t': 0.0}

    @server.route("/", GET)
    def index(request: Request):
        state['last_request_t'] = monotonic()
        html = _form_html(_networks[0], _current_values[0], _config_errors[0],
                          _current_colors[0])
        _config_errors[0] = {}  # clear after first render
        return Response(request, html, content_type="text/html")

    @server.route("/scan", GET)
    def scan(request: Request):
        state['last_request_t'] = monotonic()
        _networks[0] = network.scan_networks()
        configured_ssid = _current_values[0].get('ssid') or None
        return Response(request, _ssid_options(_networks[0], configured_ssid), content_type="text/html")

    @server.route("/", POST)
    def submit(request: Request):
        state['last_request_t'] = monotonic()
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_POST_BODY_BYTES:
            return Response(request, "Request too large", status=413)
        # Decode percent-encoding: adafruit_httpserver does not URL-decode
        # form values, so '#' arrives as '%23', '"' as '%22', etc.
        # Use safe=False to skip the library's HTML-entity pass — we are
        # routing values to _toml_escape, not to HTML output.
        raw = request.form_data
        form_data = {
            field: _url_decode(raw.get(field, safe=False) or "")
            for field in FIELD_TO_KEY
        }
        colors_data = {
            field: _url_decode(raw.get(field, safe=False) or "")
            for field in COLORS_FIELD_TO_KEY
        }
        all_data = dict(form_data)
        all_data.update(colors_data)
        errors = _validate_form_data(all_data)
        if errors:
            return Response(request, _validation_error_html(errors), content_type="text/html")
        try:
            content = save_all(form_data, colors_data)
        except RuntimeError:
            return Response(request, _usb_error_html(), content_type="text/html")
        state['reload_pending'] = True
        return Response(request, _success_html(content), content_type="text/html")

    server.start("0.0.0.0", port=80)
    print(f"HTTP server running at http://{ip}:80")
    return server, state


# ---------------------------------------------------------------------------
# Portal display class
# ---------------------------------------------------------------------------

# Maximum number of side labels on the QR screen.
_MAX_QR_LABEL_LINES = max(len(LABEL_WIFI), len(LABEL_URL))  # 3
# QR Version 2: 25×25 modules + 1-pixel border each side = 27×27 pixels.
# This is the only size that both fits the 32-pixel display height and is
# reliably scannable by phone cameras on this hardware.
_QR_SIZE = 25 + 2 * QR_BORDER_PX  # 27


class PortalDisplay(BaseDisplay):
    """Owns the portal's displayio tree and all screen transitions.

    Uses ``bit_depth=1`` for monochrome rendering — required so QR codes
    are clean on/off signals with no PWM strobing, allowing cameras to scan
    them reliably.

    All displayio objects are pre-allocated in ``__init__``.  Screen
    transitions toggle ``hidden`` on two persistent groups — the inherited
    text group and a QR group — rather than tearing down and rebuilding
    the root group.

    Public screen constants (``SCREEN_*``) identify the current logical
    screen.  ``self.screen`` is updated by every public screen method.
    """

    SCREEN_USB_WARNING = "usb_warning"
    SCREEN_SETUP_INTRO = "setup_intro"
    SCREEN_WIFI_QR     = "wifi_qr"
    SCREEN_CONNECTED   = "connected"
    SCREEN_URL_QR      = "url_qr"
    SCREEN_IN_SETUP    = "in_setup"
    SCREEN_COUNTDOWN   = "countdown"

    def __init__(self, config):
        """Initialize matrix (bit_depth=1), font, and all pre-allocated display objects."""
        super().__init__(config, bit_depth=1)

        # Portal text labels use a 1px left margin so text doesn't sit flush
        # against the left edge of the monochrome display.
        for lbl in self._text_labels:
            lbl.x = 1

        # --- QR group -----------------------------------------------------
        # Pre-allocated backing bitmap, palette, TileGrid, and side labels.
        # show_wifi_qr()/show_url_qr() copy pixels in and update label text
        # in place — no new objects are allocated on each transition.
        self._qr_palette = displayio.Palette(2)
        self._qr_palette[QR_BLACK] = 0x000000
        self._qr_palette[QR_WHITE] = 0xFFFFFF
        self._qr_backing_bitmap = displayio.Bitmap(_QR_SIZE, _QR_SIZE, 2)
        self._qr_grid = displayio.TileGrid(
            self._qr_backing_bitmap,
            pixel_shader=self._qr_palette,
            tile_width=_QR_SIZE,
            tile_height=_QR_SIZE,
            x=1,
            y=(self.DISPLAY_HEIGHT - _QR_SIZE) // 2,
        )
        _label_x = _QR_SIZE + 3
        _start_y, _line_height = self._vcenter_y(_MAX_QR_LABEL_LINES)
        self._qr_labels = [
            self._make_label(x=_label_x, y=_start_y + i * _line_height)
            for i in range(_MAX_QR_LABEL_LINES)
        ]
        self._qr_group = displayio.Group()
        self._qr_group.append(self._qr_grid)
        for lbl in self._qr_labels:
            self._qr_group.append(lbl)

        # Both groups live in the root group permanently; only hidden toggles.
        self.root_group.append(self._status_group)
        self.root_group.append(self._qr_group)

        # Initial state — the first call in run() is always show_setup_intro()
        # or show_usb_warning(), both of which show the text group.
        self.screen = self.SCREEN_SETUP_INTRO
        self._status_group.hidden = False
        self._qr_group.hidden   = True

    # ------------------------------------------------------------------
    # Private rendering helpers
    # ------------------------------------------------------------------

    def _show_text(self, lines, color=0xFFFFFF, colors=None):
        """Assign content to the 4 fixed text slots, hide the QR group, and flush."""
        super()._show_text(lines, color=color, colors=colors)
        self._qr_group.hidden = True
        self.flush()

    def _show_qr(self, source_bitmap, label_lines):
        """Copy QR pixels into the backing bitmap, update side labels, and flush."""
        for y in range(_QR_SIZE):
            for x in range(_QR_SIZE):
                self._qr_backing_bitmap[x, y] = source_bitmap[x, y]

        for i, lbl in enumerate(self._qr_labels):
            lbl.text = label_lines[i] if i < len(label_lines) else ""

        self._qr_group.hidden    = False
        self._status_group.hidden = True
        self.flush()

    # ------------------------------------------------------------------
    # Public screen-switch methods
    # ------------------------------------------------------------------

    def show_usb_warning(self):
        """Show the USB-connected warning (edit settings.toml directly)."""
        self.screen = self.SCREEN_USB_WARNING
        # LABEL_USB_WARNING has exactly 4 items — fills all slots.
        self._show_text(LABEL_USB_WARNING, color=USB_WARNING_COLOR)

    def show_setup_intro(self, lines=None):
        """Show the setup interstitial.

        ``lines`` overrides the default four-slot text.  Pass a custom list
        to show a different message — e.g. ``["", "Wi-Fi", "failed", ""]``
        in recovery mode.  Defaults to ``["", "Weather", "Panel", "Setup"]``.
        """
        self.screen = self.SCREEN_SETUP_INTRO
        self._show_text(lines if lines is not None else ["", "Weather", "Panel", "Setup"])

    def show_wifi_qr(self, source_bitmap):
        """Show the Wi-Fi AP QR code (scan to connect to the portal AP)."""
        self.screen = self.SCREEN_WIFI_QR
        self._show_qr(source_bitmap, LABEL_WIFI)

    def show_connected(self):
        """Show the "Connected!" interstitial when a client joins the AP."""
        self.screen = self.SCREEN_CONNECTED
        self._show_text(["", "", "Connected!", ""])

    def show_url_qr(self, source_bitmap):
        """Show the setup-URL QR code (scan to open the configuration form)."""
        self.screen = self.SCREEN_URL_QR
        self._show_qr(source_bitmap, LABEL_URL)

    def show_in_setup(self):
        """Show the "In setup..." screen while the browser is active."""
        self.screen = self.SCREEN_IN_SETUP
        self._show_text(["", "In", "setup...", ""])

    def show_countdown_start(self):
        """Show the initial settings-saved countdown screen."""
        self.screen = self.SCREEN_COUNTDOWN
        # Colors aligned to all 4 slots; slot 0 is blank so its color is ignored.
        colors = [0xFFFFFF, 0x00AA00, 0x00AA00, _COUNTDOWN_COLORS[0]]
        self._show_text(
            ["", "Settings", "saved!", f"{SAVE_COUNTDOWN_S}..."],
            colors=colors,
        )

    def show_countdown(self, n, colors):
        """Update the countdown number in slot 3 in place.

        Caller must have called ``show_countdown_start()`` first.  Only
        slot 3's text and color are updated — no group rebuild, no screen-state change.
        """
        self._text_labels[3].text  = f"{n}..."
        self._text_labels[3].color = colors[3] if colors and len(colors) > 3 else 0xFFFFFF


# ---------------------------------------------------------------------------
# Main portal entry point
# ---------------------------------------------------------------------------

def run(config, config_errors=None, recovery=False):
    """Run the Wi-Fi configuration portal.

    Owns the full lifecycle: matrix init, AP startup, QR display,
    two-phase display swap when a client connects, and (in Phase 3+)
    web form handling and settings persistence.

    ``config_errors`` is a dict of ``{config_key: message}`` for values that
    failed type coercion in code.py.  Keys are translated to field names via
    ``KEY_TO_FIELD`` so the form can highlight the relevant inputs.

    ``recovery`` should be ``True`` when the portal was entered because Wi-Fi
    was previously configured but became unreachable (i.e. the scheduler raised
    ``PortalNeeded``).  In recovery mode the portal periodically retries the
    saved credentials and reloads automatically if they succeed.  When
    ``False`` (the default) — first-time setup, forced reconfig, or config
    errors — no retry is attempted; the user explicitly wanted the portal.

    Exits only by calling supervisor.reload() after saving new config.
    """
    network.user_agent = config.get('USER_AGENT')
    _run_start = monotonic()

    # Read current saved values to pre-populate the form.
    _raw_settings = load_settings()
    _current_values = {
        KEY_TO_FIELD[k]: v
        for k, v in _raw_settings.items()
        if k in KEY_TO_FIELD
    }

    _raw_colors = load_settings("/colors.toml")
    _current_colors = {
        COLORS_KEY_TO_FIELD[k]: v
        for k, v in _raw_colors.items()
        if k in COLORS_KEY_TO_FIELD
    }

    # Translate config-key error names to form field names.
    _field_errors = {}
    for key, msg in (config_errors or {}).items():
        field = KEY_TO_FIELD.get(key)
        if field:
            _field_errors[field] = msg

    display = PortalDisplay(config)

    ssid = config.get('AP_SSID', 'WP')
    password = config.get('AP_PASSWORD')

    network.start_ap(ssid, password)
    ip = network.ap_ip()
    print(f"Portal AP: {ssid} ({ip})")

    wifi_bitmap = make_qr_bitmap(wifi_qr_data(ssid, password))
    url_bitmap = make_qr_bitmap(url_qr_data(ip))

    _usb_connected = supervisor.runtime.usb_connected
    if _usb_connected:
        display.show_usb_warning()
    else:
        display.show_setup_intro(["", "Wi-Fi", "failed", ""] if recovery else None)
        sleep(INTERSTITIAL_S)

    print("Scanning for networks...")
    initial_networks = network.scan_networks()
    for ssid, rssi in initial_networks:
        print(f"  {ssid} ({rssi} dBm)")
    n = len(initial_networks)
    print(f"Found {n} {'network' if n == 1 else 'networks'}")

    server, server_state = _make_server(ip, initial_networks, _current_values, _field_errors,
                                        _current_colors)

    if not _usb_connected:
        display.show_wifi_qr(wifi_bitmap)

    # Disarm before reconfiguring: setting timeout while mode=WatchDogMode.RAISE
    # raises espidf.IDFError on ESP32 even though the assignment takes effect.
    # Setting mode=None first is safe from any prior mode.
    watchdog = microcontroller.watchdog
    watchdog.mode = None
    watchdog.timeout = WATCHDOG_TIMEOUT_SECONDS

    _client_connected = False
    _in_setup = False
    _last_client_check = monotonic()
    _last_wifi_retry = _run_start

    while True:
        watchdog.mode = WatchDogMode.RESET
        watchdog.feed()

        try:
            server.poll()
        except OSError as e:
            print(f"Server poll error: {e}")

        if server_state.get('reload_pending'):
            display.show_countdown_start()
            for i in range(SAVE_COUNTDOWN_S, 0, -1):
                watchdog.feed()
                display.show_countdown(i, [0x00AA00, 0x00AA00, 0x00AA00, _COUNTDOWN_COLORS[SAVE_COUNTDOWN_S - i]])
                sleep(1)
            supervisor.reload()

        now = monotonic()
        if recovery and not _client_connected and now - _last_wifi_retry >= AP_CYCLE_S:
            _last_wifi_retry = now
            print("Portal: retrying Wi-Fi connection...")
            network.connect(config)
            if wifi.radio.connected:
                print("Portal: Wi-Fi reconnected — reloading")
                supervisor.reload()
            else:
                print("Portal: Wi-Fi retry failed — continuing portal")

        usb_now = supervisor.runtime.usb_connected
        if usb_now != _usb_connected:
            _usb_connected = usb_now
            if _usb_connected:
                print("USB connected — showing warning")
                display.show_usb_warning()
            else:
                print("USB ejected — showing WiFi QR")
                display.show_wifi_qr(wifi_bitmap)
            _client_connected = False
            _in_setup = False

        if _usb_connected:
            continue

        if now - _last_client_check >= CLIENT_CHECK_INTERVAL_S:
            _last_client_check = now
            clients = wifi.radio.stations_ap
            now_connected = bool(clients)

            if now_connected != _client_connected:
                _client_connected = now_connected
                _in_setup = False
                if _client_connected:
                    print("Client connected — showing URL QR")
                    display.show_connected()
                    sleep(INTERSTITIAL_S)
                    display.show_url_qr(url_bitmap)
                else:
                    print("Client disconnected — showing WiFi QR")
                    display.show_wifi_qr(wifi_bitmap)

            elif _client_connected:
                last_req = server_state['last_request_t']
                now_in_setup = last_req > 0 and (now - last_req) < SETUP_TIMEOUT_S
                if now_in_setup != _in_setup:
                    _in_setup = now_in_setup
                    if _in_setup:
                        print("Browser active — showing 'In setup...'")
                        display.show_in_setup()
                    else:
                        print("Setup timed out — showing URL QR")
                        display.show_url_qr(url_bitmap)
