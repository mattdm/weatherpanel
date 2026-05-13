"""Render-to-PNG tests for the portal display states.

Exercises the full portal display pipeline (adafruit_miniqr → displayio_sim
→ matrix_sim → PIL Image) and compares against reference PNGs.

First run (no reference yet): each test saves the rendered PNG and passes.
Subsequent runs: pixel-for-pixel comparison against the saved reference.
Intentional update: pytest --update-refs
"""
from pathlib import Path

import pytest

_FONTS_DIR = Path(__file__).parent.parent / "fonts"

_PORTAL_CONFIG = {
    "SWAP_GREEN_BLUE": False,
    "AP_SSID": "WP",
    "AP_PASSWORD": None,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def portal_display(monkeypatch):
    """A PortalDisplay initialized via the real sim stack.

    Returns ``(display, sim_display)`` so tests can call display methods and
    then render via sim_display.  Font path is redirected to the repo
    fonts/ directory.
    """
    import adafruit_bitmap_font.bitmap_font as _bmp_font
    import matrix as matrix_module
    import matrix_sim
    import portal as portal_module

    orig_load = _bmp_font.load_font
    monkeypatch.setattr(
        _bmp_font, "load_font",
        lambda path: orig_load(str(_FONTS_DIR / Path(path).name)),
    )
    monkeypatch.setattr(matrix_module, "display_set_root", matrix_sim.display_set_root)

    display = portal_module.PortalDisplay(_PORTAL_CONFIG)
    sim_disp = matrix_sim.SimDisplay(display._root_group)
    return display, sim_disp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compare_portal(request, sim_disp, name):
    """Render the portal root at scale=8 and compare against reference PNG."""
    from PIL import Image

    refs_dir = Path(__file__).parent / "reference-images"
    ref_path = refs_dir / f"portal_{name}.png"
    img = sim_disp.render_to_image(scale=8)

    if request.config.getoption("--update-refs") or not ref_path.exists():
        refs_dir.mkdir(exist_ok=True)
        img.save(ref_path)
        return

    ref_img = Image.open(ref_path).convert("RGB")
    assert list(img.getdata()) == list(ref_img.getdata()), (
        f"Portal render mismatch for '{name}' — run pytest --update-refs to accept"
    )


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------

class TestPortalRender:
    def test_splash_screen(self, portal_display, request):
        """Splash: 'Weather / Panel / Setup' centered text."""
        display, sim_disp = portal_display
        display.show_text(["Weather", "Panel", "Setup"])
        _compare_portal(request, sim_disp, "splash")

    def test_connected_interstitial(self, portal_display, request):
        """Connected! interstitial before showing URL QR."""
        display, sim_disp = portal_display
        display.show_text("Connected!")
        _compare_portal(request, sim_disp, "connected")

    def test_in_setup_interstitial(self, portal_display, request):
        """In setup... interstitial when browser has the form open."""
        display, sim_disp = portal_display
        display.show_text(["In", "setup..."])
        _compare_portal(request, sim_disp, "in_setup")

    def test_wifi_qr(self, portal_display, request):
        """WiFi QR code with 'Scan / for / WiFi' label (open network)."""
        display, sim_disp = portal_display
        import portal as portal_module
        data   = portal_module.wifi_qr_data("WP")
        bitmap = portal_module.make_qr_bitmap(data)
        display.show_qr(bitmap, portal_module.LABEL_WIFI)
        _compare_portal(request, sim_disp, "wifi_qr")

    def test_wifi_qr_password(self, portal_display, request):
        """WiFi QR code for a password-protected AP."""
        display, sim_disp = portal_display
        import portal as portal_module
        data   = portal_module.wifi_qr_data("WP", "WeatherP")
        bitmap = portal_module.make_qr_bitmap(data)
        display.show_qr(bitmap, portal_module.LABEL_WIFI)
        _compare_portal(request, sim_disp, "wifi_qr_password")

    def test_url_qr(self, portal_display, request):
        """URL QR code with 'Link / to / Setup' label."""
        display, sim_disp = portal_display
        import portal as portal_module
        data   = portal_module.url_qr_data("127.0.0.1:8080")
        bitmap = portal_module.make_qr_bitmap(data)
        display.show_qr(bitmap, portal_module.LABEL_URL)
        _compare_portal(request, sim_disp, "url_qr")

    def test_usb_warning_interstitial(self, portal_display, request):
        """USB warning: 'Edit / CIRCUITPY / settings / .toml' in USB_WARNING_COLOR."""
        display, sim_disp = portal_display
        import portal as portal_module
        display.show_text(portal_module.LABEL_USB_WARNING, color=portal_module.USB_WARNING_COLOR)
        _compare_portal(request, sim_disp, "usb_warning")


# ---------------------------------------------------------------------------
# QR bitmap structural checks (not visual, no reference PNG needed)
# ---------------------------------------------------------------------------

class TestQrBitmapStructure:
    def test_wifi_qr_bitmap_size(self):
        """Version 2 QR + 1px border = 27×27 bitmap."""
        import portal as portal_module
        bitmap = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        assert bitmap.width == 27
        assert bitmap.height == 27

    def test_url_qr_bitmap_size(self):
        """URL QR is also Version 2 = 27×27 bitmap."""
        import portal as portal_module
        bitmap = portal_module.make_qr_bitmap(portal_module.url_qr_data("127.0.0.1:8080"))
        assert bitmap.width == 27
        assert bitmap.height == 27

    def test_qr_bitmap_has_both_colors(self):
        """A real QR code has dark and light modules -- not all one color."""
        import portal as portal_module
        bitmap = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        pixels = [bitmap[x, y] for y in range(bitmap.height) for x in range(bitmap.width)]
        assert portal_module.QR_BLACK in pixels, "No dark modules in QR bitmap"
        assert portal_module.QR_WHITE in pixels, "No light modules in QR bitmap"

    def test_open_vs_wpa_qr_differs(self):
        """Open-network and WPA QR codes must produce different bitmaps."""
        import portal as portal_module
        open_bm = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        wpa_bm  = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP", "secret12"))
        open_px = [open_bm[x, y] for y in range(open_bm.height) for x in range(open_bm.width)]
        wpa_px  = [wpa_bm[x, y]  for y in range(wpa_bm.height)  for x in range(wpa_bm.width)]
        assert open_px != wpa_px, "Open and WPA QR bitmaps should differ"
