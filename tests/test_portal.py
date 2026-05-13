"""Tests for the Wi-Fi configuration portal."""
from unittest.mock import MagicMock

import pytest
import network
from portal import (
    wifi_qr_data, url_qr_data,
    _ssid_options, _form_html,
    FIELD_TO_KEY, KEY_TO_FIELD, _PREFERRED_KEY_ORDER, merge_settings, save_settings,
    _read_settings,
    _toml_escape, _has_control_chars, _validate_form_data,
    _success_html, _mask_password, _usb_error_html,
    _url_decode,
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
    def test_empty_ssid(self):
        config = {'CIRCUITPY_WIFI_SSID': ''}
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
# PortalDisplay class
# ---------------------------------------------------------------------------

class _FakeLabel:
    """Minimal Label stand-in: mutable .text, .color, .y — no rendering."""
    def __init__(self, font, text="", color=0xFFFFFF, x=0, y=0, **kwargs):
        self.font  = font
        self.text  = text
        self.color = color
        self.x     = x
        self.y     = y


@pytest.fixture
def portal_display(monkeypatch):
    """PortalDisplay with matrix and font mocked; Label replaced by _FakeLabel."""
    import matrix as matrix_module
    from adafruit_bitmap_font import bitmap_font
    import portal as portal_module

    monkeypatch.setattr(
        matrix_module, 'display_set_root',
        lambda rg, swapgb=False, bit_depth=6: MagicMock(),
    )
    monkeypatch.setattr(bitmap_font, 'load_font', lambda path: MagicMock())
    monkeypatch.setattr(portal_module, 'Label', _FakeLabel)

    return portal_module.PortalDisplay({})


class TestPortalDisplay:

    # -- __init__ --

    def test_init_calls_matrix_with_bit_depth_1(self, monkeypatch):
        import matrix as matrix_module
        from adafruit_bitmap_font import bitmap_font
        import portal as portal_module

        captured = {}
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda rg, swapgb=False, bit_depth=6: captured.update({'bit_depth': bit_depth}),
        )
        monkeypatch.setattr(bitmap_font, 'load_font', lambda path: MagicMock())
        monkeypatch.setattr(portal_module, 'Label', _FakeLabel)

        portal_module.PortalDisplay({})
        assert captured['bit_depth'] == 1

    def test_init_passes_swapgb_from_config(self, monkeypatch):
        import matrix as matrix_module
        from adafruit_bitmap_font import bitmap_font
        import portal as portal_module

        captured = {}
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda rg, swapgb=False, bit_depth=6: captured.update({'swapgb': swapgb}),
        )
        monkeypatch.setattr(bitmap_font, 'load_font', lambda path: MagicMock())
        monkeypatch.setattr(portal_module, 'Label', _FakeLabel)

        portal_module.PortalDisplay({'SWAP_GREEN_BLUE': True})
        assert captured['swapgb'] is True

    # -- show_text: label reuse --

    def test_show_text_reuses_label_objects(self, portal_display):
        """Labels allocated in __init__ are the same objects after show_text calls."""
        ids_before = [id(lb) for lb in portal_display._text_labels]
        portal_display.show_text(["Hello"])
        portal_display.show_text(["World"])
        ids_after  = [id(lb) for lb in portal_display._text_labels]
        assert ids_before == ids_after

    def test_show_text_updates_text_in_place(self, portal_display):
        portal_display.show_text(["Line 1", "Line 2"])
        assert portal_display._text_labels[0].text == "Line 1"
        assert portal_display._text_labels[1].text == "Line 2"

    def test_show_text_hides_unused_slots(self, portal_display):
        portal_display.show_text(["One"])
        for lb in portal_display._text_labels[1:]:
            assert lb.text == ""

    def test_show_text_accepts_single_string(self, portal_display):
        portal_display.show_text("Connected!")
        assert portal_display._text_labels[0].text == "Connected!"
        for lb in portal_display._text_labels[1:]:
            assert lb.text == ""

    # -- show_text: color logic --

    def test_show_text_default_color_is_white(self, portal_display):
        portal_display.show_text(["Hello"])
        assert portal_display._text_labels[0].color == 0xFFFFFF

    def test_show_text_explicit_color_applied_to_all(self, portal_display):
        portal_display.show_text(["Line1", "Line2"], color=0xFF0000)
        assert portal_display._text_labels[0].color == 0xFF0000
        assert portal_display._text_labels[1].color == 0xFF0000

    def test_show_text_per_line_colors_override_default(self, portal_display):
        portal_display.show_text(
            ["Settings", "saved!", "5..."],
            colors=[0x00AA00, 0x00AA00, 0xFF2200],
        )
        assert portal_display._text_labels[0].color == 0x00AA00
        assert portal_display._text_labels[1].color == 0x00AA00
        assert portal_display._text_labels[2].color == 0xFF2200

    def test_show_text_per_line_colors_shorter_than_lines_falls_back(self, portal_display):
        portal_display.show_text(
            ["A", "B", "C"],
            color=0xFFFFFF,
            colors=[0xFF0000],
        )
        assert portal_display._text_labels[0].color == 0xFF0000
        assert portal_display._text_labels[1].color == 0xFFFFFF
        assert portal_display._text_labels[2].color == 0xFFFFFF

    # -- show_qr --

    def test_show_qr_clears_existing_content(self, portal_display):
        """show_qr clears whatever was showing before rebuilding the QR layout."""
        portal_display.show_text("before")
        content_before = len(portal_display._root_group)
        assert content_before > 0

        bitmap = MagicMock()
        bitmap.width  = 25
        bitmap.height = 25
        portal_display.show_qr(bitmap, ["Scan", "for", "WiFi"])
        # Group is rebuilt — content count changes and all new objects are in place.
        assert len(portal_display._root_group) > 0

    # -- show_countdown --

    def test_show_countdown_updates_only_third_label(self, portal_display):
        portal_display.show_text(["Settings", "saved!", "5..."])
        first_text  = portal_display._text_labels[0].text
        second_text = portal_display._text_labels[1].text

        portal_display.show_countdown(3, [0x00AA00, 0x00AA00, 0xFF2200])

        assert portal_display._text_labels[0].text == first_text
        assert portal_display._text_labels[1].text == second_text
        assert portal_display._text_labels[2].text  == "3..."
        assert portal_display._text_labels[2].color == 0xFF2200

    def test_show_countdown_fallback_color(self, portal_display):
        portal_display.show_text(["a", "b", "c"])
        portal_display.show_countdown(2, [])
        assert portal_display._text_labels[2].color == 0xFFFFFF


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

    def test_lat_lon_labels_say_required(self):
        html = _form_html([])
        assert '(required)' in html

    def test_has_osm_link(self):
        html = _form_html([])
        assert 'openstreetmap.org' in html

    def test_no_navigator_geolocation_js(self):
        html = _form_html([])
        assert 'navigator.geolocation' not in html

    def test_has_temp_scale_fields(self):
        html = _form_html([])
        assert 'name="temp_min"' in html
        assert 'name="temp_max"' in html

    def test_has_history_years_field(self):
        html = _form_html([])
        assert 'name="history_years"' in html

    def test_has_swap_green_blue_checkbox(self):
        html = _form_html([])
        assert 'name="swap_green_blue"' in html
        assert 'type="checkbox"' in html

    def test_swap_green_blue_checkbox_before_hidden(self):
        """Checkbox must precede its hidden sibling so its value is first in the POST body.

        adafruit_httpserver's form_data.get() returns the first value when a field
        appears multiple times — the checkbox (value="1") must come before the hidden
        fallback (value="0") so a checked box wins.
        """
        html = _form_html([])
        cb_pos = html.find('type="checkbox" name="swap_green_blue"')
        hid_pos = html.find('type="hidden" name="swap_green_blue"')
        assert cb_pos != -1 and hid_pos != -1
        assert cb_pos < hid_pos

    def test_has_clock_twentyfour_checkbox(self):
        html = _form_html([])
        assert 'name="clock_twentyfour"' in html

    def test_clock_twentyfour_checkbox_before_hidden(self):
        """Same first-value ordering requirement as swap_green_blue."""
        html = _form_html([])
        cb_pos = html.find('type="checkbox" name="clock_twentyfour"')
        hid_pos = html.find('type="hidden" name="clock_twentyfour"')
        assert cb_pos != -1 and hid_pos != -1
        assert cb_pos < hid_pos

    def test_posts_to_root(self):
        html = _form_html([])
        assert 'action="/"' in html

    def test_has_auto_scale_checkbox(self):
        html = _form_html([])
        assert 'name="auto_scale"' in html

    def test_auto_scale_checkbox_before_hidden(self):
        """Checkbox must precede its hidden sibling so its value is first in the POST body."""
        html = _form_html([])
        cb_pos = html.find('type="checkbox" name="auto_scale"')
        hid_pos = html.find('type="hidden" name="auto_scale"')
        assert cb_pos != -1 and hid_pos != -1
        assert cb_pos < hid_pos

    def test_auto_scale_checked_by_default(self):
        """AUTO_SCALE defaults to True — the checkbox must be checked when no current_values given."""
        html = _form_html([])
        assert 'onchange="_vAutoScale(this.checked)" checked>' in html

    def test_auto_scale_checked_when_saved_as_1(self):
        html = _form_html([], current_values={"auto_scale": "1"})
        # The HTML `checked` attribute closes the checkbox tag: `... checked>`.
        # This distinguishes it from `this.checked` inside the JS onchange handler.
        assert 'onchange="_vAutoScale(this.checked)" checked>' in html

    def test_auto_scale_not_checked_when_saved_as_0(self):
        html = _form_html([], current_values={"auto_scale": "0"})
        assert 'onchange="_vAutoScale(this.checked)" checked>' not in html

    def test_auto_scale_js_helper_present(self):
        html = _form_html([])
        assert "_vAutoScale" in html

    def test_auto_scale_js_called_on_load(self):
        """JS must call _vAutoScale on page load to set the initial disabled state."""
        html = _form_html([])
        assert "_vAutoScale(document.getElementById('auto_scale').checked)" in html


# ---------------------------------------------------------------------------
# Settings key mapping
# ---------------------------------------------------------------------------

class TestFieldToKey:
    def test_all_fields_present(self):
        expected = {"ssid", "password", "lat", "lon", "auto_scale",
                    "temp_min", "temp_max", "history_years",
                    "swap_green_blue", "clock_twentyfour"}
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
        old = 'HISTORICAL_API = "https://example.com"\nCIRCUITPY_WIFI_SSID = "x"\n'
        result = merge_settings({"ssid": "new"}, old)
        assert 'HISTORICAL_API = "https://example.com"' in result

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

    def test_auto_scale_written_when_submitted(self):
        result = merge_settings({"ssid": "Net", "auto_scale": "1"}, "")
        assert 'AUTO_SCALE = "1"' in result

    def test_auto_scale_zero_written_when_unchecked(self):
        result = merge_settings({"ssid": "Net", "auto_scale": "0"}, "")
        assert 'AUTO_SCALE = "0"' in result

    def test_all_seven_fields_round_trip(self):
        form = {
            "ssid": "HomeNet",
            "password": "hunter2",
            "lat": "42.39",
            "lon": "-71.10",
            "temp_min": "-10",
            "temp_max": "110",
            "history_years": "15",
        }
        result = merge_settings(form, "")
        assert 'CIRCUITPY_WIFI_SSID = "HomeNet"' in result
        assert 'CIRCUITPY_WIFI_PASSWORD = "hunter2"' in result
        assert 'LATITUDE = "42.39"' in result
        assert 'LONGITUDE = "-71.10"' in result
        assert 'TEMP_MIN = "-10"' in result
        assert 'TEMP_MAX = "110"' in result
        assert 'HISTORY_YEARS = "15"' in result

    def test_swap_green_blue_enabled_writes_one(self):
        # Checked checkbox sends "1" as first value; hidden sends "0" second.
        result = merge_settings({"ssid": "Net", "swap_green_blue": "1"}, "")
        assert 'SWAP_GREEN_BLUE = "1"' in result

    def test_swap_green_blue_disabled_writes_zero(self):
        # Unchecked checkbox sends no value; only hidden "0" reaches the server.
        result = merge_settings({"ssid": "Net", "swap_green_blue": "0"}, "")
        assert 'SWAP_GREEN_BLUE = "0"' in result

    def test_swap_green_blue_updates_existing_key(self):
        old = 'SWAP_GREEN_BLUE = "0"\n'
        result = merge_settings({"ssid": "Net", "swap_green_blue": "1"}, old)
        assert 'SWAP_GREEN_BLUE = "1"' in result
        assert 'SWAP_GREEN_BLUE = "0"' not in result

    def test_clock_twentyfour_enabled_writes_one(self):
        result = merge_settings({"ssid": "Net", "clock_twentyfour": "1"}, "")
        assert 'CLOCK_TWENTYFOUR = "1"' in result

    def test_clock_twentyfour_disabled_writes_zero(self):
        result = merge_settings({"ssid": "Net", "clock_twentyfour": "0"}, "")
        assert 'CLOCK_TWENTYFOUR = "0"' in result

    def test_key_updated_only_once_when_appears_multiple_times(self):
        # Malformed file with duplicate key — only first match should be updated,
        # the duplicate is preserved as-is (degenerate input, defined behavior).
        old = 'CIRCUITPY_WIFI_SSID = "first"\nCIRCUITPY_WIFI_SSID = "second"\n'
        result = merge_settings({"ssid": "new"}, old)
        lines = [ln for ln in result.splitlines() if "CIRCUITPY_WIFI_SSID" in ln]
        assert lines[0] == 'CIRCUITPY_WIFI_SSID = "new"'

    def test_fresh_file_keys_in_preferred_order(self):
        form = {
            "ssid": "Net", "password": "hunter22",
            "lat": "42.39", "lon": "-71.10",
            "auto_scale": "1",
            "temp_min": "-5", "temp_max": "105",
            "history_years": "10",
            "swap_green_blue": "0", "clock_twentyfour": "1",
        }
        result = merge_settings(form, "")
        keys = [line.split("=")[0].strip() for line in result.splitlines() if "=" in line]
        assert keys == list(_PREFERRED_KEY_ORDER)

    def test_partial_file_appended_keys_in_preferred_order(self):
        # Only SSID pre-exists; remaining keys appended in canonical order.
        old = 'CIRCUITPY_WIFI_SSID = "old"\n'
        form = {"ssid": "new", "lat": "42.39", "temp_min": "-5"}
        result = merge_settings(form, old)
        appended = [
            line.split("=")[0].strip()
            for line in result.splitlines()
            if "=" in line and not line.startswith("CIRCUITPY_WIFI_SSID")
        ]
        assert appended == ["LATITUDE", "TEMP_MIN"]


# ---------------------------------------------------------------------------
# _PREFERRED_KEY_ORDER — structural invariant
# ---------------------------------------------------------------------------

class TestPreferredKeyOrder:
    def test_covers_all_field_to_key_values(self):
        assert set(_PREFERRED_KEY_ORDER) == set(FIELD_TO_KEY.values())

    def test_no_duplicates(self):
        assert len(_PREFERRED_KEY_ORDER) == len(set(_PREFERRED_KEY_ORDER))


# ---------------------------------------------------------------------------
# save_settings — I/O wrapper
# ---------------------------------------------------------------------------

class TestSaveSettings:
    def test_writes_merged_content_to_path(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('CIRCUITPY_WIFI_SSID = "old"\n')

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
# URL decoding
# ---------------------------------------------------------------------------

class TestUrlDecode:
    def test_plain_string_unchanged(self):
        assert _url_decode("hunter2") == "hunter2"

    def test_plus_becomes_space(self):
        assert _url_decode("hello+world") == "hello world"

    def test_hash_decoded(self):
        assert _url_decode("p%23ss") == "p#ss"

    def test_double_quote_decoded(self):
        assert _url_decode("p%22ss") == 'p"ss'

    def test_ampersand_decoded(self):
        assert _url_decode("p%26ss") == "p&ss"

    def test_percent_sign_literal(self):
        assert _url_decode("100%25") == "100%"

    def test_bare_percent_at_end_unchanged(self):
        assert _url_decode("bad%") == "bad%"

    def test_bare_percent_with_one_hex_char_unchanged(self):
        assert _url_decode("bad%2") == "bad%2"

    def test_invalid_hex_percent_unchanged(self):
        assert _url_decode("bad%zz") == "bad%zz"

    def test_multiple_encoded_chars(self):
        assert _url_decode("p%40ss%23word") == "p@ss#word"

    def test_empty_string(self):
        assert _url_decode("") == ""

    def test_hash_password_round_trip_to_toml(self):
        """A password with # survives URL decode → TOML escape → file content."""
        import portal as _portal
        decoded = _url_decode("p%23ss")   # browser sends p#ss as p%23ss
        result = _portal.merge_settings({"ssid": "Net", "password": decoded}, "")
        assert 'CIRCUITPY_WIFI_PASSWORD = "p#ss"' in result

    def test_double_quote_password_round_trip_to_toml(self):
        """A password with \" survives URL decode → TOML escape as \\\"."""
        import portal as _portal
        decoded = _url_decode('p%22ss')   # browser sends p"ss as p%22ss
        result = _portal.merge_settings({"ssid": "Net", "password": decoded}, "")
        assert r'CIRCUITPY_WIFI_PASSWORD = "p\"ss"' in result


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
        ssid_lines = [ln for ln in lines if "CIRCUITPY_WIFI_SSID" in ln]
        assert len(ssid_lines) == 1
        # The newline is escaped as \n inside the quoted string
        assert r"\n" in ssid_lines[0]
        # "FAKE_KEY" must not appear as a bare TOML assignment on its own line
        assert not any(ln.startswith("FAKE_KEY") for ln in lines)

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

    def test_missing_lat_and_lon_both_required(self):
        errors = _validate_form_data({"ssid": "Net"})
        assert "lat" in errors
        assert "lon" in errors

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

    def test_lat_required_when_missing(self):
        assert "lat" in _validate_form_data({"ssid": "Net"})

    def test_lat_required_when_empty(self):
        assert "lat" in _validate_form_data({"ssid": "Net", "lat": ""})

    def test_lat_non_numeric(self):
        assert "lat" in _validate_form_data({"ssid": "Net", "lat": "notanumber"})

    def test_lat_out_of_range_high(self):
        assert "lat" in _validate_form_data({"ssid": "Net", "lat": "73.0"})

    def test_lat_out_of_range_low(self):
        assert "lat" in _validate_form_data({"ssid": "Net", "lat": "16.9"})

    def test_lat_valid_us(self):
        assert "lat" not in _validate_form_data({"ssid": "Net", "lat": "42.39", "lon": "-71.10"})

    def test_lon_required_when_missing(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lat": "42.39"})

    def test_lon_required_when_empty(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lat": "42.39", "lon": ""})

    def test_lon_non_numeric(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lat": "42.39", "lon": "bad"})

    def test_lon_outside_us_east(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lat": "42.39", "lon": "-63.0"})

    def test_lon_outside_us_west(self):
        assert "lon" in _validate_form_data({"ssid": "Net", "lat": "42.39", "lon": "0.0"})

    def test_lon_valid_us(self):
        assert "lon" not in _validate_form_data({"ssid": "Net", "lat": "42.39", "lon": "-71.10"})

    def test_temp_min_not_int(self):
        assert "temp_min" in _validate_form_data(
            {"ssid": "Net", "temp_min": "abc"})

    def test_temp_min_too_low(self):
        assert "temp_min" in _validate_form_data(
            {"ssid": "Net", "temp_min": "-101"})

    def test_temp_max_not_int(self):
        assert "temp_max" in _validate_form_data(
            {"ssid": "Net", "temp_max": "abc"})

    def test_temp_max_too_high(self):
        assert "temp_max" in _validate_form_data(
            {"ssid": "Net", "temp_max": "151"})

    def test_temp_min_max_valid(self):
        assert "temp_min" not in _validate_form_data(
            {"ssid": "Net", "temp_min": "-5", "temp_max": "105"})
        assert "temp_max" not in _validate_form_data(
            {"ssid": "Net", "temp_min": "-5", "temp_max": "105"})

    def test_temp_span_too_small(self):
        assert "temp_max" in _validate_form_data(
            {"ssid": "Net", "temp_min": "50", "temp_max": "81"})   # span=31, just under 32

    def test_temp_span_exactly_32_ok(self):
        assert "temp_max" not in _validate_form_data(
            {"ssid": "Net", "temp_min": "50", "temp_max": "82"})   # span=32, minimum allowed

    def test_temp_span_too_large(self):
        assert "temp_max" in _validate_form_data(
            {"ssid": "Net", "temp_min": "-100", "temp_max": "105"})

    def test_temp_min_above_max(self):
        assert "temp_max" in _validate_form_data(
            {"ssid": "Net", "temp_min": "80", "temp_max": "30"})

    def test_history_years_out_of_range(self):
        assert "history_years" in _validate_form_data(
            {"ssid": "Net", "history_years": "46"})

    def test_history_years_valid(self):
        assert "history_years" not in _validate_form_data(
            {"ssid": "Net", "history_years": "10"})

    def test_swap_green_blue_zero_ok(self):
        assert "swap_green_blue" not in _validate_form_data(
            {"ssid": "Net", "swap_green_blue": "0"})

    def test_swap_green_blue_one_ok(self):
        assert "swap_green_blue" not in _validate_form_data(
            {"ssid": "Net", "swap_green_blue": "1"})

    def test_swap_green_blue_invalid_rejected(self):
        assert "swap_green_blue" in _validate_form_data(
            {"ssid": "Net", "swap_green_blue": "true"})

    def test_swap_green_blue_absent_ok(self):
        assert "swap_green_blue" not in _validate_form_data({"ssid": "Net"})

    def test_clock_twentyfour_zero_ok(self):
        assert "clock_twentyfour" not in _validate_form_data(
            {"ssid": "Net", "clock_twentyfour": "0"})

    def test_clock_twentyfour_one_ok(self):
        assert "clock_twentyfour" not in _validate_form_data(
            {"ssid": "Net", "clock_twentyfour": "1"})

    def test_clock_twentyfour_invalid_rejected(self):
        assert "clock_twentyfour" in _validate_form_data(
            {"ssid": "Net", "clock_twentyfour": "yes"})

    def test_auto_scale_zero_ok(self):
        assert "auto_scale" not in _validate_form_data(
            {"ssid": "Net", "auto_scale": "0"})

    def test_auto_scale_one_ok(self):
        assert "auto_scale" not in _validate_form_data(
            {"ssid": "Net", "auto_scale": "1"})

    def test_auto_scale_invalid_rejected(self):
        assert "auto_scale" in _validate_form_data(
            {"ssid": "Net", "auto_scale": "true"})

    def test_auto_scale_absent_ok(self):
        assert "auto_scale" not in _validate_form_data({"ssid": "Net"})

    def test_multiple_errors_returned(self):
        errors = _validate_form_data({"ssid": "", "lat": "bad", "lon": "worse"})
        assert len(errors) >= 3


# ---------------------------------------------------------------------------
# Success page HTML
# ---------------------------------------------------------------------------

class TestMaskPassword:
    def test_masks_password_value(self):
        content = 'CIRCUITPY_WIFI_PASSWORD = "hunter2"\n'
        result = _mask_password(content)
        assert '"hunter2"' not in result
        assert "CIRCUITPY_WIFI_PASSWORD" in result

    def test_mask_is_fixed_length_regardless_of_actual(self):
        short = _mask_password('CIRCUITPY_WIFI_PASSWORD = "ab"\n')
        long  = _mask_password('CIRCUITPY_WIFI_PASSWORD = "averylongpassword"\n')
        # Extract the quoted value from each result
        def quoted(s):
            line = [ln for ln in s.splitlines() if "CIRCUITPY_WIFI_PASSWORD" in ln][0]
            return line.split("=", 1)[1].strip()
        assert quoted(short) == quoted(long)

    def test_preserves_other_lines(self):
        content = 'CIRCUITPY_WIFI_SSID = "MyNet"\nCIRCUITPY_WIFI_PASSWORD = "s3cr3t"\n'
        result = _mask_password(content)
        assert 'CIRCUITPY_WIFI_SSID = "MyNet"' in result

    def test_no_password_line_unchanged(self):
        content = 'CIRCUITPY_WIFI_SSID = "MyNet"\n'
        assert _mask_password(content) == content

    def test_empty_content_unchanged(self):
        assert _mask_password("") == ""


class TestSuccessHtml:
    def test_contains_heading(self):
        assert "Settings saved" in _success_html("x = 1\n")

    def test_mentions_restarting(self):
        assert "restarting" in _success_html("x = 1\n")

    def test_mentions_reconfigure_hint(self):
        assert "Reset" in _success_html("x = 1\n")
        assert "Up or Down" in _success_html("x = 1\n")

    def test_password_masked_in_output(self):
        content = 'CIRCUITPY_WIFI_SSID = "Net"\nCIRCUITPY_WIFI_PASSWORD = "s3cr3t"\n'
        body = _success_html(content)
        assert "s3cr3t" not in body
        assert "CIRCUITPY_WIFI_PASSWORD" in body

    def test_ssid_not_masked(self):
        content = 'CIRCUITPY_WIFI_SSID = "MyNetwork"\nCIRCUITPY_WIFI_PASSWORD = "pw"\n'
        body = _success_html(content)
        assert "MyNetwork" in body

    def test_content_displayed(self):
        body = _success_html('SSID = "home"\n')
        assert 'SSID = "home"' in body

    def test_html_special_chars_escaped(self):
        body = _success_html('PW = "<b>&amp;</b>"\n')
        assert "&lt;b&gt;" in body
        assert "&amp;amp;" in body

    def test_empty_content_renders(self):
        body = _success_html("")
        assert "Settings saved" in body
        assert "<pre><code>" in body


# ---------------------------------------------------------------------------
# USB error page
# ---------------------------------------------------------------------------

class TestUsbErrorHtml:
    def test_contains_cannot_save_heading(self):
        assert "Cannot save" in _usb_error_html()

    def test_mentions_power_supply(self):
        assert "power supply" in _usb_error_html()

    def test_mentions_not_a_computer(self):
        assert "not a computer" in _usb_error_html()

    def test_instructs_to_eject_circuitpy(self):
        assert "Eject the CIRCUITPY drive" in _usb_error_html()

    def test_offers_direct_edit_alternative(self):
        body = _usb_error_html()
        assert "settings.toml" in body
        assert "directly" in body


# ---------------------------------------------------------------------------
# KEY_TO_FIELD reverse mapping
# ---------------------------------------------------------------------------

class TestKeyToField:
    def test_is_exact_reverse_of_field_to_key(self):
        for field, key in FIELD_TO_KEY.items():
            assert KEY_TO_FIELD[key] == field

    def test_same_length(self):
        assert len(KEY_TO_FIELD) == len(FIELD_TO_KEY)


# ---------------------------------------------------------------------------
# _read_settings — settings.toml parser
# ---------------------------------------------------------------------------

class TestReadSettings:
    def test_reads_quoted_string_values(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('CIRCUITPY_WIFI_SSID = "HomeNet"\n')
        assert _read_settings(str(f)) == {"CIRCUITPY_WIFI_SSID": "HomeNet"}

    def test_reads_multiple_keys(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('CIRCUITPY_WIFI_SSID = "Net"\nLATITUDE = "42.39"\n')
        result = _read_settings(str(f))
        assert result["CIRCUITPY_WIFI_SSID"] == "Net"
        assert result["LATITUDE"] == "42.39"

    def test_skips_comment_lines(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('# a comment\nCIRCUITPY_WIFI_SSID = "Net"\n')
        result = _read_settings(str(f))
        assert "CIRCUITPY_WIFI_SSID" in result
        assert len(result) == 1

    def test_skips_blank_lines(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('\n\nCIRCUITPY_WIFI_SSID = "Net"\n\n')
        assert _read_settings(str(f)) == {"CIRCUITPY_WIFI_SSID": "Net"}

    def test_skips_integer_literal_lines(self, tmp_path):
        # Integer-valued keys (e.g., SWAP_GREEN_BLUE = 0) aren't double-quoted
        # and are written by the portal as strings, but handle bare integers
        # gracefully by ignoring them.
        f = tmp_path / "settings.toml"
        f.write_text('SWAP_GREEN_BLUE = 0\nCIRCUITPY_WIFI_SSID = "Net"\n')
        result = _read_settings(str(f))
        assert "SWAP_GREEN_BLUE" not in result
        assert result["CIRCUITPY_WIFI_SSID"] == "Net"

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        assert _read_settings(str(tmp_path / "nonexistent.toml")) == {}

    def test_value_with_escaped_quote_preserved_raw(self, tmp_path):
        # _read_settings returns the raw (still-escaped) string; unescaping
        # is the browser/form's responsibility.
        f = tmp_path / "settings.toml"
        f.write_text(r'CIRCUITPY_WIFI_PASSWORD = "hunt\"r2"' + "\n")
        result = _read_settings(str(f))
        assert result["CIRCUITPY_WIFI_PASSWORD"] == r'hunt\"r2'

    def test_empty_file_returns_empty_dict(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text("")
        assert _read_settings(str(f)) == {}


# ---------------------------------------------------------------------------
# _ssid_options — configured-but-missing SSID handling
# ---------------------------------------------------------------------------

class TestSsidOptionsConfiguredSsid:
    def test_configured_ssid_in_scan_is_selected(self):
        html = _ssid_options([("HomeNet", -45), ("Other", -70)], configured_ssid="HomeNet")
        assert '<option value="HomeNet" selected>' in html

    def test_configured_ssid_not_in_scan_prepended_selected(self):
        html = _ssid_options([("Other", -70)], configured_ssid="HomeNet")
        assert html.index("(not visible) HomeNet") < html.index("Other")
        assert 'value="HomeNet" selected' in html

    def test_configured_ssid_not_in_scan_other_networks_still_listed(self):
        html = _ssid_options([("Other", -70)], configured_ssid="HomeNet")
        assert "Other" in html

    def test_no_configured_ssid_none_selected_by_default(self):
        html = _ssid_options([("HomeNet", -45)], configured_ssid=None)
        assert "selected" not in html

    def test_empty_configured_ssid_treated_as_none(self):
        html = _ssid_options([("HomeNet", -45)], configured_ssid="")
        assert "not visible" not in html
        assert "selected" not in html

    def test_html_escape_in_not_visible_option(self):
        html = _ssid_options([], configured_ssid='Net<work>')
        assert "Net&lt;work&gt;" in html
        assert "<work>" not in html

    def test_no_configured_ssid_no_extra_option(self):
        html = _ssid_options([("HomeNet", -45)])
        assert "not visible" not in html

    def test_configured_ssid_in_scan_no_not_visible_option(self):
        html = _ssid_options([("HomeNet", -45)], configured_ssid="HomeNet")
        assert "not visible" not in html


# ---------------------------------------------------------------------------
# _form_html — pre-population and error display
# ---------------------------------------------------------------------------

class TestFormHtmlPrePopulation:
    def test_lat_lon_pre_filled(self):
        html = _form_html([], current_values={"lat": "42.39", "lon": "-71.10"})
        assert 'value="42.39"' in html
        assert 'value="-71.10"' in html

    def test_temp_min_max_pre_filled(self):
        html = _form_html([], current_values={"temp_min": "-10", "temp_max": "90"})
        assert 'value="-10"' in html
        assert 'value="90"' in html

    def test_history_years_pre_filled(self):
        html = _form_html([], current_values={"history_years": "15"})
        assert 'value="15"' in html

    def test_swap_green_blue_checked_when_enabled(self):
        html = _form_html([], current_values={"swap_green_blue": "1"})
        # The checkbox for swap_green_blue must be checked.
        idx_cb = html.index('name="swap_green_blue"')
        assert "checked" in html[idx_cb:idx_cb + 80]

    def test_swap_green_blue_not_checked_when_disabled(self):
        html = _form_html([], current_values={"swap_green_blue": "0"})
        idx_cb = html.index('name="swap_green_blue"')
        snippet = html[idx_cb:idx_cb + 80]
        assert "checked" not in snippet

    def test_clock_twentyfour_checked_when_enabled(self):
        html = _form_html([], current_values={"clock_twentyfour": "1"})
        idx_cb = html.index('name="clock_twentyfour"')
        assert "checked" in html[idx_cb:idx_cb + 80]

    def test_defaults_used_when_no_current_values(self):
        html = _form_html([])
        assert 'value="-5"' in html
        assert 'value="105"' in html
        assert 'value="10"' in html

    def test_password_not_pre_filled(self):
        # Password is never pre-populated for security.
        html = _form_html([], current_values={"password": "s3cret"})
        assert "s3cret" not in html

    def test_ssid_passed_to_options_for_selection(self):
        html = _form_html(
            [("HomeNet", -45)],
            current_values={"ssid": "HomeNet"},
        )
        assert '<option value="HomeNet" selected>' in html

    def test_configured_ssid_not_in_scan_shows_warning(self):
        html = _form_html(
            [("Other", -60)],
            current_values={"ssid": "HomeNet"},
        )
        assert "not visible" in html
        assert "HomeNet" in html


class TestFormHtmlConfigErrors:
    def test_banner_shown_when_config_errors(self):
        html = _form_html([], config_errors={"temp_min": "TEMP_MIN must be a whole number"})
        assert "TEMP_MIN must be a whole number" in html
        assert "banner" in html

    def test_no_banner_when_no_errors(self):
        html = _form_html([])
        assert '<div class="banner">' not in html

    def test_error_message_shown_inline_for_temp_min(self):
        html = _form_html([], config_errors={"temp_min": "Must be a whole number."})
        assert "Must be a whole number." in html

    def test_error_message_shown_inline_for_history_years(self):
        html = _form_html([], config_errors={"history_years": "Bad value."})
        assert "Bad value." in html

    def test_advanced_section_open_when_advanced_field_has_error(self):
        html = _form_html([], config_errors={"temp_min": "Bad."})
        # <details open> should appear (not just <details>)
        assert "<details open>" in html or "<details  open>" in html or "details open" in html

    def test_advanced_section_not_open_without_errors(self):
        html = _form_html([])
        assert "<details>" in html
        assert "<details open>" not in html

    def test_error_html_escaped(self):
        html = _form_html([], config_errors={"temp_min": '<script>alert(1)</script>'})
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html
