"""CPython-compatible simulation of CircuitPython's displayio module.

Provides real implementations of Bitmap, Palette, Group, and TileGrid so
that display rendering code can run and be tested on CPython without hardware.
Only the subset of the API used by this project is implemented.
"""


def release_displays():
    """No-op: on hardware this releases display resources; not needed in sim."""


class Bitmap:
    """2D array of palette color indices, matching the displayio.Bitmap API."""

    def __init__(self, width, height, num_colors):
        self.width = width
        self.height = height
        self.num_colors = num_colors
        self._data = bytearray(width * height)

    def __getitem__(self, key):
        if isinstance(key, tuple):
            x, y = key
            return self._data[y * self.width + x]
        return self._data[key]

    def __setitem__(self, key, value):
        if isinstance(key, tuple):
            x, y = key
            idx = y * self.width + x
        else:
            idx = key
        if value < 0 or value >= self.num_colors:
            raise ValueError(f"Color index {value} out of range [0, {self.num_colors})")
        self._data[idx] = value


class Palette:
    """Ordered list of 24-bit RGB colors with optional per-index transparency."""

    def __init__(self, n):
        self._colors = [0] * n
        self._transparent = set()

    def __len__(self):
        return len(self._colors)

    def __getitem__(self, i):
        return self._colors[i]

    def __setitem__(self, i, color):
        self._colors[i] = color

    def make_transparent(self, idx):
        self._transparent.add(idx)

    def make_opaque(self, idx):
        self._transparent.discard(idx)

    def is_transparent(self, idx):
        return idx in self._transparent


class Group:
    """Ordered collection of displayio objects with position and visibility."""

    def __init__(self, x=0, y=0, scale=1):
        self.x = x
        self.y = y
        self._scale = scale
        self.hidden = False
        self._children = []

    @property
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        self._scale = value

    def append(self, child):
        self._children.append(child)

    def pop(self, index=-1):
        return self._children.pop(index)

    def __len__(self):
        return len(self._children)

    def __iter__(self):
        return iter(self._children)

    def __getitem__(self, i):
        return self._children[i]

    def __setitem__(self, i, value):
        self._children[i] = value


class TileGrid:
    """Renders a Bitmap through a Palette at a given position."""

    def __init__(self, bitmap, pixel_shader, tile_width=None, tile_height=None,
                 x=0, y=0, default_tile=0, **kwargs):
        self.bitmap = bitmap
        self.pixel_shader = pixel_shader
        self.tile_width = tile_width if tile_width is not None else bitmap.width
        self.tile_height = tile_height if tile_height is not None else bitmap.height
        self.x = x
        self.y = y
        self.default_tile = default_tile
        self.transpose_xy = False
        self.flip_x = False
        self.flip_y = False
