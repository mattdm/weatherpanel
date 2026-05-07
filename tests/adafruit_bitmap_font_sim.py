"""CPython-compatible simulation of adafruit_bitmap_font.

Returns a minimal SimFont object so that display code can call
bitmap_font.load_font() without touching the filesystem or hardware fonts.
"""


class SimFont:
    """Stub font — enough for display code that only uses width calculations."""

    def get_bounding_box(self):
        return (8, 8, 0, 0)


class _BitmapFontModule:
    def load_font(self, path):
        return SimFont()


bitmap_font = _BitmapFontModule()
