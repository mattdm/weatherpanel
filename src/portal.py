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
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label
from adafruit_httpserver import Server, Request, Response, GET, POST

import matrix
import network
import wifi

QR_BORDER_PX = 1
WATCHDOG_TIMEOUT_S = 60
CLIENT_CHECK_INTERVAL_S = 1  # how often to check stations_ap
SETUP_TIMEOUT_S = 60         # revert to URL QR if no browser activity
INTERSTITIAL_S = 1.5
LABEL_LINE_HEIGHT = 10  # 8px font + 2px gap

LABEL_WIFI = ["Scan", "for", "WiFi"]
LABEL_URL  = ["Link", "to", "Setup"]

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
    "temp_scale_range": "TEMP_SCALE_RANGE",
    "temp_midpoint":    "TEMP_MIDPOINT",
}


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
            result.append(f'{matched_key} = "{updates[matched_key]}"\n')
            found.add(matched_key)
        else:
            result.append(line)

    for key, val in updates.items():
        if key not in found:
            result.append(f'{key} = "{val}"\n')

    return "".join(result)


def save_settings(form_data, path="/settings.toml"):
    """Read settings.toml, merge form data, and write back atomically.

    Uses ``storage.remount`` to temporarily make the filesystem writable.
    Raises ``RuntimeError`` (re-raised from ``storage.remount``) when the
    USB drive is mounted — the caller should catch this and return an error
    page instructing the user to disconnect USB.

    Skips the write entirely when the merged content is identical to the
    original, avoiding unnecessary flash wear.

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


# ---------------------------------------------------------------------------
# QR data helpers
# ---------------------------------------------------------------------------

def wifi_qr_data(ssid, password=None):
    """Build a Wi-Fi QR code data string.

    Uses the de-facto ``WIFI:`` URI scheme recognised by iOS and Android
    camera apps.  Returns the raw string (caller encodes to bytes for the
    QR library).
    """
    if password:
        return f"WIFI:T:WPA;S:{ssid};P:{password};;"
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


def _ssid_options(networks):
    """Return HTML <option> elements for a list of (ssid, rssi) tuples."""
    return "".join(
        f'<option value="{_html_escape(ssid)}">{_html_escape(ssid)} ({rssi} dBm)</option>'
        for ssid, rssi in networks
    )


def _form_html(networks):
    """Return the full config form HTML page."""
    options = _ssid_options(networks)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WeatherPanel Setup</title>
<style>
body{{font-family:sans-serif;max-width:480px;margin:2em auto;padding:0 1em}}
label{{display:block;margin-top:1em}}
input,select{{width:100%;padding:.4em;box-sizing:border-box}}
.hint{{color:#666;font-size:.85em}}
.pw-row{{display:flex;gap:.4em}}
.pw-row input{{flex:1}}
.pw-toggle{{white-space:nowrap;padding:.4em .8em;margin-top:0;width:auto;font-size:.9em}}
button{{margin-top:1.5em;width:100%;padding:.6em;font-size:1em}}
details{{margin-top:1.5em}}summary{{cursor:pointer;color:#444}}
</style>
</head>
<body>
<h2>WeatherPanel Setup</h2>
<form method="POST" action="/">
<label>Wi-Fi network
<div class="pw-row">
<select name="ssid" id="ssid" style="flex:1">
{options}
</select>
<button type="button" class="pw-toggle"
  onclick="this.textContent='...';fetch('/scan').then(r=>r.text()).then(h=>{{document.getElementById('ssid').innerHTML=h;this.textContent='Scan'}})">Scan</button>
</div></label>
<label>Password
<div class="pw-row">
<input type="password" name="password" id="pw">
<button type="button" class="pw-toggle"
  onclick="var i=document.getElementById('pw');i.type=i.type=='password'?'text':'password';this.textContent=i.type=='password'?'Show':'Hide'">Show</button>
</div></label>
<label>Latitude <span class="hint">(optional — leave blank for auto)</span>
<input type="text" name="lat" placeholder="e.g. 42.39"></label>
<label>Longitude <span class="hint">(optional)</span>
<input type="text" name="lon" placeholder="e.g. -71.10"></label>
<details>
<summary>Advanced</summary>
<label>Temperature scale range (°F) <span class="hint">(full span of the color scale)</span>
<input type="number" name="temp_scale_range" placeholder="110" value="110"></label>
<label>Temperature midpoint (°F) <span class="hint">(temperature mapped to center of scale)</span>
<input type="number" name="temp_midpoint" placeholder="50" value="50"></label>
</details>
<button type="submit">Save &amp; Connect</button>
</form>
</body>
</html>"""


def _success_html():
    """Return the brief success page shown after settings are saved."""
    return """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Saved!</title>
<style>body{font-family:sans-serif;max-width:480px;margin:4em auto;text-align:center}</style>
</head>
<body>
<h2>Saved!</h2>
<p>Rebooting&hellip; reconnect to your Wi-Fi network in a moment.</p>
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
<p>The USB drive is currently mounted. Disconnect USB from your computer,
then try submitting the form again.</p>
</body>
</html>"""


def _make_server(ip, initial_networks):
    """Create and start the HTTP server bound to all interfaces.

    ``initial_networks`` is a pre-scanned list of (ssid, rssi) tuples used
    for the initial form render -- scanning inside a request handler can
    interfere with the AP radio and drop the client connection.

    Returns ``(server, state)`` where ``state['last_request_t']`` is updated
    on each incoming request so the main loop can track browser activity.
    """
    pool = socketpool.SocketPool(wifi.radio)
    server = Server(pool)

    _networks = [initial_networks]
    state = {'last_request_t': 0.0}

    @server.route("/", GET)
    def index(request: Request):
        state['last_request_t'] = monotonic()
        return Response(request, _form_html(_networks[0]), content_type="text/html")

    @server.route("/scan", GET)
    def scan(request: Request):
        state['last_request_t'] = monotonic()
        _networks[0] = network.scan_networks()
        return Response(request, _ssid_options(_networks[0]), content_type="text/html")

    @server.route("/", POST)
    def submit(request: Request):
        state['last_request_t'] = monotonic()
        form_data = request.form_data
        try:
            save_settings(form_data)
        except RuntimeError:
            return Response(request, _usb_error_html(), content_type="text/html")
        state['reload_pending'] = True
        return Response(request, _success_html(), content_type="text/html")

    server.start("0.0.0.0", port=80)
    print(f"HTTP server running at http://{ip}:80")
    return server, state


# ---------------------------------------------------------------------------
# Portal display setup
# ---------------------------------------------------------------------------

def _make_portal_display(config):
    """Initialize the matrix with bit_depth=1 and return a root group."""
    root_group = displayio.Group()
    matrix.display_set_root(root_group, swapgb=config.get('SWAP_GREEN_BLUE', False), bit_depth=1)
    return root_group


def _show_qr(root_group, font, qr_bitmap, label_lines):
    """Render a QR bitmap and multi-line label into root_group.

    Clears any previous content first.  The QR sits left-aligned;
    the label lines sit to its right, vertically centered as a group.
    """
    while len(root_group) > 0:
        root_group.pop()

    qr_palette = displayio.Palette(2)
    qr_palette[QR_BLACK] = 0x000000
    qr_palette[QR_WHITE] = 0xFFFFFF

    qr_grid = displayio.TileGrid(
        qr_bitmap, pixel_shader=qr_palette,
        tile_width=qr_bitmap.width, tile_height=qr_bitmap.height,
    )
    qr_grid.y = (32 - qr_bitmap.height) // 2
    qr_grid.x = 1

    root_group.append(qr_grid)

    n = len(label_lines)
    total_h = n * 8 + (n - 1) * 2
    start_y = (32 - total_h) // 2 + 4
    label_x = qr_bitmap.width + 3

    for i, text in enumerate(label_lines):
        label = Label(
            font, text=text, color=0xFFFFFF,
            x=label_x, y=start_y + i * LABEL_LINE_HEIGHT,
        )
        root_group.append(label)


def _show_interstitial(root_group, font, lines):
    """Clear the display and show one or more centered text lines.

    ``lines`` may be a single string or a list of strings.
    Lines are vertically centered as a group, starting at x=1.
    """
    if isinstance(lines, str):
        lines = [lines]

    while len(root_group) > 0:
        root_group.pop()

    n = len(lines)
    total_h = n * 8 + (n - 1) * 2
    start_y = (32 - total_h) // 2 + 4

    for i, text in enumerate(lines):
        label = Label(
            font, text=text, color=0xFFFFFF,
            x=1, y=start_y + i * LABEL_LINE_HEIGHT,
        )
        root_group.append(label)


# ---------------------------------------------------------------------------
# Main portal entry point
# ---------------------------------------------------------------------------

def run(config):
    """Run the Wi-Fi configuration portal.

    Owns the full lifecycle: matrix init, AP startup, QR display,
    two-phase display swap when a client connects, and (in Phase 3+)
    web form handling and settings persistence.

    Exits only by calling supervisor.reload() after saving new config.
    """
    network.user_agent = config.get('USER_AGENT')

    root_group = _make_portal_display(config)
    font = bitmap_font.load_font("/fonts/dogica-pixel-8.pcf")

    ssid = config.get('AP_SSID', 'WeatherPanel')
    password = config.get('AP_PASSWORD')

    network.start_ap(ssid, password)
    ip = network.ap_ip()
    print(f"Portal AP: {ssid} ({ip})")

    wifi_bitmap = make_qr_bitmap(wifi_qr_data(ssid, password))
    url_bitmap = make_qr_bitmap(url_qr_data(ip))

    _show_interstitial(root_group, font, ["Weather", "Panel", "Setup"])
    sleep(INTERSTITIAL_S)

    print("Scanning for networks...")
    initial_networks = network.scan_networks()
    print(f"Found {len(initial_networks)} network(s)")

    server, server_state = _make_server(ip, initial_networks)

    _show_qr(root_group, font, wifi_bitmap, LABEL_WIFI)

    watchdog = microcontroller.watchdog
    watchdog.timeout = WATCHDOG_TIMEOUT_S

    _client_connected = False
    _in_setup = False
    _last_client_check = monotonic()

    while True:
        watchdog.mode = WatchDogMode.RESET
        watchdog.feed()

        try:
            server.poll()
        except OSError as e:
            print(f"Server poll error: {e}")

        if server_state.get('reload_pending'):
            supervisor.reload()

        now = monotonic()
        if now - _last_client_check >= CLIENT_CHECK_INTERVAL_S:
            _last_client_check = now
            clients = wifi.radio.stations_ap
            now_connected = bool(clients)

            if now_connected != _client_connected:
                _client_connected = now_connected
                _in_setup = False
                if _client_connected:
                    print("Client connected -- showing URL QR")
                    _show_interstitial(root_group, font, "Connected!")
                    sleep(INTERSTITIAL_S)
                    _show_qr(root_group, font, url_bitmap, LABEL_URL)
                else:
                    print("Client disconnected -- showing WiFi QR")
                    _show_qr(root_group, font, wifi_bitmap, LABEL_WIFI)

            elif _client_connected:
                last_req = server_state['last_request_t']
                now_in_setup = last_req > 0 and (now - last_req) < SETUP_TIMEOUT_S
                if now_in_setup != _in_setup:
                    _in_setup = now_in_setup
                    if _in_setup:
                        print("Browser active -- showing 'In setup...'")
                        _show_interstitial(root_group, font, ["In", "setup..."])
                    else:
                        print("Setup timed out -- showing URL QR")
                        _show_qr(root_group, font, url_bitmap, LABEL_URL)
