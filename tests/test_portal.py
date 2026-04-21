"""Tests for the Wi-Fi configuration portal."""
from unittest.mock import MagicMock

import network
import portal as portal_module
from portal import wifi_qr_data, url_qr_data, _show_qr, _show_interstitial, _make_portal_display


# ---------------------------------------------------------------------------
# QR data string generation
# ---------------------------------------------------------------------------

class TestWifiQrData:
    def test_open_network(self):
        assert wifi_qr_data("MyAP") == "WIFI:T:nopass;S:MyAP;;"

    def test_password_protected(self):
        assert wifi_qr_data("MyAP", "s3cret") == "WIFI:T:WPA;S:MyAP;P:s3cret;;"

    def test_special_characters_preserved(self):
        assert wifi_qr_data("My AP!") == "WIFI:T:nopass;S:My AP!;;"

    def test_empty_password_treated_as_open(self):
        assert wifi_qr_data("Net", "") == "WIFI:T:nopass;S:Net;;"

    def test_none_password_treated_as_open(self):
        assert wifi_qr_data("Net", None) == "WIFI:T:nopass;S:Net;;"


class TestUrlQrData:
    def test_default_ip(self):
        assert url_qr_data("192.168.4.1") == "http://192.168.4.1"

    def test_custom_ip(self):
        assert url_qr_data("10.0.0.1") == "http://10.0.0.1"


# ---------------------------------------------------------------------------
# Wi-Fi configured detection
# ---------------------------------------------------------------------------

class TestWifiConfigured:
    def test_placeholder_ssid(self):
        config = {'CIRCUITPY_WIFI_SSID': 'change me in settings.toml'}
        assert not network.wifi_configured(config)

    def test_real_ssid(self):
        config = {'CIRCUITPY_WIFI_SSID': 'HomeNetwork'}
        assert network.wifi_configured(config)

    def test_missing_key(self):
        assert not network.wifi_configured({})

    def test_none_value(self):
        config = {'CIRCUITPY_WIFI_SSID': None}
        assert not network.wifi_configured(config)


# ---------------------------------------------------------------------------
# Portal display functions
# ---------------------------------------------------------------------------

class TestMakePortalDisplay:
    def test_calls_matrix_with_bit_depth_1(self, monkeypatch):
        import matrix as matrix_module
        calls = []
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda root, swapgb=False, bit_depth=6: calls.append(bit_depth)
        )
        import displayio
        displayio.Group = MagicMock(return_value=MagicMock())

        _make_portal_display({})
        assert calls == [1]

    def test_passes_swapgb_from_config(self, monkeypatch):
        import matrix as matrix_module
        captured = {}
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda root, swapgb=False, bit_depth=6: captured.update({'swapgb': swapgb})
        )
        import displayio
        displayio.Group = MagicMock(return_value=MagicMock())

        _make_portal_display({'SWAP_GREEN_BLUE': True})
        assert captured['swapgb'] is True


class TestShowQr:
    def test_clears_existing_content(self):
        root = MagicMock()
        root.__len__ = MagicMock(side_effect=[2, 1, 0])
        font = MagicMock()
        bitmap = MagicMock()
        bitmap.width = 25
        bitmap.height = 25

        _show_qr(root, font, bitmap, ["Scan", "for", "WiFi"])

        assert root.pop.call_count == 2

    def test_appends_grid_and_all_label_lines(self):
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)
        font = MagicMock()
        bitmap = MagicMock()
        bitmap.width = 25
        bitmap.height = 25

        _show_qr(root, font, bitmap, ["Link", "to", "Setup"])

        # 1 TileGrid + 3 label lines = 4 appends
        assert root.append.call_count == 4

    def test_single_line_label(self):
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)
        font = MagicMock()
        bitmap = MagicMock()
        bitmap.width = 25
        bitmap.height = 25

        _show_qr(root, font, bitmap, ["OK"])

        # 1 TileGrid + 1 label line = 2 appends
        assert root.append.call_count == 2


class TestShowInterstitial:
    def test_clears_existing_content(self):
        root = MagicMock()
        root.__len__ = MagicMock(side_effect=[1, 0])
        font = MagicMock()

        _show_interstitial(root, font, "Connected!")

        assert root.pop.call_count == 1

    def test_appends_single_label(self):
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)
        font = MagicMock()

        _show_interstitial(root, font, "Connected!")

        assert root.append.call_count == 1


# ---------------------------------------------------------------------------
# portal.run() dispatch
# ---------------------------------------------------------------------------

class TestPortalRun:
    def test_starts_ap_with_ssid(self, monkeypatch):
        calls = []
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: calls.append(ssid))
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, '_make_portal_display', lambda config: MagicMock())
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())
        monkeypatch.setattr(portal_module, '_show_qr', lambda *a, **k: None)

        import sys
        _watchdog = sys.modules['microcontroller'].watchdog
        _watchdog.timeout = None

        # Break the loop after one iteration
        iteration = [0]
        def fake_sleep(s):
            iteration[0] += 1
            if iteration[0] >= 1:
                raise StopIteration
        monkeypatch.setattr(portal_module, 'sleep', fake_sleep)

        import wifi as _wifi
        _wifi.radio.stations_ap = 0

        font_mock = MagicMock()
        monkeypatch.setattr(portal_module.bitmap_font, 'load_font', lambda path: font_mock)

        try:
            portal_module.run({'AP_SSID': 'TestNet'})
        except StopIteration:
            pass

        assert 'TestNet' in calls

    def test_uses_default_ssid(self, monkeypatch):
        calls = []
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: calls.append(ssid))
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, '_make_portal_display', lambda config: MagicMock())
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())
        monkeypatch.setattr(portal_module, '_show_qr', lambda *a, **k: None)
        monkeypatch.setattr(portal_module.bitmap_font, 'load_font', lambda path: MagicMock())

        import wifi as _wifi
        _wifi.radio.stations_ap = 0

        def fake_sleep(s):
            raise StopIteration
        monkeypatch.setattr(portal_module, 'sleep', fake_sleep)

        try:
            portal_module.run({})
        except StopIteration:
            pass

        assert calls == ['WeatherPanel']

    def test_shows_wifi_qr_on_start(self, monkeypatch):
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: None)
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, '_make_portal_display', lambda config: MagicMock())

        wifi_bm = MagicMock()
        url_bm = MagicMock()
        bitmaps = iter([wifi_bm, url_bm])
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: next(bitmaps))

        shown = []
        monkeypatch.setattr(portal_module, '_show_qr', lambda root, font, bm, label: shown.append(label))
        monkeypatch.setattr(portal_module, '_show_interstitial', lambda *a: None)
        monkeypatch.setattr(portal_module.bitmap_font, 'load_font', lambda path: MagicMock())

        import wifi as _wifi
        _wifi.radio.stations_ap = 0

        def fake_sleep(s):
            raise StopIteration
        monkeypatch.setattr(portal_module, 'sleep', fake_sleep)

        try:
            portal_module.run({'AP_SSID': 'X'})
        except StopIteration:
            pass

        assert shown[0] == ["Scan", "for", "WiFi"]

    def test_swaps_to_url_qr_when_client_connects(self, monkeypatch):
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: None)
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, '_make_portal_display', lambda config: MagicMock())
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())

        shown = []
        monkeypatch.setattr(portal_module, '_show_qr', lambda root, font, bm, label: shown.append(label))
        interstitials = []
        monkeypatch.setattr(portal_module, '_show_interstitial', lambda root, font, text: interstitials.append(text))
        monkeypatch.setattr(portal_module.bitmap_font, 'load_font', lambda path: MagicMock())

        import wifi as _wifi
        iteration = [0]

        def fake_sleep(s):
            if s == portal_module.INTERSTITIAL_S:
                return  # skip interstitial pause
            iteration[0] += 1
            if iteration[0] == 1:
                _wifi.radio.stations_ap = 1  # client connects on second poll
            elif iteration[0] >= 2:
                raise StopIteration
        monkeypatch.setattr(portal_module, 'sleep', fake_sleep)

        _wifi.radio.stations_ap = 0

        try:
            portal_module.run({'AP_SSID': 'X'})
        except StopIteration:
            pass

        assert ["Scan", "for", "WiFi"] in shown
        assert ["Link", "to", "Setup"] in shown
        assert interstitials == ["Connected!"]

    def test_reverts_to_wifi_qr_when_client_disconnects(self, monkeypatch):
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: None)
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, '_make_portal_display', lambda config: MagicMock())
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())

        shown = []
        monkeypatch.setattr(portal_module, '_show_qr', lambda root, font, bm, label: shown.append(label))
        monkeypatch.setattr(portal_module, '_show_interstitial', lambda *a: None)
        monkeypatch.setattr(portal_module.bitmap_font, 'load_font', lambda path: MagicMock())

        import wifi as _wifi
        iteration = [0]

        def fake_sleep(s):
            if s == portal_module.INTERSTITIAL_S:
                return  # skip interstitial pause
            iteration[0] += 1
            if iteration[0] == 1:
                _wifi.radio.stations_ap = 1  # client connects
            elif iteration[0] == 2:
                _wifi.radio.stations_ap = 0  # client disconnects
            elif iteration[0] >= 3:
                raise StopIteration
        monkeypatch.setattr(portal_module, 'sleep', fake_sleep)

        _wifi.radio.stations_ap = 0

        try:
            portal_module.run({'AP_SSID': 'X'})
        except StopIteration:
            pass

        # WiFi QR -> URL QR -> WiFi QR
        assert shown == [["Scan", "for", "WiFi"], ["Link", "to", "Setup"], ["Scan", "for", "WiFi"]]
