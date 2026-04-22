"""Tests for the Wi-Fi configuration portal."""
from unittest.mock import MagicMock

import network
from portal import (
    wifi_qr_data, url_qr_data,
    _show_qr, _show_interstitial, _make_portal_display,
    _ssid_options, _form_html,
)


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
    def test_includes_port_80(self):
        assert url_qr_data("192.168.4.1") == "http://192.168.4.1:80"

    def test_custom_ip(self):
        assert url_qr_data("10.0.0.1") == "http://10.0.0.1:80"


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


# ---------------------------------------------------------------------------
# Network scan
# ---------------------------------------------------------------------------

class TestScanNetworks:
    def test_returns_sorted_by_rssi(self, monkeypatch):
        import wifi as _wifi
        entries = [
            MagicMock(ssid="Weak", rssi=-80),
            MagicMock(ssid="Strong", rssi=-40),
            MagicMock(ssid="Mid", rssi=-60),
        ]
        _wifi.radio.start_scanning_networks = MagicMock(return_value=iter(entries))
        _wifi.radio.stop_scanning_networks = MagicMock()

        result = network.scan_networks()

        assert [s for s, _ in result] == ["Strong", "Mid", "Weak"]

    def test_deduplicates_keeping_strongest(self, monkeypatch):
        import wifi as _wifi
        entries = [
            MagicMock(ssid="Net", rssi=-70),
            MagicMock(ssid="Net", rssi=-50),
            MagicMock(ssid="Net", rssi=-65),
        ]
        _wifi.radio.start_scanning_networks = MagicMock(return_value=iter(entries))
        _wifi.radio.stop_scanning_networks = MagicMock()

        result = network.scan_networks()

        assert len(result) == 1
        assert result[0] == ("Net", -50)

    def test_filters_empty_ssids(self, monkeypatch):
        import wifi as _wifi
        entries = [
            MagicMock(ssid="", rssi=-50),
            MagicMock(ssid="Real", rssi=-60),
        ]
        _wifi.radio.start_scanning_networks = MagicMock(return_value=iter(entries))
        _wifi.radio.stop_scanning_networks = MagicMock()

        result = network.scan_networks()

        assert [s for s, _ in result] == ["Real"]

    def test_calls_stop_scanning(self, monkeypatch):
        import wifi as _wifi
        _wifi.radio.start_scanning_networks = MagicMock(return_value=iter([]))
        _wifi.radio.stop_scanning_networks = MagicMock()

        network.scan_networks()

        _wifi.radio.stop_scanning_networks.assert_called_once()


# ---------------------------------------------------------------------------
# Web form HTML helpers
# ---------------------------------------------------------------------------

class TestSsidOptions:
    def test_generates_option_elements(self):
        html = _ssid_options([("HomeNet", -45), ("Other", -70)])
        assert '<option value="HomeNet">HomeNet (-45 dBm)</option>' in html
        assert '<option value="Other">Other (-70 dBm)</option>' in html

    def test_empty_list_returns_empty_string(self):
        assert _ssid_options([]) == ""


class TestFormHtml:
    def test_contains_ssid_options(self):
        html = _form_html([("MyNet", -50)])
        assert "MyNet" in html

    def test_has_password_field(self):
        html = _form_html([])
        assert 'name="password"' in html

    def test_has_lat_lon_fields(self):
        html = _form_html([])
        assert 'name="lat"' in html
        assert 'name="lon"' in html

    def test_has_temp_scale_fields(self):
        html = _form_html([])
        assert 'name="temp_scale_range"' in html
        assert 'name="temp_midpoint"' in html

    def test_posts_to_root(self):
        html = _form_html([])
        assert 'action="/"' in html
