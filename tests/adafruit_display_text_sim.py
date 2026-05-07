"""CPython-compatible simulation of adafruit_display_text.

Provides a Label class whose `width` property mirrors the 8px dogica font:
each character occupies 8 pixels, so width = len(text) * 8.  This is the
value that display.py uses for overlay-repositioning logic.

render_pixels() draws the label text into a pixel canvas using PIL's built-in
bitmap font — an approximation of the device's dogica-pixel-8.pcf, which PIL
cannot load standalone.
"""

_CHAR_WIDTH = 8


class Label:
    """Text label with position, color, and font-proportional width."""

    def __init__(self, font, text="", color=0, x=0, y=0,
                 anchor_point=None, anchored_position=None):
        self._font = font
        self._text = text
        self.color = color
        self.x = x
        self.y = y
        self.anchor_point = anchor_point
        self.anchored_position = anchored_position
        self.hidden = False

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

    @property
    def width(self):
        return len(self._text) * _CHAR_WIDTH

    def render_pixels(self, canvas_pixels, offset_x=0, offset_y=0):
        """Draw label text into canvas_pixels (list of rows of RGB tuples).

        Skips rendering if text is empty or the label is hidden.
        Position is derived from anchor_point + anchored_position when set
        (e.g. the right-aligned clock label), otherwise from x/y directly.
        """
        if not self._text or self.hidden:
            return
        from PIL import Image, ImageDraw, ImageFont

        if self.anchored_position is not None and self.anchor_point is not None:
            draw_x = self.anchored_position[0] - int(self.anchor_point[0] * self.width)
            draw_y = self.anchored_position[1]
        else:
            draw_x = self.x
            draw_y = self.y

        draw_x += offset_x
        draw_y += offset_y

        h = len(canvas_pixels)
        w = len(canvas_pixels[0]) if h else 0
        r = (self.color >> 16) & 0xFF
        g = (self.color >> 8) & 0xFF
        b = self.color & 0xFF

        font = ImageFont.load_default(size=8)
        tmp = Image.new("RGB", (w, h))
        ImageDraw.Draw(tmp).text((draw_x, draw_y), self._text, font=font, fill=(r, g, b))

        for py in range(h):
            for px in range(w):
                pix = tmp.getpixel((px, py))
                if pix != (0, 0, 0):
                    canvas_pixels[py][px] = pix


class _LabelModule:
    Label = Label


label = _LabelModule()
