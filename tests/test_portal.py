"""Tests for the Wi-Fi configuration portal."""
from unittest.mock import MagicMock, call, patch

import network
from portal import (
    wifi_qr_data, url_qr_data,
    _show_qr, _show_interstitial, _make_portal_display,
    _ssid_options, _form_html,
    FIELD_TO_KEY, merge_settings, save_settings,
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
        import displayio
        import matrix as matrix_module
        calls = []
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda root, swapgb=False, bit_depth=6: calls.append(bit_depth)
        )
        monkeypatch.setattr(displayio, 'Group', MagicMock(return_value=MagicMock()))

        _make_portal_display({})
        assert calls == [1]

    def test_passes_swapgb_from_config(self, monkeypatch):
        import displayio
        import matrix as matrix_module
        captured = {}
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda root, swapgb=False, bit_depth=6: captured.update({'swapgb': swapgb})
        )
        monkeypatch.setattr(displayio, 'Group', MagicMock(return_value=MagicMock()))

        _make_portal_display({'SWAP_GREEN_BLUE': True})
        assert captured['swapgb'] is True


class TestShowQr:
    """Structural tests: verify group mutation counts without real rendering.

    Label is mocked out so these tests don't depend on the real font and
    don't leak state into subsequent render tests.
    """

    def test_clears_existing_content(self, monkeypatch):
        import portal as portal_module
        monkeypatch.setattr(portal_module, "Label", MagicMock(return_value=MagicMock()))
        root = MagicMock()
        root.__len__ = MagicMock(side_effect=[2, 1, 0])
        bitmap = MagicMock()
        bitmap.width = 25
        bitmap.height = 25

        _show_qr(root, MagicMock(), bitmap, ["Scan", "for", "WiFi"])

        assert root.pop.call_count == 2

    def test_appends_grid_and_all_label_lines(self, monkeypatch):
        import portal as portal_module
        monkeypatch.setattr(portal_module, "Label", MagicMock(return_value=MagicMock()))
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)
        bitmap = MagicMock()
        bitmap.width = 25
        bitmap.height = 25

        _show_qr(root, MagicMock(), bitmap, ["Link", "to", "Setup"])

        # 1 TileGrid + 3 label lines = 4 appends
        assert root.append.call_count == 4

    def test_single_line_label(self, monkeypatch):
        import portal as portal_module
        monkeypatch.setattr(portal_module, "Label", MagicMock(return_value=MagicMock()))
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)
        bitmap = MagicMock()
        bitmap.width = 25
        bitmap.height = 25

        _show_qr(root, MagicMock(), bitmap, ["OK"])

        # 1 TileGrid + 1 label line = 2 appends
        assert root.append.call_count == 2


class TestShowInterstitial:
    """Structural tests: verify group mutation counts without real rendering."""

    def test_clears_existing_content(self, monkeypatch):
        import portal as portal_module
        monkeypatch.setattr(portal_module, "Label", MagicMock(return_value=MagicMock()))
        root = MagicMock()
        root.__len__ = MagicMock(side_effect=[1, 0])

        _show_interstitial(root, MagicMock(), "Connected!")

        assert root.pop.call_count == 1

    def test_single_string_appends_one_label(self, monkeypatch):
        import portal as portal_module
        monkeypatch.setattr(portal_module, "Label", MagicMock(return_value=MagicMock()))
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)

        _show_interstitial(root, MagicMock(), "Connected!")

        assert root.append.call_count == 1

    def test_list_appends_one_label_per_line(self, monkeypatch):
        import portal as portal_module
        monkeypatch.setattr(portal_module, "Label", MagicMock(return_value=MagicMock()))
        root = MagicMock()
        root.__len__ = MagicMock(return_value=0)

        _show_interstitial(root, MagicMock(), ["Weather", "Panel", "Setup"])

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


# ---------------------------------------------------------------------------
# Settings key mapping
# ---------------------------------------------------------------------------

class TestFieldToKey:
    def test_all_six_fields_present(self):
        expected = {"ssid", "password", "lat", "lon", "temp_scale_range", "temp_midpoint"}
        assert set(FIELD_TO_KEY.keys()) == expected

    def test_ssid_maps_to_wifi_ssid(self):
        assert FIELD_TO_KEY["ssid"] == "CIRCUITPY_WIFI_SSID"

    def test_password_maps_to_wifi_password(self):
        assert FIELD_TO_KEY["password"] == "CIRCUITPY_WIFI_PASSWORD"

    def test_lat_maps_to_latitude(self):
        assert FIELD_TO_KEY["lat"] == "LATITUDE"

    def test_lon_maps_to_longitude(self):
        assert FIELD_TO_KEY["lon"] == "LONGITUDE"

    def test_temp_scale_range_maps_to_temp_scale_range(self):
        assert FIELD_TO_KEY["temp_scale_range"] == "TEMP_SCALE_RANGE"

    def test_temp_midpoint_maps_to_temp_midpoint(self):
        assert FIELD_TO_KEY["temp_midpoint"] == "TEMP_MIDPOINT"


# ---------------------------------------------------------------------------
# merge_settings — pure function
# ---------------------------------------------------------------------------

class TestMergeSettings:
    def test_empty_old_content_appends_new_keys(self):
        result = merge_settings({"ssid": "MyNet", "password": "s3cr3t"}, "")
        assert 'CIRCUITPY_WIFI_SSID = "MyNet"' in result
        assert 'CIRCUITPY_WIFI_PASSWORD = "s3cr3t"' in result

    def test_updates_existing_key_in_place(self):
        old = 'CIRCUITPY_WIFI_SSID = "old"\nOTHER = "keep"\n'
        result = merge_settings({"ssid": "new"}, old)
        assert 'CIRCUITPY_WIFI_SSID = "new"' in result
        assert 'CIRCUITPY_WIFI_SSID = "old"' not in result

    def test_preserves_unrelated_keys(self):
        old = 'GEOLOCATION_API = "http://example.com"\nCIRCUITPY_WIFI_SSID = "x"\n'
        result = merge_settings({"ssid": "new"}, old)
        assert 'GEOLOCATION_API = "http://example.com"' in result

    def test_preserves_comments(self):
        old = '# My config\nCIRCUITPY_WIFI_SSID = "old"\n'
        result = merge_settings({"ssid": "new"}, old)
        assert "# My config" in result

    def test_empty_form_values_not_written(self):
        old = 'CIRCUITPY_WIFI_SSID = "old"\n'
        result = merge_settings({"ssid": "", "password": ""}, old)
        assert 'CIRCUITPY_WIFI_SSID = "old"' in result
        assert "CIRCUITPY_WIFI_PASSWORD" not in result

    def test_whitespace_only_value_treated_as_empty(self):
        old = ""
        result = merge_settings({"lat": "   "}, old)
        assert "LATITUDE" not in result

    def test_appends_key_not_in_original(self):
        old = 'CIRCUITPY_WIFI_SSID = "x"\n'
        result = merge_settings({"lat": "42.39"}, old)
        assert 'LATITUDE = "42.39"' in result

    def test_does_not_match_key_prefix(self):
        # LATITUDE should not match a line for LATITUDE_EXTRA
        old = 'LATITUDE_EXTRA = "junk"\n'
        result = merge_settings({"lat": "42.39"}, old)
        assert 'LATITUDE_EXTRA = "junk"' in result
        assert 'LATITUDE = "42.39"' in result

    def test_all_six_fields_round_trip(self):
        form = {
            "ssid": "HomeNet",
            "password": "hunter2",
            "lat": "42.39",
            "lon": "-71.10",
            "temp_scale_range": "120",
            "temp_midpoint": "55",
        }
        result = merge_settings(form, "")
        assert 'CIRCUITPY_WIFI_SSID = "HomeNet"' in result
        assert 'CIRCUITPY_WIFI_PASSWORD = "hunter2"' in result
        assert 'LATITUDE = "42.39"' in result
        assert 'LONGITUDE = "-71.10"' in result
        assert 'TEMP_SCALE_RANGE = "120"' in result
        assert 'TEMP_MIDPOINT = "55"' in result

    def test_key_updated_only_once_when_appears_multiple_times(self):
        # Malformed file with duplicate key — only first match should be updated,
        # the duplicate is preserved as-is (degenerate input, defined behavior).
        old = 'CIRCUITPY_WIFI_SSID = "first"\nCIRCUITPY_WIFI_SSID = "second"\n'
        result = merge_settings({"ssid": "new"}, old)
        lines = [l for l in result.splitlines() if "CIRCUITPY_WIFI_SSID" in l]
        assert lines[0] == 'CIRCUITPY_WIFI_SSID = "new"'


# ---------------------------------------------------------------------------
# save_settings — I/O wrapper
# ---------------------------------------------------------------------------

class TestSaveSettings:
    def test_writes_merged_content_to_path(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('CIRCUITPY_WIFI_SSID = "old"\n')

        import storage as _storage
        save_settings({"ssid": "new"}, path=str(f))

        assert 'CIRCUITPY_WIFI_SSID = "new"' in f.read_text()

    def test_calls_remount_writable_then_readonly(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text("")

        import storage as _storage
        remount_calls = []
        _storage.remount = lambda path, readonly: remount_calls.append((path, readonly))

        save_settings({"ssid": "net"}, path=str(f))

        assert remount_calls[0] == ("/", False)
        assert remount_calls[1] == ("/", True)

    def test_skips_write_when_content_unchanged(self, tmp_path):
        f = tmp_path / "settings.toml"
        original = 'CIRCUITPY_WIFI_SSID = "same"\n'
        f.write_text(original)
        mtime_before = f.stat().st_mtime

        import storage as _storage
        remount_calls = []
        _storage.remount = lambda path, readonly: remount_calls.append((path, readonly))

        # Submitting the same SSID that's already in the file should not write.
        save_settings({"ssid": "same"}, path=str(f))

        assert remount_calls == []
        assert f.stat().st_mtime == mtime_before

    def test_creates_file_when_missing(self, tmp_path):
        f = tmp_path / "new-settings.toml"

        import storage as _storage
        _storage.remount = MagicMock()

        save_settings({"ssid": "brand-new"}, path=str(f))

        assert f.exists()
        assert 'CIRCUITPY_WIFI_SSID = "brand-new"' in f.read_text()

    def test_reraises_runtime_error_from_remount(self, tmp_path):
        import pytest
        f = tmp_path / "settings.toml"
        f.write_text("")

        import storage as _storage
        _storage.remount = MagicMock(side_effect=RuntimeError("USB connected"))

        with pytest.raises(RuntimeError, match="USB connected"):
            save_settings({"ssid": "net"}, path=str(f))
