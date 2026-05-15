"""Tests for BaseDisplay — the shared display base class.

Verifies that both Display and PortalDisplay are proper subclasses and that
the vertical-centering formula produces the correct label positions.

The key structural test: _vcenter_y(4) must yield start_y=4, which matches
the fixed y-positions that Display's boot/scale slots use.
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


