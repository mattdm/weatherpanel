"""Tests for the Wi-Fi configuration portal."""
from unittest.mock import MagicMock

import network
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

    def test_single_string_appends_one_label(self):
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)
        font = MagicMock()

        _show_interstitial(root, font, "Connected!")

        assert root.append.call_count == 1

    def test_list_appends_one_label_per_line(self):
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)
        font = MagicMock()

        _show_interstitial(root, font, ["Weather", "Panel", "Setup"])

        assert root.append.call_count == 3
