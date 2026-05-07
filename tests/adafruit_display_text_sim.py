"""CPython-compatible simulation of adafruit_display_text.

Provides a Label class whose `width` property mirrors the 8px dogica font:
each character occupies 8 pixels, so width = len(text) * 8.  This is the
value that display.py uses for overlay-repositioning logic.
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


class _LabelModule:
    Label = Label


label = _LabelModule()
