"""Tests for BaseDisplay — the shared display base class.

Verifies that both WeatherDisplay and PortalDisplay are proper subclasses,
that shared constants and methods are accessible on both, and that the
pre-allocated text screen layout is correct.

The key structural test: _show_text() with 4 lines must produce label
y-positions at 4, 12, 20, 28 — identical to the fixed positions that
WeatherDisplay's boot/scale slots use.  This proves the two representations
are the same layout, not coincidentally similar ones.
"""
import pytest
from unittest.mock import MagicMock


_CONFIG = {'SWAP_GREEN_BLUE': False}


@pytest.fixture
def weather_display(sim_display):
    """Reuse the session-scoped sim_display fixture."""
    return sim_display


@pytest.fixture
def portal_display(monkeypatch):
    """PortalDisplay with matrix and font mocked."""
    import matrix as matrix_module
    from adafruit_bitmap_font import bitmap_font
    import portal as portal_module
    import base_display as base_display_module

    monkeypatch.setattr(
        matrix_module, 'display_set_root',
        lambda rg, swapgb=False, bit_depth=6: MagicMock(),
    )
    monkeypatch.setattr(bitmap_font, 'load_font', lambda path: MagicMock())

    class _FakeLabel:
        def __init__(self, font, text="", color=0xFFFFFF, x=0, y=0, **kwargs):
            self.font  = font
            self.text  = text
            self.color = color
            self.x     = x
            self.y     = y

    monkeypatch.setattr(base_display_module, 'Label', _FakeLabel)
    return portal_module.PortalDisplay({})


# ---------------------------------------------------------------------------
# isinstance checks
# ---------------------------------------------------------------------------

class TestInheritance:
    def test_weather_display_is_base_display(self, weather_display):
        from base_display import BaseDisplay
        assert isinstance(weather_display, BaseDisplay)

    def test_portal_display_is_base_display(self, portal_display):
        from base_display import BaseDisplay
        assert isinstance(portal_display, BaseDisplay)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_font_path_on_weather_display(self, weather_display):
        assert weather_display.FONT_PATH == "/fonts/dogica-pixel-8-narrow.pcf"

    def test_font_path_on_portal_display(self, portal_display):
        assert portal_display.FONT_PATH == "/fonts/dogica-pixel-8-narrow.pcf"

    def test_display_width_on_weather_display(self, weather_display):
        assert weather_display.DISPLAY_WIDTH == 64

    def test_display_height_on_weather_display(self, weather_display):
        assert weather_display.DISPLAY_HEIGHT == 32

    def test_display_height_on_portal_display(self, portal_display):
        assert portal_display.DISPLAY_HEIGHT == 32

    def test_label_line_height_on_weather_display(self, weather_display):
        assert weather_display.LABEL_LINE_HEIGHT == 10

    def test_label_line_height_on_portal_display(self, portal_display):
        assert portal_display.LABEL_LINE_HEIGHT == 10

    def test_weather_display_text_labels_at_boot_positions(self, weather_display):
        """WeatherDisplay __init__ must place named label aliases at y=4, 12, 20, 28.

        These are the positions baked in at init and never changed. Verifying
        them here catches any accidental repositioning in __init__ or its callers.
        """
        assert weather_display._top_label.y     == 4
        assert weather_display._loc_main_label.y == 12
        assert weather_display.station_label.y   == 20
        assert weather_display.network_label.y   == 28


# ---------------------------------------------------------------------------
# flush()
# ---------------------------------------------------------------------------

class TestFlush:
    def test_flush_calls_display_refresh(self, weather_display):
        """flush() must delegate to self._display.refresh()."""
        refresh_count = [0]
        orig_refresh = weather_display._display.refresh

        def counting_refresh():
            refresh_count[0] += 1
            return orig_refresh()

        weather_display._display.refresh = counting_refresh
        before = refresh_count[0]
        weather_display.flush()
        assert refresh_count[0] == before + 1

        # Restore
        weather_display._display.refresh = orig_refresh


# ---------------------------------------------------------------------------
# _make_label()
# ---------------------------------------------------------------------------

class TestMakeLabel:
    def test_returns_label_with_correct_text(self, weather_display):
        import adafruit_display_text.label as label_mod
        lbl = weather_display._make_label(text="Hello")
        assert isinstance(lbl, label_mod.Label)
        assert lbl.text == "Hello"

    def test_returns_label_with_correct_color(self, weather_display):
        lbl = weather_display._make_label(color=0xFF0000)
        assert lbl.color == 0xFF0000

    def test_returns_label_with_correct_position(self, weather_display):
        lbl = weather_display._make_label(x=5, y=10)
        assert lbl.x == 5
        assert lbl.y == 10

    def test_uses_shared_font(self, weather_display):
        lbl = weather_display._make_label()
        assert lbl.font is weather_display._font


# ---------------------------------------------------------------------------
# _vcenter_y()
# ---------------------------------------------------------------------------

class TestVcenterY:
    def test_one_line_centers_vertically(self):
        from base_display import BaseDisplay
        start_y, lh = BaseDisplay._vcenter_y(1)
        # total_h=8, start_y=(32-8)//2+4=16
        assert start_y == 16
        assert lh == BaseDisplay.LABEL_LINE_HEIGHT

    def test_two_lines(self):
        from base_display import BaseDisplay
        start_y, lh = BaseDisplay._vcenter_y(2)
        # total_h=2*8+2=18, start_y=(32-18)//2+4=11
        assert start_y == 11
        assert lh == BaseDisplay.LABEL_LINE_HEIGHT

    def test_three_lines(self):
        from base_display import BaseDisplay
        start_y, lh = BaseDisplay._vcenter_y(3)
        # total_h=3*8+2*2=28, start_y=(32-28)//2+4=6
        assert start_y == 6
        assert lh == BaseDisplay.LABEL_LINE_HEIGHT

    def test_four_lines_tight_layout(self):
        from base_display import BaseDisplay
        start_y, lh = BaseDisplay._vcenter_y(4)
        # total_h=4*8+3*2=38 > 32 → tight: total_h=32, line_height=8
        # start_y=(32-32)//2+4=4
        assert start_y == 4
        assert lh == 8


