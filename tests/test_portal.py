"""Tests for the Wi-Fi configuration portal."""
import threading
from unittest.mock import MagicMock

import pytest
import network
from portal import (
    wifi_qr_data,
    _ssid_options, _form_html,
    _PREFERRED_KEY_ORDER, merge_settings, save_settings,
    COLORS_FIELD_TO_KEY, _COLORS_KEY_ORDER, merge_colors, save_all,
    _toml_escape, _has_control_chars, _validate_form_data,
    _success_html, _mask_password, _url_decode,
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
        data = wifi_qr_data('WP')
        assert len(data.encode('utf-8')) <= 26, (
            f"Default WIFI: URI is {len(data.encode())} bytes, exceeds 26-byte Version 2 / EC-L limit"
        )


# ---------------------------------------------------------------------------
# Wi-Fi configured detection
# ---------------------------------------------------------------------------

class TestWifiConfigured:
    def test_real_ssid(self):
        config = {'CIRCUITPY_WIFI_SSID': 'HomeNetwork'}
        assert network.wifi_configured(config)

    def test_missing_key(self):
        assert not network.wifi_configured({})


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
    import base_display as base_display_module

    monkeypatch.setattr(
        matrix_module, 'display_set_root',
        lambda rg, swapgb=False, bit_depth=6: MagicMock(),
    )
    monkeypatch.setattr(bitmap_font, 'load_font', lambda path: MagicMock())
    monkeypatch.setattr(base_display_module, 'Label', _FakeLabel)

    return portal_module.PortalDisplay({})



class TestPortalDisplay:

    # -- __init__ --

    def test_init_calls_matrix_with_bit_depth_1(self, monkeypatch):
        import matrix as matrix_module
        from adafruit_bitmap_font import bitmap_font
        import portal as portal_module
        import base_display as base_display_module

        captured = {}
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda rg, swapgb=False, bit_depth=6: captured.update({'bit_depth': bit_depth}),
        )
        monkeypatch.setattr(bitmap_font, 'load_font', lambda path: MagicMock())
        monkeypatch.setattr(base_display_module, 'Label', _FakeLabel)

        portal_module.PortalDisplay({})
        assert captured['bit_depth'] == 1

    def test_init_passes_swapgb_from_config(self, monkeypatch):
        import matrix as matrix_module
        from adafruit_bitmap_font import bitmap_font
        import portal as portal_module
        import base_display as base_display_module

        captured = {}
        monkeypatch.setattr(
            matrix_module, 'display_set_root',
            lambda rg, swapgb=False, bit_depth=6: captured.update({'swapgb': swapgb}),
        )
        monkeypatch.setattr(bitmap_font, 'load_font', lambda path: MagicMock())
        monkeypatch.setattr(base_display_module, 'Label', _FakeLabel)

        portal_module.PortalDisplay({'SWAP_GREEN_BLUE': True})
        assert captured['swapgb'] is True

    # -- screen state --

    def test_initial_screen_is_setup_intro(self, portal_display):
        import portal as portal_module
        assert portal_display.screen == portal_module.PortalDisplay.SCREEN_SETUP_INTRO

    # -- group visibility toggling --

    def test_text_screen_shows_status_group_hides_qr_group(self, portal_display):
        portal_display.show_connected()
        assert portal_display._status_group.hidden is False
        assert portal_display._qr_group.hidden     is True

    def test_qr_screen_shows_qr_group_hides_status_group(self, portal_display):
        import portal as portal_module
        bitmap = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        portal_display.show_wifi_qr(bitmap)
        assert portal_display._qr_group.hidden     is False
        assert portal_display._status_group.hidden is True

    def test_switching_from_qr_to_text_toggles_visibility(self, portal_display):
        import portal as portal_module
        bitmap = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        portal_display.show_wifi_qr(bitmap)
        portal_display.show_connected()
        assert portal_display._status_group.hidden is False
        assert portal_display._qr_group.hidden     is True

    # -- flush on every screen transition --
    # In bin/simulate --portal, SimDisplay.refresh() is patched to emit frames.
    # A missing flush() call means the display silently never updates on screen.
    # These spy tests enforce the call at the unit level.

    def test_text_screen_calls_flush(self, portal_display):
        """show_*() for text screens must call flush() so the display updates."""
        refresh_count = [0]
        portal_display._display.refresh = lambda: refresh_count.__setitem__(0, refresh_count[0] + 1)
        portal_display.show_connected()
        assert refresh_count[0] >= 1, "show_connected() must call flush()"

    def test_qr_screen_calls_flush(self, portal_display):
        """show_wifi_qr() must call flush() so the display updates."""
        import portal as portal_module
        bitmap = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        refresh_count = [0]
        portal_display._display.refresh = lambda: refresh_count.__setitem__(0, refresh_count[0] + 1)
        portal_display.show_wifi_qr(bitmap)
        assert refresh_count[0] >= 1, "show_wifi_qr() must call flush()"

    # -- label reuse (no new allocations on transition) --

    def test_text_label_objects_reused_across_text_screens(self, portal_display):
        """show_connected() then show_setup_intro() must reuse the same Label objects."""
        ids_before = [id(lb) for lb in portal_display._text_labels]
        portal_display.show_connected()
        portal_display.show_setup_intro()
        ids_after  = [id(lb) for lb in portal_display._text_labels]
        assert ids_before == ids_after

    def test_qr_label_objects_reused_across_qr_screens(self, portal_display):
        """show_wifi_qr() then show_url_qr() must reuse the same QR Label objects."""
        import portal as portal_module
        wifi_bmp = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        url_bmp  = portal_module.make_qr_bitmap(portal_module.url_qr_data("192.168.4.1"))
        portal_display.show_wifi_qr(wifi_bmp)
        ids_after_wifi = [id(lb) for lb in portal_display._qr_labels]
        portal_display.show_url_qr(url_bmp)
        ids_after_url  = [id(lb) for lb in portal_display._qr_labels]
        assert ids_after_wifi == ids_after_url

    def test_qr_tilegrid_reused_across_qr_screens(self, portal_display):
        """The TileGrid must be the same object after both QR screen calls."""
        import portal as portal_module
        wifi_bmp = portal_module.make_qr_bitmap(portal_module.wifi_qr_data("WP"))
        url_bmp  = portal_module.make_qr_bitmap(portal_module.url_qr_data("192.168.4.1"))
        portal_display.show_wifi_qr(wifi_bmp)
        grid_id_wifi = id(portal_display._qr_grid)
        portal_display.show_url_qr(url_bmp)
        grid_id_url  = id(portal_display._qr_grid)
        assert grid_id_wifi == grid_id_url


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

    def test_auto_scale_js_called_on_load(self):
        """JS must call _vAutoScale on page load to set the initial disabled state."""
        html = _form_html([])
        assert "_vAutoScale(document.getElementById('auto_scale').checked)" in html


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
    def test_plus_becomes_space(self):
        assert _url_decode("hello+world") == "hello world"

    def test_hash_decoded(self):
        assert _url_decode("p%23ss") == "p#ss"

    def test_double_quote_decoded(self):
        assert _url_decode("p%22ss") == 'p"ss'

    def test_percent_sign_literal(self):
        assert _url_decode("100%25") == "100%"

    def test_bare_percent_at_end_unchanged(self):
        assert _url_decode("bad%") == "bad%"

    def test_bare_percent_with_one_hex_char_unchanged(self):
        assert _url_decode("bad%2") == "bad%2"

    def test_invalid_hex_percent_unchanged(self):
        assert _url_decode("bad%zz") == "bad%zz"

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

    def test_del_true(self):
        """DEL (0x7F) is the other special case in the condition."""
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

    def test_password_exactly_63_chars_ok(self):
        assert "password" not in _validate_form_data({"ssid": "Net", "password": "x" * 63})

    def test_password_control_char_rejected(self):
        assert "password" in _validate_form_data({"ssid": "Net", "password": "hunter\x002"})

    def test_password_optional_empty_ok(self):
        assert "password" not in _validate_form_data({"ssid": "Net"})

    def test_lat_required_when_missing(self):
        assert "lat" in _validate_form_data({"ssid": "Net"})

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

    def test_swap_green_blue_one_ok(self):
        assert "swap_green_blue" not in _validate_form_data(
            {"ssid": "Net", "swap_green_blue": "1"})

    def test_swap_green_blue_invalid_rejected(self):
        assert "swap_green_blue" in _validate_form_data(
            {"ssid": "Net", "swap_green_blue": "true"})

    def test_swap_green_blue_absent_ok(self):
        assert "swap_green_blue" not in _validate_form_data({"ssid": "Net"})

    def test_clock_twentyfour_one_ok(self):
        assert "clock_twentyfour" not in _validate_form_data(
            {"ssid": "Net", "clock_twentyfour": "1"})

    def test_clock_twentyfour_invalid_rejected(self):
        assert "clock_twentyfour" in _validate_form_data(
            {"ssid": "Net", "clock_twentyfour": "yes"})

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
    def test_password_masked_in_output(self):
        content = 'CIRCUITPY_WIFI_SSID = "Net"\nCIRCUITPY_WIFI_PASSWORD = "s3cr3t"\n'
        body = _success_html(content)
        assert "s3cr3t" not in body
        assert "CIRCUITPY_WIFI_PASSWORD" in body

    def test_html_special_chars_escaped(self):
        body = _success_html('PW = "<b>&amp;</b>"\n')
        assert "&lt;b&gt;" in body
        assert "&amp;amp;" in body


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


# ---------------------------------------------------------------------------
# merge_colors
# ---------------------------------------------------------------------------

class TestMergeColors:
    def test_appends_new_key(self):
        result = merge_colors({"temp_color_cold": "#ff0000"}, "")
        assert 'TEMP_COLOR_COLD = "0xff0000"' in result

    def test_updates_existing_key(self):
        old = 'TEMP_COLOR_COLD = "0x143cd2"\n'
        result = merge_colors({"temp_color_cold": "#ff0000"}, old)
        assert 'TEMP_COLOR_COLD = "0xff0000"' in result
        assert '0x143cd2' not in result

    def test_preserves_other_lines(self):
        old = '# a comment\nTEMP_COLOR_WARM = "0xff4800"\n'
        result = merge_colors({"temp_color_cold": "#0000ff"}, old)
        assert '# a comment' in result
        assert 'TEMP_COLOR_WARM = "0xff4800"' in result

    def test_empty_field_value_ignored(self):
        old = 'TEMP_COLOR_COLD = "0x143cd2"\n'
        result = merge_colors({"temp_color_cold": ""}, old)
        assert result == old

    def test_all_keys_appended_in_canonical_order(self):
        form = {f: "#aabbcc" for f in COLORS_FIELD_TO_KEY}
        result = merge_colors(form, "")
        positions = [result.index(k) for k in _COLORS_KEY_ORDER]
        assert positions == sorted(positions)

    def test_normalises_hash_to_0x(self):
        result = merge_colors({"comfort_color": "#0a3c00"}, "")
        assert '= "0x0a3c00"' in result

    def test_preserves_non_color_lines(self):
        old = 'CIRCUITPY_WIFI_SSID = "MyNet"\n'
        result = merge_colors({"temp_color_cold": "#123456"}, old)
        assert 'CIRCUITPY_WIFI_SSID = "MyNet"' in result


# ---------------------------------------------------------------------------
# save_all
# ---------------------------------------------------------------------------

class TestSaveAll:
    def test_writes_settings_and_colors(self, tmp_path):
        import storage as _storage
        _storage.remount = MagicMock()

        s = tmp_path / "settings.toml"
        c = tmp_path / "colors.toml"
        s.write_text("")
        c.write_text("")

        save_all({"ssid": "Net"}, {"temp_color_cold": "#ff0000"},
                 settings_path=str(s), colors_path=str(c))

        assert 'CIRCUITPY_WIFI_SSID = "Net"' in s.read_text()
        assert 'TEMP_COLOR_COLD = "0xff0000"' in c.read_text()

    def test_single_remount_cycle(self, tmp_path):
        import storage as _storage
        calls = []
        _storage.remount = lambda path, readonly: calls.append((path, readonly))

        s = tmp_path / "settings.toml"
        c = tmp_path / "colors.toml"
        s.write_text("")
        c.write_text("")

        save_all({"ssid": "Net"}, {"temp_color_cold": "#ff0000"},
                 settings_path=str(s), colors_path=str(c))

        assert calls == [("/", False), ("/", True)]

    def test_no_write_when_both_unchanged(self, tmp_path):
        import storage as _storage
        calls = []
        _storage.remount = lambda path, readonly: calls.append((path, readonly))

        s = tmp_path / "settings.toml"
        c = tmp_path / "colors.toml"
        s.write_text('CIRCUITPY_WIFI_SSID = "same"\n')
        c.write_text('TEMP_COLOR_COLD = "0xff0000"\n')

        save_all({"ssid": "same"}, {"temp_color_cold": "#ff0000"},
                 settings_path=str(s), colors_path=str(c))

        assert calls == []

# ---------------------------------------------------------------------------
# Color validation in _validate_form_data
# ---------------------------------------------------------------------------

class TestValidateColorFields:
    def _base_form(self):
        """Minimal valid settings form with no color fields."""
        return {
            'ssid': 'TestNet',
            'password': '',
            'lat': '42.39',
            'lon': '-71.11',
        }

    def test_valid_hash_color_accepted(self):
        form = {**self._base_form(), 'temp_color_cold': '#143cd2'}
        errors = _validate_form_data(form)
        assert 'temp_color_cold' not in errors

    def test_valid_0x_color_accepted(self):
        form = {**self._base_form(), 'temp_color_cold': '0x143cd2'}
        errors = _validate_form_data(form)
        assert 'temp_color_cold' not in errors

    def test_empty_color_accepted(self):
        """Empty value means 'keep default' — not an error."""
        form = {**self._base_form(), 'temp_color_cold': ''}
        errors = _validate_form_data(form)
        assert 'temp_color_cold' not in errors

    def test_invalid_color_rejected(self):
        form = {**self._base_form(), 'temp_color_cold': 'notacolor'}
        errors = _validate_form_data(form)
        assert 'temp_color_cold' in errors

    def test_short_hex_rejected(self):
        form = {**self._base_form(), 'temp_color_cold': '#abc'}
        errors = _validate_form_data(form)
        assert 'temp_color_cold' in errors

    def test_all_16_color_fields_validated(self):
        """All color fields are checked — bad value in any one produces an error."""
        for field in COLORS_FIELD_TO_KEY:
            form = {**self._base_form(), field: 'bad'}
            errors = _validate_form_data(form)
            assert field in errors, f"expected error for bad {field}"


# ---------------------------------------------------------------------------
# Colors section in _form_html
# ---------------------------------------------------------------------------

class TestFormHtmlColors:
    def _html(self, current_colors=None):
        return _form_html([], current_colors=current_colors)

    def test_colors_details_section_present(self):
        assert '<summary>Colors</summary>' in self._html()

    def test_all_color_inputs_present(self):
        html = self._html()
        for field in COLORS_FIELD_TO_KEY:
            assert f'name="{field}"' in html, f"missing input for {field}"

    def test_default_values_pre_filled(self):
        """Color inputs show default values when no current_colors provided."""
        from appconfig import COLOR_DEFAULTS
        html = self._html()
        cold_html = f"#{COLOR_DEFAULTS['TEMP_COLOR_COLD']:06x}"
        assert cold_html in html

    def test_current_color_value_pre_filled(self):
        html = self._html(current_colors={"temp_color_cold": "0xff0000"})
        assert '#ff0000' in html

# ---------------------------------------------------------------------------
# show_setup_intro() lines= parameter
# ---------------------------------------------------------------------------

class TestShowCountdown:
    """show_countdown() must apply _COUNTDOWN_COLORS to the digit label (slot 3)."""

    def test_digit_color_uses_countdown_colors(self, portal_display):
        """After show_countdown_start() + show_countdown(n, ...) the digit label
        must be colored from _COUNTDOWN_COLORS, not fall back to white.

        Regression guard: the call site previously passed a 3-element list, so
        len(colors) > 3 was always False and _text_labels[3].color was never
        updated from 0xFFFFFF.
        """
        import portal as portal_module

        portal_display.show_countdown_start()
        # Replicate the call that portal.run() makes for i == SAVE_COUNTDOWN_S-1
        # (second tick — index 1 into _COUNTDOWN_COLORS).
        color_index = 1
        expected_color = portal_module._COUNTDOWN_COLORS[color_index]
        portal_display.show_countdown(
            portal_module.SAVE_COUNTDOWN_S - 1,
            [0x00AA00, 0x00AA00, 0x00AA00, expected_color],
        )

        assert portal_display._text_labels[3].color == expected_color, (
            "show_countdown() did not update the digit color — "
            "colors list may be too short (len ≤ 3)"
        )

    def test_digit_color_fallback_when_short_list(self, portal_display):
        """A list with ≤ 3 elements falls back to white (0xFFFFFF)."""
        portal_display.show_countdown_start()
        portal_display.show_countdown(3, [0x00AA00, 0x00AA00, 0x00AA00])
        assert portal_display._text_labels[3].color == 0xFFFFFF


class TestSubmitBodySizeLimit:
    """The submit() route handler must reject POST bodies exceeding MAX_POST_BODY_BYTES."""

    @staticmethod
    def _capture_submit(monkeypatch):
        """Call _make_server() and return the captured POST '/' handler.

        Patches portal.Server so that @server.route(...) acts as an identity
        decorator, leaving the actual handler functions accessible for direct
        testing rather than being swallowed by MagicMock's default decorator
        behavior.
        """
        import portal as portal_module

        registered = []
        mock_server = MagicMock()
        mock_server.route.side_effect = (
            lambda path, method: (lambda fn: registered.append(fn) or fn)
        )
        monkeypatch.setattr(portal_module, "Server", MagicMock(return_value=mock_server))

        portal_module._make_server("0.0.0.0", [])

        # Handlers registered in order:
        #   0 — index  (GET /)
        #   1 — scan   (GET /scan)
        #   2 — submit (POST /)
        assert len(registered) >= 3, "Expected at least 3 route handlers from _make_server"
        return registered[2]

    def test_returns_413_when_content_length_exceeds_limit(self, monkeypatch):
        """A Content-Length header above MAX_POST_BODY_BYTES must produce a 413 Response."""
        import portal as portal_module

        portal_module.Response.reset_mock()
        submit = self._capture_submit(monkeypatch)

        req = MagicMock()
        req.headers.get.return_value = str(portal_module.MAX_POST_BODY_BYTES + 1)

        submit(req)

        portal_module.Response.assert_called_with(req, "Request too large", status=413)

    def test_no_413_when_content_length_absent(self, monkeypatch):
        """A missing Content-Length header must not be treated as oversized."""
        import portal as portal_module

        portal_module.Response.reset_mock()
        submit = self._capture_submit(monkeypatch)

        req = MagicMock()
        req.headers.get.return_value = None  # no Content-Length

        try:
            submit(req)
        except (TypeError, AttributeError):
            # The handler may fail downstream when MagicMock form_data is passed
            # to _url_decode — that is expected in this minimal test harness.
            # The only behavior under test is that a 413 was NOT returned.
            pass

        for call in portal_module.Response.call_args_list:
            assert call.kwargs.get("status") != 413, (
                "submit() returned 413 when Content-Length header was absent"
            )


class TestShowSetupIntroLines:
    """Tests for the optional lines= parameter of show_setup_intro()."""

    def test_default_lines_show_setup_text(self, portal_display):
        portal_display.show_setup_intro()
        texts = [lb.text for lb in portal_display._text_labels]
        assert texts == ["", "Weather", "Panel", "Setup"]

    def test_none_falls_back_to_default(self, portal_display):
        portal_display.show_setup_intro(None)
        texts = [lb.text for lb in portal_display._text_labels]
        assert texts == ["", "Weather", "Panel", "Setup"]

    def test_custom_lines_displayed(self, portal_display):
        portal_display.show_setup_intro(["", "Wi-Fi", "failed", ""])
        texts = [lb.text for lb in portal_display._text_labels]
        assert texts == ["", "Wi-Fi", "failed", ""]

    def test_screen_stays_setup_intro_with_custom_lines(self, portal_display):
        import portal as portal_module
        portal_display.show_setup_intro(["a", "b", "c", "d"])
        assert portal_display.screen == portal_module.PortalDisplay.SCREEN_SETUP_INTRO


# ---------------------------------------------------------------------------
# portal.run() recovery= parameter
# ---------------------------------------------------------------------------

class _PortalDone(Exception):
    """Raised by the mocked supervisor.reload() to exit portal.run() in tests."""


class TestPortalRecovery:
    """Tests for portal.run(recovery=) — whether Wi-Fi retry fires.

    Each test runs portal.run() in a daemon thread with all hardware mocked
    out.  AP_CYCLE_S is patched to 0 so the retry condition fires on the
    first loop iteration without waiting 30 seconds.  SAVE_COUNTDOWN_S is
    patched to 0 so the countdown after reload_pending is set exits instantly.
    """

    def _setup_mocks(self, monkeypatch, *, reload_pending=False):
        """Wire all portal.run() dependencies as no-ops or minimal stubs.

        Returns ``(connect_calls, server_state)`` so callers can assert on
        whether network.connect() was called and control portal exit timing.
        """
        import portal as portal_module
        import network as network_module
        import supervisor as supervisor_module
        import wifi as wifi_module

        # Speed up: no retry delay, no countdown delay, no interstitial sleep.
        monkeypatch.setattr(portal_module, 'AP_CYCLE_S', 0)
        monkeypatch.setattr(portal_module, 'SAVE_COUNTDOWN_S', 0)
        monkeypatch.setattr(portal_module, 'sleep', lambda _: None)

        # Display: stub out so no hardware or font setup is needed.
        monkeypatch.setattr(portal_module, 'PortalDisplay', lambda cfg: MagicMock())

        # Network helpers.
        monkeypatch.setattr(network_module, 'start_ap', lambda ssid, password=None: None)
        monkeypatch.setattr(network_module, 'ap_ip', lambda: "192.168.4.1")
        monkeypatch.setattr(network_module, 'scan_networks', lambda: [])

        # connect() records calls and auto-connects so the portal sees success.
        connect_calls = []
        def _fake_connect(cfg):
            connect_calls.append(True)
            wifi_module.radio.connected = True

        monkeypatch.setattr(network_module, 'connect', _fake_connect)

        # Wi-Fi radio initial state — not connected, no AP clients.
        wifi_module.radio.connected = False
        wifi_module.radio.stations_ap = 0

        # supervisor.reload() raises so the portal loop exits cleanly.
        def _raise_done():
            raise _PortalDone()

        monkeypatch.setattr(supervisor_module, 'reload', _raise_done)

        # Server shim: poll() is a no-op; reload_pending controls exit timing.
        server_state = {'last_request_t': 0.0, 'reload_pending': reload_pending}

        class _Shim:
            def poll(self):
                pass

        def _make_server_fn(ip, nets, current_values=None, config_errors=None, current_colors=None):
            return _Shim(), server_state

        monkeypatch.setattr(portal_module, '_make_server', _make_server_fn)

        return connect_calls, server_state

    def _run_portal(self, config, recovery, *, timeout=3):
        """Run portal.run() in a daemon thread; return True if it exited in time."""
        import portal as portal_module

        done = threading.Event()

        def _target():
            try:
                portal_module.run(config, recovery=recovery)
            except Exception:
                pass  # portal exits via exception in test context; any exit is fine
            finally:
                done.set()

        threading.Thread(target=_target, daemon=True).start()
        return done.wait(timeout=timeout)

    # Minimal config: SSID + required AP keys so portal starts correctly.
    _WIFI_CONFIG = {
        'CIRCUITPY_WIFI_SSID': 'TestNet',
        'CIRCUITPY_WIFI_PASSWORD': 'testpass',
        'AP_SSID': 'WP',
        'AP_PASSWORD': None,
    }

    def test_recovery_true_calls_network_connect(self, monkeypatch):
        """recovery=True: portal retries Wi-Fi and exits when it reconnects."""
        connect_calls, _ = self._setup_mocks(monkeypatch)
        finished = self._run_portal(self._WIFI_CONFIG, recovery=True)
        assert finished, "portal.run(recovery=True) did not exit within the timeout"
        assert connect_calls, "network.connect() was not called with recovery=True"

    def test_recovery_false_does_not_call_network_connect(self, monkeypatch):
        """recovery=False: portal never retries Wi-Fi even with credentials set."""
        connect_calls, _ = self._setup_mocks(monkeypatch, reload_pending=True)
        finished = self._run_portal(self._WIFI_CONFIG, recovery=False)
        assert finished, "portal.run(recovery=False) did not exit within the timeout"
        assert not connect_calls, "network.connect() was called despite recovery=False"
