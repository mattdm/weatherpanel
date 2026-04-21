"""Wi-Fi configuration portal for initial setup and recovery.

Runs as an independent program path (invoked from code.py), completely
separate from the weather scheduler.  Owns its own matrix init, displayio
tree, and event loop.  Uses bit_depth=1 so the QR code is a clean
on/off signal with no PWM strobing -- cameras can scan it reliably.
"""
import displayio
import microcontroller
import socketpool
from time import sleep
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
PORTAL_LOOP_SLEEP_S = 1
INTERSTITIAL_S = 1.5
LABEL_LINE_HEIGHT = 10  # 8px font + 2px gap

LABEL_WIFI = ["Scan", "for", "WiFi"]
LABEL_URL  = ["Link", "to", "Setup"]

# Palette indices
QR_BLACK = 0
QR_WHITE = 1


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
    """Build a URL QR code data string pointing at the portal web server."""
    return f"http://{ip}"


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

def _ssid_options(networks):
    """Return HTML <option> elements for a list of (ssid, rssi) tuples."""
    return "".join(
        f'<option value="{ssid}">{ssid} ({rssi} dBm)</option>'
        for ssid, rssi in networks
    )


def _form_html(networks):
    """Return the full config form HTML page."""
    options = _ssid_options(networks)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WeatherPanel Setup</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:2em auto;padding:0 1em}}
label{{display:block;margin-top:1em}}input,select{{width:100%;padding:.4em;box-sizing:border-box}}
.hint{{color:#666;font-size:.85em}}button{{margin-top:1.5em;width:100%;padding:.6em;font-size:1em}}</style>
</head>
<body>
<h2>WeatherPanel Setup</h2>
<form method="POST" action="/">
<label>Wi-Fi network
<select name="ssid">
{options}
</select></label>
<label>Password <input type="password" name="password"></label>
<label>Latitude <span class="hint">(optional — leave blank for auto)</span>
<input type="text" name="lat" placeholder="e.g. 42.39"></label>
<label>Longitude <span class="hint">(optional)</span>
<input type="text" name="lon" placeholder="e.g. -71.10"></label>
<button type="submit">Save &amp; Connect</button>
</form>
</body>
</html>"""


def _make_server(ip):
    """Create and start the HTTP server bound to the AP address."""
    pool = socketpool.SocketPool(wifi.radio)
    server = Server(pool)

    @server.route("/", GET)
    def index(request: Request):
        networks = network.scan_networks()
        return Response(request, _form_html(networks), content_type="text/html")

    @server.route("/scan", GET)
    def scan(request: Request):
        networks = network.scan_networks()
        return Response(request, _ssid_options(networks), content_type="text/html")

    @server.route("/", POST)
    def submit(request: Request):
        # Phase 3: parse form and save settings.  Stub for now.
        return Response(request, "<p>Saving... (not yet implemented)</p>", content_type="text/html")

    server.start(ip, port=80)
    print(f"HTTP server running at http://{ip}")
    return server


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

    server = _make_server(ip)

    _show_qr(root_group, font, wifi_bitmap, LABEL_WIFI)

    watchdog = microcontroller.watchdog
    watchdog.timeout = WATCHDOG_TIMEOUT_S

    _client_connected = False

    while True:
        watchdog.mode = WatchDogMode.RESET
        watchdog.feed()

        try:
            server.poll()
        except OSError as e:
            print(f"Server poll error: {e}")

        # Two-phase QR: swap to URL QR when a client joins the AP
        clients = wifi.radio.stations_ap
        now_connected = bool(clients)
        if now_connected != _client_connected:
            _client_connected = now_connected
            if _client_connected:
                print("Client connected -- showing URL QR")
                _show_interstitial(root_group, font, "Connected!")
                sleep(INTERSTITIAL_S)
                _show_qr(root_group, font, url_bitmap, LABEL_URL)
            else:
                print("Client disconnected -- showing WiFi QR")
                _show_qr(root_group, font, wifi_bitmap, LABEL_WIFI)

        sleep(PORTAL_LOOP_SLEEP_S)
