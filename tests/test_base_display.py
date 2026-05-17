"""Tests for BaseDisplay — the shared display base class.

Verifies the vertical-centering formula produces the correct label positions.

The key structural test: _vcenter_y(4) must yield start_y=4, which matches
the fixed y-positions that Display's boot/scale slots use.
"""


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
