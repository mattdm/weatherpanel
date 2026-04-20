"""Tests for display temperature color mapping logic.

Since Display.__init__ requires hardware (displayio, matrix), we test
_temp_color_index by constructing a minimal mock with just the palette
size attribute it needs.
"""


class FakePalette:
    """Minimal stand-in for displayio.Palette with a length."""
    def __init__(self, n):
        self._n = n
    def __len__(self):
        return self._n


class FakeDisplay:
    """Minimal stand-in for Display with just the palette and method."""
    def __init__(self, palette_size=12):
        self.temperature_palette = FakePalette(palette_size)

    def _temp_color_index(self, temperature, historical=None):
        # Inline copy of Display._temp_color_index to test the logic
        # without importing hardware modules
        center = len(self.temperature_palette) // 2
        buckets = center - 1

        if not historical:
            return center
        if temperature < historical['ave-low']:
            spread = historical['low'] - historical['ave-low']
            if spread == 0:
                return 1
            return center - min(buckets, int((temperature - historical['ave-low']) / (spread / buckets)))
        if temperature > historical['ave-high']:
            spread = historical['high'] - historical['ave-high']
            if spread == 0:
                return len(self.temperature_palette) - 1
            return center + min(buckets, int((temperature - historical['ave-high']) / (spread / buckets)))
        return center


HISTORICAL = {
    'low': 20,
    'ave-low': 35,
    'ave-high': 55,
    'high': 75,
}


class TestTempColorIndex:
    def setup_method(self):
        self.d = FakeDisplay(palette_size=12)
        self.center = 6  # 12 // 2

    def test_no_historical_returns_center(self):
        assert self.d._temp_color_index(50) == self.center

    def test_empty_historical_returns_center(self):
        assert self.d._temp_color_index(50, {}) == self.center

    def test_average_temp_returns_center(self):
        assert self.d._temp_color_index(45, HISTORICAL) == self.center

    def test_at_ave_low_returns_center(self):
        assert self.d._temp_color_index(35, HISTORICAL) == self.center

    def test_at_ave_high_returns_center(self):
        assert self.d._temp_color_index(55, HISTORICAL) == self.center

    def test_below_ave_low_is_below_center(self):
        idx = self.d._temp_color_index(25, HISTORICAL)
        assert idx < self.center

    def test_above_ave_high_is_above_center(self):
        idx = self.d._temp_color_index(65, HISTORICAL)
        assert idx > self.center

    def test_at_historical_low_extreme(self):
        idx = self.d._temp_color_index(20, HISTORICAL)
        assert idx <= 1  # Should be near the cold extreme

    def test_at_historical_high_extreme(self):
        idx = self.d._temp_color_index(75, HISTORICAL)
        assert idx >= len(self.d.temperature_palette) - 2

    def test_colder_is_lower_index(self):
        idx_cool = self.d._temp_color_index(30, HISTORICAL)
        idx_cold = self.d._temp_color_index(22, HISTORICAL)
        assert idx_cold < idx_cool

    def test_warmer_is_higher_index(self):
        idx_warm = self.d._temp_color_index(60, HISTORICAL)
        idx_hot = self.d._temp_color_index(72, HISTORICAL)
        assert idx_hot > idx_warm

    def test_zero_spread_cold(self):
        hist = {'low': 35, 'ave-low': 35, 'ave-high': 55, 'high': 75}
        idx = self.d._temp_color_index(30, hist)
        assert idx == 1

    def test_zero_spread_hot(self):
        hist = {'low': 20, 'ave-low': 35, 'ave-high': 55, 'high': 55}
        idx = self.d._temp_color_index(60, hist)
        assert idx == len(self.d.temperature_palette) - 1

    def test_index_never_below_1(self):
        idx = self.d._temp_color_index(-20, HISTORICAL)
        assert idx >= 1

    def test_index_never_above_palette_max(self):
        idx = self.d._temp_color_index(120, HISTORICAL)
        assert idx < len(self.d.temperature_palette)
