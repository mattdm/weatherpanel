"""Tests for display temperature color mapping logic."""
from display import _temp_color_index

PALETTE_LEN = 12
CENTER = PALETTE_LEN // 2

HISTORICAL = {
    'low': 20,
    'ave-low': 35,
    'ave-high': 55,
    'high': 75,
}


class TestTempColorIndex:
    def test_no_historical_returns_center(self):
        assert _temp_color_index(PALETTE_LEN, 50) == CENTER

    def test_empty_historical_returns_center(self):
        assert _temp_color_index(PALETTE_LEN, 50, {}) == CENTER

    def test_average_temp_returns_center(self):
        assert _temp_color_index(PALETTE_LEN, 45, HISTORICAL) == CENTER

    def test_at_ave_low_returns_center(self):
        assert _temp_color_index(PALETTE_LEN, 35, HISTORICAL) == CENTER

    def test_at_ave_high_returns_center(self):
        assert _temp_color_index(PALETTE_LEN, 55, HISTORICAL) == CENTER

    def test_below_ave_low_is_below_center(self):
        idx = _temp_color_index(PALETTE_LEN, 25, HISTORICAL)
        assert idx < CENTER

    def test_above_ave_high_is_above_center(self):
        idx = _temp_color_index(PALETTE_LEN, 65, HISTORICAL)
        assert idx > CENTER

    def test_at_historical_low_extreme(self):
        idx = _temp_color_index(PALETTE_LEN, 20, HISTORICAL)
        assert idx <= 1

    def test_at_historical_high_extreme(self):
        idx = _temp_color_index(PALETTE_LEN, 75, HISTORICAL)
        assert idx >= PALETTE_LEN - 2

    def test_colder_is_lower_index(self):
        idx_cool = _temp_color_index(PALETTE_LEN, 30, HISTORICAL)
        idx_cold = _temp_color_index(PALETTE_LEN, 22, HISTORICAL)
        assert idx_cold < idx_cool

    def test_warmer_is_higher_index(self):
        idx_warm = _temp_color_index(PALETTE_LEN, 60, HISTORICAL)
        idx_hot = _temp_color_index(PALETTE_LEN, 72, HISTORICAL)
        assert idx_hot > idx_warm

    def test_zero_spread_cold(self):
        hist = {'low': 35, 'ave-low': 35, 'ave-high': 55, 'high': 75}
        idx = _temp_color_index(PALETTE_LEN, 30, hist)
        assert idx == 1

    def test_zero_spread_hot(self):
        hist = {'low': 20, 'ave-low': 35, 'ave-high': 55, 'high': 55}
        idx = _temp_color_index(PALETTE_LEN, 60, hist)
        assert idx == PALETTE_LEN - 1

    def test_index_never_below_1(self):
        idx = _temp_color_index(PALETTE_LEN, -20, HISTORICAL)
        assert idx >= 1

    def test_index_never_above_palette_max(self):
        idx = _temp_color_index(PALETTE_LEN, 120, HISTORICAL)
        assert idx < PALETTE_LEN
