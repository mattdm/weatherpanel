"""Wi-Fi configuration portal for initial setup and recovery.

Starts an access point, displays a QR code for joining, and (in later
phases) serves a web-based configuration form.
"""
import displayio
import adafruit_miniqr

import network


QR_BORDER_PX = 2


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

    White (palette index 1) is the background / quiet zone; black
    (index 0) is the dark QR modules.  The bitmap includes a
    ``QR_BORDER_PX``-wide quiet zone on all sides.
    """
    qr = adafruit_miniqr.QRCode()
    qr.add_data(data.encode("utf-8"))
    qr.make()

    mat = qr.matrix
    size = mat.width + 2 * QR_BORDER_PX
    bitmap = displayio.Bitmap(size, size, 2)

    for y in range(size):
        for x in range(size):
            bitmap[x, y] = 1

    for y in range(mat.height):
        for x in range(mat.width):
            if mat[x, y]:
                bitmap[x + QR_BORDER_PX, y + QR_BORDER_PX] = 0

    return bitmap


class Portal:
    """Manages the Wi-Fi configuration portal lifecycle."""

    def __init__(self, display, config):
        self._display = display
        self._config = config
        self._running = False

    def start(self):
        """Start access point and show Wi-Fi QR code on the display."""
        ssid = self._config.get('AP_SSID', 'WeatherPanel')
        password = self._config.get('AP_PASSWORD')

        network.start_ap(ssid, password)
        print(f"Portal AP: {ssid} ({network.ap_ip()})")

        data = wifi_qr_data(ssid, password)
        qr_bitmap = make_qr_bitmap(data)
        self._display.show_portal(qr_bitmap, "Scan")

        self._running = True

    def poll(self):
        """Check for portal activity.  Returns None (stub for Phase 2)."""
        return None

    def stop(self):
        """Stop access point and restore normal display."""
        network.stop_ap()
        self._display.hide_portal()
        self._running = False
        print("Portal stopped")

    @property
    def running(self):
        return self._running
