"""Tests for the Wi-Fi configuration portal."""
from unittest.mock import MagicMock, call, patch

import pytest
import network
from portal import (
    wifi_qr_data, url_qr_data,
    _show_qr, _show_interstitial, _make_portal_display,
    _ssid_options, _form_html,
    FIELD_TO_KEY, merge_settings, save_settings,
    _should_cycle_reload, AP_CYCLE_S,
    _toml_escape, _has_control_chars, _validate_form_data,
)


# ---------------------------------------------------------------------------
# QR data string generation
# ---------------------------------------------------------------------------

class TestWifiQrData:
    def test_open_network(self):
        assert wifi_qr_data("MyAP") == "WIFI:T:nopass;S:MyAP;;"

    def test_password_protected(self):
        assert wifi_qr_data("MyAP", "s3cret") == "WIFI:T:WPA;S:MyAP;P:s3cret;;"

    def test_plain_characters_not_escaped(self):
        """Characters that don't need escaping pass through unchanged."""
        assert wifi_qr_data("My AP!") == "WIFI:T:nopass;S:My AP!;;"

    def test_empty_password_treated_as_open(self):
        assert wifi_qr_data("Net", "") == "WIFI:T:nopass;S:Net;;"

    def test_none_password_treated_as_open(self):
        assert wifi_qr_data("Net", None) == "WIFI:T:nopass;S:Net;;"

    def test_semicolon_in_ssid_escaped(self):
        assert wifi_qr_data("Net;work") == "WIFI:T:nopass;S:Net\\;work;;"

    def test_backslash_in_ssid_escaped(self):
        assert wifi_qr_data("Net\\work") == "WIFI:T:nopass;S:Net\\\\work;;"

    def test_colon_in_password_escaped(self):
        assert wifi_qr_data("Net", "pass:word") == "WIFI:T:WPA;S:Net;P:pass\\:word;;"

    def test_comma_in_ssid_escaped(self):
        assert wifi_qr_data("Net,work") == "WIFI:T:nopass;S:Net\\,work;;"

    def test_quote_in_password_escaped(self):
        assert wifi_qr_data("Net", 'p"w') == 'WIFI:T:WPA;S:Net;P:p\\"w;;'

    def test_default_ssid_fits_version2_qr_capacity(self):
        """Default AP SSID 'WP' must produce a WIFI: URI within the ~26-byte Version 2 / EC-L limit."""
        from portal import run
        import inspect
        src = inspect.getsource(run)
        # Confirm the literal default is still 'WP' in the source
        assert "'WP'" in src, "Default AP_SSID must be 'WP' to fit in a Version 2 QR code"
        data = wifi_qr_data('WP')
        assert len(data.encode('utf-8')) <= 26, (
            f"Default WIFI: URI is {len(data.encode())} bytes, exceeds 26-byte Version 2 / EC-L limit"
        )


class TestUrlQrData:
    def test_includes_port_80(self):
        assert url_qr_data("192.168.4.1") == "http://192.168.4.1:80"



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
    """Verify _show_qr clears the group before rendering.

    Label is mocked out so this test does not depend on the real font and
    does not leak state into subsequent render tests.  Visual output is
    covered by TestPortalRender in test_portal_render.py.
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


class TestShowInterstitial:
    """Verify _show_interstitial clears the group before rendering.

    Visual output is covered by TestPortalRender in test_portal_render.py.
    """

    def test_clears_existing_content(self, monkeypatch):
        import portal as portal_module
        monkeypatch.setattr(portal_module, "Label", MagicMock(return_value=MagicMock()))
        root = MagicMock()
        root.__len__ = MagicMock(side_effect=[1, 0])

        _show_interstitial(root, MagicMock(), "Connected!")

        assert root.pop.call_count == 1


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

    def test_has_history_years_field(self):
        html = _form_html([])
        assert 'name="history_years"' in html

    def test_posts_to_root(self):
        html = _form_html([])
        assert 'action="/"' in html


# ---------------------------------------------------------------------------
# Settings key mapping
# ---------------------------------------------------------------------------

class TestFieldToKey:
    def test_all_seven_fields_present(self):
        expected = {"ssid", "password", "lat", "lon", "temp_scale_range", "temp_midpoint",
                    "history_years"}
        assert set(FIELD_TO_KEY.keys()) == expected



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

    def test_all_seven_fields_round_trip(self):
        form = {
            "ssid": "HomeNet",
            "password": "hunter2",
            "lat": "42.39",
            "lon": "-71.10",
            "temp_scale_range": "120",
            "temp_midpoint": "55",
            "history_years": "15",
        }
        result = merge_settings(form, "")
        assert 'CIRCUITPY_WIFI_SSID = "HomeNet"' in result
        assert 'CIRCUITPY_WIFI_PASSWORD = "hunter2"' in result
        assert 'LATITUDE = "42.39"' in result
        assert 'LONGITUDE = "-71.10"' in result
        assert 'TEMP_SCALE_RANGE = "120"' in result
        assert 'TEMP_MIDPOINT = "55"' in result
        assert 'HISTORY_YEARS = "15"' in result

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


# ---------------------------------------------------------------------------
# Auto-reload cycle timer
# ---------------------------------------------------------------------------

class TestShouldCycleReload:
    def test_reloads_when_configured_and_time_expired(self):
        assert _should_cycle_reload(True, 0, AP_CYCLE_S)

    def test_reloads_well_past_cycle_time(self):
        assert _should_cycle_reload(True, 0, AP_CYCLE_S + 600)

    def test_does_not_reload_when_wifi_not_configured(self):
        assert not _should_cycle_reload(False, 0, AP_CYCLE_S)

    def test_does_not_reload_before_cycle_time(self):
        assert not _should_cycle_reload(True, 0, AP_CYCLE_S - 1)

    def test_does_not_reload_at_zero_elapsed(self):
        assert not _should_cycle_reload(True, 1000, 1000)


# ---------------------------------------------------------------------------
# TOML escaping
# ---------------------------------------------------------------------------

class TestTomlEscape:
    def test_plain_string_unchanged(self):
        assert _toml_escape("hunter2") == "hunter2"

    def test_escapes_double_quote(self):
        assert _toml_escape('hunt"r2') == r'hunt\"r2'

    def test_escapes_backslash(self):
        assert _toml_escape("p\\ass") == r"p\\ass"

    def test_escapes_newline(self):
        assert _toml_escape("a\nb") == r"a\nb"

    def test_escapes_carriage_return(self):
        assert _toml_escape("a\rb") == r"a\rb"

    def test_escapes_tab(self):
        assert _toml_escape("a\tb") == r"a\tb"

    def test_escapes_null_byte(self):
        assert _toml_escape("a\x00b") == r"a\u0000b"

    def test_escapes_other_control_char(self):
        assert _toml_escape("a\x01b") == r"a\u0001b"

    def test_del_char_escaped(self):
        assert _toml_escape("a\x7fb") == r"a\u007fb"

    def test_non_ascii_printable_unchanged(self):
        assert _toml_escape("café") == "café"

    def test_both_quote_and_backslash(self):
        assert _toml_escape('"\\') == r'\"' + r'\\'


class TestMergeSettingsEscaping:
    def test_escapes_quote_in_password(self):
        result = merge_settings({"password": 'hunt"r2'}, "")
        assert 'CIRCUITPY_WIFI_PASSWORD = "hunt\\"r2"' in result

    def test_escapes_backslash_in_password(self):
        result = merge_settings({"password": "p\\ass"}, "")
        assert 'CIRCUITPY_WIFI_PASSWORD = "p\\\\ass"' in result

    def test_newline_does_not_inject_toml_key(self):
        """A newline in a value must be escaped, not injected as a real TOML key."""
        result = merge_settings({"ssid": "net\nFAKE_KEY = injected"}, "")
        lines = result.splitlines()
        # Exactly one TOML key line containing the SSID key
        ssid_lines = [l for l in lines if "CIRCUITPY_WIFI_SSID" in l]
        assert len(ssid_lines) == 1
        # The newline is escaped as \n inside the quoted string
        assert r"\n" in ssid_lines[0]
        # "FAKE_KEY" must not appear as a bare TOML assignment on its own line
        assert not any(l.startswith("FAKE_KEY") for l in lines)

    def test_null_byte_escaped(self):
        result = merge_settings({"password": "x\x00y"}, "")
        assert r"\u0000" in result
        assert "\x00" not in result


# ---------------------------------------------------------------------------
# Server-side form validation
# ---------------------------------------------------------------------------

class TestHasControlChars:
    def test_plain_string_false(self):
        assert not _has_control_chars("hunter2")

    def test_newline_true(self):
        assert _has_control_chars("pass\nword")

    def test_null_byte_true(self):
        assert _has_control_chars("p\x00ss")

    def test_tab_true(self):
        assert _has_control_chars("p\tss")

    def test_del_true(self):
        assert _has_control_chars("p\x7fss")


class TestValidateFormData:
    def test_valid_submission(self):
        form = {"ssid": "HomeNet", "password": "hunter22",
                "lat": "42.39", "lon": "-71.10"}
        assert _validate_form_data(form) == {}

    def test_missing_ssid_required(self):
        assert "ssid" in _validate_form_data({})

    def test_empty_ssid_required(self):
        assert "ssid" in _validate_form_data({"ssid": ""})

    def test_ssid_too_long(self):
        assert "ssid" in _validate_form_data({"ssid": "x" * 33})

    def test_ssid_exactly_32_bytes_ok(self):
        assert "ssid" not in _validate_form_data({"ssid": "x" * 32})

    def test_ssid_multibyte_bytes_limit(self):
        # Each "é" is 2 bytes; 17 × "é" = 34 bytes > 32
        assert "ssid" in _validate_form_data({"ssid": "é" * 17})

    def test_ssid_control_char_rejected(self):
        assert "ssid" in _validate_form_data({"ssid": "net\nwork"})

    def test_password_too_short(self):
        assert "password" in _validate_form_data({"ssid": "Net", "password": "short"})

    def test_password_exactly_8_chars_ok(self):
        assert "password" not in _validate_form_data({"ssid": "Net", "password": "12345678"})

    def test_password_too_long(self):
        assert "password" in _validate_form_data({"ssid": "Net", "password": "x" * 64})

    def test_password_exactly_63_chars_ok(self):
        assert "password" not in _validate_form_data({"ssid": "Net", "password": "x" * 63})

    def test_password_control_char_rejected(self):
        assert "password" in _validate_form_data({"ssid": "Net", "password": "hunter\x002"})

    def test_password_optional_empty_ok(self):
        assert "password" not in _validate_form_data({"ssid": "Net"})

    def test_lat_non_numeric(self):
        assert "lat" in _validate_form_data({"ssid": "Net", "lat": "notanumber"})

    def test_lat_out_of_range_high(self):
        assert "lat" in _validate_form_data({"ssid": "Net", "lat": "73.0"})

    def test_lat_out_of_range_low(self):
        assert "lat" in _validate_form_data({"ssid": "Net", "lat": "16.9"})

    def test_lat_valid_us(self):
        assert "lat" not in _validate_form_data({"ssid": "Net", "lat": "42.39"})

    def test_lat_optional_empty_ok(self):
        assert "lat" not in _validate_form_data({"ssid": "Net"})

    def test_lon_non_numeric(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lon": "bad"})

    def test_lon_outside_us_east(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lon": "-63.0"})

    def test_lon_outside_us_west(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lon": "0.0"})

    def test_lon_valid_us(self):
        assert "lon" not in _validate_form_data({"ssid": "Net", "lon": "-71.10"})

    def test_temp_scale_range_not_int(self):
        assert "temp_scale_range" in _validate_form_data(
            {"ssid": "Net", "temp_scale_range": "abc"})

    def test_temp_scale_range_out_of_range(self):
        assert "temp_scale_range" in _validate_form_data(
            {"ssid": "Net", "temp_scale_range": "9"})

    def test_temp_scale_range_too_high(self):
        assert "temp_scale_range" in _validate_form_data(
            {"ssid": "Net", "temp_scale_range": "201"})

    def test_temp_scale_range_valid(self):
        assert "temp_scale_range" not in _validate_form_data(
            {"ssid": "Net", "temp_scale_range": "110"})

    def test_history_years_out_of_range(self):
        assert "history_years" in _validate_form_data(
            {"ssid": "Net", "history_years": "45"})

    def test_history_years_valid(self):
        assert "history_years" not in _validate_form_data(
            {"ssid": "Net", "history_years": "10"})

    def test_multiple_errors_returned(self):
        errors = _validate_form_data({"ssid": "", "lat": "bad", "lon": "worse"})
        assert len(errors) >= 3
