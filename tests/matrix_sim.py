"""CPython-compatible simulation of the matrix hardware module.

Provides display_set_root() and SimDisplay so that display.py can initialize
without any real RGB matrix hardware.  SimDisplay.render_to_pixels() composes
the full displayio Group tree into a flat 64×32 RGB pixel array for testing.
"""

from displayio_sim import Group, Palette, TileGrid


class SimDisplay:
    """Simulated FramebufferDisplay — tracks the root group and supports refresh."""

    def __init__(self, root_group):
        self.root_group = root_group

    def refresh(self):
        """No-op: on hardware this flushes the framebuffer to the LED matrix."""

    def render_to_pixels(self):
        """Compose the full Group tree into a 64×32 array of (R, G, B) tuples.

        Returns a list of 32 rows, each a list of 64 (r, g, b) tuples.
        Transparent palette indices leave the underlying pixel unchanged.
        Groups and TileGrids with hidden=True are skipped.
        """
        width = 64
        height = 32
        pixels = [[(0, 0, 0)] * width for _ in range(height)]
        _render_group(self.root_group, pixels, offset_x=0, offset_y=0)
        return pixels

    def render_to_image(self, scale=8):
        """Return a PIL Image of the composited display, scaled up for visibility.

        Each logical pixel is rendered as a scale×scale block of the same color,
        so a 64×32 display becomes a 512×256 image at the default scale of 8.
        Uses nearest-neighbor scaling so pixel boundaries stay crisp.
        """
        import numpy as np
        from PIL import Image

        pixels = self.render_to_pixels()
        arr = np.array(pixels, dtype=np.uint8)   # shape (32, 64, 3)
        img = Image.fromarray(arr)
        return img.resize((64 * scale, 32 * scale), Image.NEAREST)


def _render_group(group, pixels, offset_x, offset_y):
    if getattr(group, 'hidden', False):
        return
    gx = offset_x + getattr(group, 'x', 0)
    gy = offset_y + getattr(group, 'y', 0)
    for child in group:
        if isinstance(child, Group):
            _render_group(child, pixels, gx, gy)
        elif isinstance(child, TileGrid):
            _render_tilegrid(child, pixels, gx, gy)


def _render_tilegrid(grid, pixels, offset_x, offset_y):
    if getattr(grid, 'hidden', False):
        return
    bmp = grid.bitmap
    palette = grid.pixel_shader
    ox = offset_x + grid.x
    oy = offset_y + grid.y
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    for y in range(bmp.height):
        for x in range(bmp.width):
            idx = bmp[x, y]
            if isinstance(palette, Palette) and palette.is_transparent(idx):
                continue
            px = ox + x
            py = oy + y
            if 0 <= px < width and 0 <= py < height:
                if isinstance(palette, Palette):
                    color = palette[idx]
                else:
                    color = int(palette[idx]) if palette[idx] else 0
                r = (color >> 16) & 0xFF
                g = (color >> 8) & 0xFF
                b = color & 0xFF
                pixels[py][px] = (r, g, b)


def display_set_root(root_group, _rotation=None, swapgb=False, bit_depth=6):
    """Simulate matrix hardware init — return a SimDisplay for the root group."""
    return SimDisplay(root_group)
