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
        from PIL import Image

        pixels = self.render_to_pixels()
        img = Image.new("RGB", (64, 32))
        img.putdata([(r, g, b) for row in pixels for r, g, b in row])
        return img.resize((64 * scale, 32 * scale), Image.NEAREST)

    def render_to_realistic_image(self, cell_size=20, dot_fraction=0.8):
        """Return a PIL Image that mimics the physical appearance of an LED matrix.

        Each LED pixel is rendered as a smooth antialiased filled circle on a
        black background using the Versa Design integer distance algorithm —
        no numpy, no ImageDraw. A soft glow is composited on top via PIL blur.
        Default cell_size=20 gives 1280×640 output.

        Reference: "A novel technique to draw antialiased circles without floating
        point math nor square root" — Juan Ramón Vadillo, Versa Design S.L., 2023
        https://github.com/Versa-Design/Antialiased_Circle
        """
        from PIL import Image, ImageChops, ImageFilter

        src = self.render_to_pixels()
        cell = cell_size
        w, h = 64 * cell, 32 * cell
        data = bytearray(w * h * 3)

        r = cell * dot_fraction / 2.0
        half = cell / 2.0
        r2 = r * r
        rmin = r2 - r          # inner squared radius (edge starts here)
        rmax = r2 + r          # outer squared radius (edge ends here)
        denom = 2.0 * r        # linear interpolation denominator

        for row_idx, row in enumerate(src):
            for col_idx, (red, green, blue) in enumerate(row):
                if red == green == blue == 0:
                    continue
                y0 = row_idx * cell
                x0 = col_idx * cell
                for dy in range(cell):
                    for dx in range(cell):
                        d2 = (dx + 0.5 - half) ** 2 + (dy + 0.5 - half) ** 2
                        if d2 < rmin:
                            alpha = 1.0
                        elif d2 < rmax:
                            alpha = (rmax - d2) / denom
                        else:
                            continue
                        i = ((y0 + dy) * w + (x0 + dx)) * 3
                        data[i]     = int(red   * alpha)
                        data[i + 1] = int(green * alpha)
                        data[i + 2] = int(blue  * alpha)

        img = Image.frombytes("RGB", (w, h), bytes(data))

        # Soft glow: blur a half-intensity copy, then take pixel-wise max so
        # the sharp dot edges always win and glow fills the surrounding gap.
        glow = img.filter(ImageFilter.GaussianBlur(radius=cell * 0.3))
        glow = glow.point(lambda x: x >> 1)
        return ImageChops.lighter(glow, img)


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


def display_set_root(root_group, swapgb=False, bit_depth=6):
    """Simulate matrix hardware init — return a SimDisplay for the root group."""
    return SimDisplay(root_group)
