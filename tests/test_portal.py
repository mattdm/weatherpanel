"""Tests for the Wi-Fi configuration portal."""
from unittest.mock import MagicMock

import network
import portal as portal_module
from portal import Portal, wifi_qr_data, url_qr_data


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
# Portal lifecycle
# ---------------------------------------------------------------------------

class TestPortalStart:
    def test_starts_ap_with_ssid(self, monkeypatch):
        calls = []
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: calls.append((ssid, password)))
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())

        display = MagicMock()
        p = Portal(display, {'AP_SSID': 'TestPanel'})
        p.start()

        assert calls == [('TestPanel', None)]

    def test_starts_ap_with_password(self, monkeypatch):
        calls = []
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: calls.append((ssid, password)))
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())

        display = MagicMock()
        p = Portal(display, {'AP_SSID': 'Locked', 'AP_PASSWORD': 'pw123'})
        p.start()

        assert calls == [('Locked', 'pw123')]

    def test_shows_portal_on_display(self, monkeypatch):
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: None)
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        fake_bitmap = MagicMock()
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: fake_bitmap)

        display = MagicMock()
        p = Portal(display, {'AP_SSID': 'Test'})
        p.start()

        display.show_portal.assert_called_once_with(fake_bitmap, "Scan")

    def test_sets_running_flag(self, monkeypatch):
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: None)
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())

        p = Portal(MagicMock(), {'AP_SSID': 'X'})
        assert not p.running
        p.start()
        assert p.running

    def test_uses_default_ssid(self, monkeypatch):
        calls = []
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: calls.append(ssid))
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: MagicMock())

        Portal(MagicMock(), {}).start()
        assert calls == ['WeatherPanel']

    def test_qr_data_matches_ssid(self, monkeypatch):
        monkeypatch.setattr(network, 'start_ap', lambda ssid, password=None: None)
        monkeypatch.setattr(network, 'ap_ip', lambda: '192.168.4.1')

        captured = []
        monkeypatch.setattr(portal_module, 'make_qr_bitmap', lambda data: captured.append(data) or MagicMock())

        Portal(MagicMock(), {'AP_SSID': 'MyNet'}).start()
        assert captured == ["WIFI:T:nopass;S:MyNet;;"]


class TestPortalStop:
    def test_stops_ap(self, monkeypatch):
        stopped = []
        monkeypatch.setattr(network, 'stop_ap', lambda: stopped.append(True))

        display = MagicMock()
        p = Portal(display, {})
        p._running = True
        p.stop()

        assert stopped == [True]

    def test_hides_portal_display(self, monkeypatch):
        monkeypatch.setattr(network, 'stop_ap', lambda: None)

        display = MagicMock()
        p = Portal(display, {})
        p._running = True
        p.stop()

        display.hide_portal.assert_called_once()

    def test_clears_running_flag(self, monkeypatch):
        monkeypatch.setattr(network, 'stop_ap', lambda: None)

        p = Portal(MagicMock(), {})
        p._running = True
        p.stop()
        assert not p.running


class TestPortalPoll:
    def test_returns_none(self):
        p = Portal(MagicMock(), {})
        assert p.poll() is None
