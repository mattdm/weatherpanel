"""Tests for display temperature color mapping logic and palette generation."""
from station import Hour
from display import _temp_color_index, _gen_temp_palette, _clamp_temp_scale, MIN_TEMP_SPREAD
from appconfig import COLOR_DEFAULTS

STALE_COLOR = COLOR_DEFAULTS['STATUS_STALE_COLOR']

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

    def test_index_never_zero_hot(self):
        """Index 0 is transparent; verify it is never returned for any hot temperature."""
        idx = _temp_color_index(PALETTE_LEN, 200, HISTORICAL)
        assert idx != 0

    def test_pathological_low_gt_ave_low(self):
        """Historical data where low > ave-low (bad API response) should still clamp safely."""
        bad_hist = {'low': 50, 'ave-low': 35, 'ave-high': 55, 'high': 75}
        idx = _temp_color_index(PALETTE_LEN, 20, bad_hist)
        assert 1 <= idx <= PALETTE_LEN - 1

    def test_pathological_high_lt_ave_high(self):
        """Historical data where high < ave-high (bad API response) should still clamp safely."""
        bad_hist = {'low': 20, 'ave-low': 35, 'ave-high': 55, 'high': 40}
        idx = _temp_color_index(PALETTE_LEN, 80, bad_hist)
        assert 1 <= idx <= PALETTE_LEN - 1


# ---------------------------------------------------------------------------
# mark_temp_stale
# ---------------------------------------------------------------------------

class TestGenTempPalette:
    """Tests for _gen_temp_palette() HSL gradient generator."""

    COLD   = COLOR_DEFAULTS['TEMP_COLOR_COLD']
    CENTER = COLOR_DEFAULTS['TEMP_COLOR_CENTER']
    WARM   = COLOR_DEFAULTS['TEMP_COLOR_WARM']
    STEPS  = 5

    def _palette(self):
        return _gen_temp_palette(self.COLD, self.CENTER, self.WARM, self.STEPS)

    def test_endpoint_colors_roundtrip(self):
        """Extreme cold (index 1) and extreme warm (index 11) must survive the HSL round-trip."""
        palette = self._palette()
        assert palette[1] == self.COLD
        assert palette[-1] == self.WARM

    def test_cold_side_blue_dominant(self):
        """HSL hue preserved — cold steps must have blue > red."""
        palette = self._palette()
        for entry in palette[1:self.STEPS + 1]:
            assert (entry & 0xFF) > (entry >> 16 & 0xFF), f"cold step {entry:#08x} not blue-dominant"

    def test_warm_side_red_dominant(self):
        """HSL hue preserved — warm steps must have red > blue."""
        palette = self._palette()
        for entry in palette[self.STEPS + 2:]:
            assert (entry >> 16 & 0xFF) > (entry & 0xFF), f"warm step {entry:#08x} not red-dominant"


class TestClampTempScale:
    """_clamp_temp_scale() must ensure temp_max - temp_min >= MIN_TEMP_SPREAD."""

    def test_wide_spread_unchanged(self):
        """A spread already above the minimum is returned as-is."""
        lo, hi = _clamp_temp_scale(-5, 105)
        assert lo == -5
        assert hi == 105

    def test_exactly_min_spread_unchanged(self):
        """Spread == MIN_TEMP_SPREAD is already acceptable — no expansion."""
        lo, hi = _clamp_temp_scale(0, MIN_TEMP_SPREAD)
        assert lo == 0
        assert hi == MIN_TEMP_SPREAD

    def test_one_below_min_expands(self):
        """Spread == MIN_TEMP_SPREAD - 1 must be expanded to MIN_TEMP_SPREAD."""
        lo, hi = _clamp_temp_scale(0, MIN_TEMP_SPREAD - 1)
        assert hi - lo == MIN_TEMP_SPREAD

    def test_equal_min_max_expands_to_min_spread(self):
        """Equal min and max (zero spread) must expand to MIN_TEMP_SPREAD."""
        lo, hi = _clamp_temp_scale(70, 70)
        assert hi - lo == MIN_TEMP_SPREAD


class TestMarkTempStale:
    """mark_temp_stale() sets current_temp_label to STALE_COLOR and flushes."""

    def test_sets_current_temp_label_to_stale_color(self, sim_display):
        """mark_temp_stale() must paint current_temp_label exactly STALE_COLOR."""
        sim_display.mark_temp_stale()
        assert sim_display.current_temp_label.color == STALE_COLOR


def _make_hour(start, end, temperature):
    """Build a minimal Hour suitable for update_forecast."""
    h = Hour()
    h.start = start
    h.end = end
    h.temperature = temperature
    h.precipitation = 0
    h.snow_fraction = 0.0
    h.qpf_mm = 0.0
    return h


class TestUpdateForecastHourSlot:
    """update_forecast() must use each hour's own historical slot for the badge.

    Regression test for the stale-hour_slot bug: the pre-pass used to leave
    hour_slot pointing to the last iterated hour's date.  At x == 0, the badge
    suffix was therefore computed against the wrong day's record when the first
    hour falls on a different calendar date than later hours.
    """

    def test_record_badge_uses_first_hour_slot(self, sim_display):
        """Badge uses today's slot (hour 0's date), not a later date's slot.

        Setup: 65 hours where hour 0 is on DATE_A and hours 1-64 are on DATE_B.
        DATE_A slot has record high = 70°F; DATE_B slot has record high = 999°F
        (so high that 70°F would never register as a record against it).
        Hour 0 temperature = 70°F — exactly DATE_A's record high → badge "!".
        Without the fix, hour_slot at x==0 would point to DATE_B's slot (last
        pre-pass iteration) and the suffix would be empty.
        """
        DATE_A = "2024-07-04"
        DATE_B = "2024-07-05"

        slot_a = {'date': DATE_A, 'low': 50, 'ave-low': 58, 'ave-high': 65, 'high': 70}
        slot_b = {'date': DATE_B, 'low': 50, 'ave-low': 58, 'ave-high': 65, 'high': 999}
        historical = [slot_a, slot_b, None, None]

        hourly = {}
        h0 = _make_hour(f"{DATE_A}T12:00:00-05:00", f"{DATE_A}T13:00:00-05:00", 70)
        hourly["h0"] = h0
        for i in range(1, 65):
            h = _make_hour(f"{DATE_B}T{i:02d}:00:00-05:00", f"{DATE_B}T{i+1:02d}:00:00-05:00", 60)
            hourly[f"h{i}"] = h

        current_time = h0.start
        sim_display.update_forecast(hourly, historical, current_time)

        label_text = sim_display.current_temp_label.text
        assert label_text == "70°!", (
            f"Expected '70°!' but got {label_text!r} — "
            "hour_slot for x==0 may be using the wrong date's historical slot"
        )
