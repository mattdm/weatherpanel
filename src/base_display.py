"""Shared base class for all display adapters.

Owns hardware initialization — font loading, root group creation, display
setup — and the 4-line text screen used by both WeatherDisplay (for boot
and scale states) and PortalDisplay (for all non-QR screens).

The text-screen layout is the same in both cases: the centering formula for
4 lines on a 32px display produces y=4, 12, 20, 28 — identical to the fixed
positions WeatherDisplay's boot/scale screens use.
"""
import displayio
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label

import matrix


class BaseDisplay:
    """Base class for LED-matrix display adapters.

    Provides hardware setup, shared display geometry, a font-aware label
    factory, and the 4-line centered text screen shared by all subclasses.

    Subclasses are responsible for appending ``_text_group`` to
    ``root_group`` at the correct z-position and for building any groups
    unique to their use case.
    """

    FONT_PATH         = "/fonts/dogica-pixel-8-narrow.pcf"
    DISPLAY_WIDTH     = 64
    DISPLAY_HEIGHT    = 32
    LABEL_LINE_HEIGHT = 10    # 8 px font + 2 px gap
    _MAX_TEXT_LINES   = 4

    def __init__(self, config, *, bit_depth=None):
        """Initialize font, root group, display, and the pre-allocated text screen."""
        self.screen = None
        self._font = bitmap_font.load_font(self.FONT_PATH)
        self.root_group = displayio.Group()
        kwargs = {'swapgb': config.get('SWAP_GREEN_BLUE', False)}
        if bit_depth is not None:
            kwargs['bit_depth'] = bit_depth
        self._display = matrix.display_set_root(self.root_group, **kwargs)

        # Pre-allocate text labels, pre-positioned at the 4-line tight layout.
        # _vcenter_y(4) yields start_y=4, line_height=8, so labels land at
        # y=4, 12, 20, 28 — the same fixed positions WeatherDisplay uses for
        # its boot/scale slots.  PortalDisplay repositions them per call via
        # _show_text() when fewer than 4 lines are shown.
        start_y, line_height = self._vcenter_y(self._MAX_TEXT_LINES)
        self._text_labels = [
            self._make_label(y=start_y + i * line_height)
            for i in range(self._MAX_TEXT_LINES)
        ]
        self._text_group = displayio.Group()
        for lbl in self._text_labels:
            self._text_group.append(lbl)
        # _text_group is NOT appended to root_group here.
        # Subclasses append it at the z-position correct for their layer order.

    def flush(self):
        """Push the current display state to the hardware."""
        self._display.refresh()

    def _make_label(self, text="", color=0xFFFFFF, x=0, y=0, **kwargs):
        """Return a Label with the shared font pre-baked in."""
        return Label(self._font, text=text, color=color, x=x, y=y, **kwargs)

    @classmethod
    def _vcenter_y(cls, n_lines, gap=2):
        """Return ``(start_y, line_height)`` to center ``n_lines`` of 8 px text.

        When the block is taller than the display, the inter-line gap is
        dropped so all lines fit without clipping.
        """
        total_h = n_lines * 8 + (n_lines - 1) * gap
        line_height = cls.LABEL_LINE_HEIGHT
        if total_h > cls.DISPLAY_HEIGHT:
            total_h = n_lines * 8
            line_height = 8
        start_y = (cls.DISPLAY_HEIGHT - total_h) // 2 + 4
        return start_y, line_height

    def _show_text(self, lines, color=0xFFFFFF, colors=None):
        """Assign text and color to the 4 fixed label slots and show the text group.

        ``lines`` must have exactly ``_MAX_TEXT_LINES`` elements. Pass ``""``
        for blank slots. Labels never move — y-positions are fixed at init.
        ``color`` applies to all non-blank lines; ``colors`` is an optional
        per-slot override list aligned to the same indices as ``lines``.
        """
        for i, label in enumerate(self._text_labels):
            label.text  = lines[i]
            label.color = colors[i] if colors and i < len(colors) and lines[i] else color
        self._text_group.hidden = False
