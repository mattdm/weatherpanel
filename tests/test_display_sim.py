"""Tests for Display rendering using the CPython displayio/font/text simulations.

The sim layer replaces MagicMock stubs for displayio, adafruit_bitmap_font,
adafruit_display_text, and the matrix hardware module — so the real Display
class runs on CPython and we can inspect the actual pixel data that would
appear on the LED matrix.
"""
import pytest

# ---------------------------------------------------------------------------
# Constants matching Display defaults
# ---------------------------------------------------------------------------

_TEMP_MIN = -5
_TEMP_MAX = 105
_HEIGHT = 32
_WIDTH = 64
_SCALE_FACTOR = (_TEMP_MAX - _TEMP_MIN) / _HEIGHT
_MIDPOINT_TEMP = (_TEMP_MAX + _TEMP_MIN) / 2
_PALETTE_CENTER = 6       # neutral gray in the 12-color temperature palette
_RAIN_INDEX = 1
_SNOW_INDEX = 2

_CONFIG = {
    'TEMP_MIN': _TEMP_MIN,
    'TEMP_MAX': _TEMP_MAX,
    'SWAP_GREEN_BLUE': False,
}

_CURRENT_TIME = "2026-05-07T09:00:00"
_NO_HISTORICAL = [None, None, None, None]
_HISTORICAL = [
    {'date': '2026-05-07', 'low': 20, 'ave-low': 35, 'ave-high': 55, 'high': 75},
]


def _temp_y(temp):
    """Expected bitmap row for a given temperature with default scale config."""
    return max(0, min(_HEIGHT - 1, round(_HEIGHT // 2 + (_MIDPOINT_TEMP - temp) / _SCALE_FACTOR)))


def make_hour(temperature, precipitation=0, snow_fraction=0.0,
              start="2026-05-07T10:00:00", end="2026-05-07T11:00:00"):
    """Build a minimal Hour with the given weather values."""
    from station import Hour
    h = Hour()
    h.start = start
    h.end = end
    h.temperature = temperature
    h.precipitation = precipitation
    h.snow_fraction = snow_fraction
    h.forecast = "Sim"
    return h


def _run(d, hours, historical=None):
    """Convenience wrapper around update_hourly_forecast."""
    if historical is None:
        historical = _NO_HISTORICAL
    return d.update_hourly_forecast(hours, historical, _CURRENT_TIME)


# ---------------------------------------------------------------------------
# Temperature pixel placement
# ---------------------------------------------------------------------------

class TestTemperaturePixelPlacement:
    def test_midpoint_temp_lands_at_center_row(self, sim_display):
        _run(sim_display, [make_hour(50)])
        assert sim_display.temperature_forecast_bitmap[0, 16] != 0

    def test_midpoint_temp_only_lights_one_row(self, sim_display):
        _run(sim_display, [make_hour(50)])
        bmp = sim_display.temperature_forecast_bitmap
        for y in range(_HEIGHT):
            if y != 16:
                assert bmp[0, y] == 0, f"Unexpected pixel at column 0, row {y}"

    def test_warm_temp_is_above_center(self, sim_display):
        _run(sim_display, [make_hour(70)])
        y = _temp_y(70)
        assert y < 16
        assert sim_display.temperature_forecast_bitmap[0, y] != 0

    def test_cold_temp_is_below_center(self, sim_display):
        _run(sim_display, [make_hour(30)])
        y = _temp_y(30)
        assert y > 16
        assert sim_display.temperature_forecast_bitmap[0, y] != 0

    def test_hotter_is_higher_on_screen(self, sim_display):
        """Warm and cold hours in adjacent columns — hot row is lower index than cold row."""
        _run(sim_display, [make_hour(70), make_hour(30)])
        bmp = sim_display.temperature_forecast_bitmap
        # Column 0 (70°F): find topmost lit row
        hot_row = next(y for y in range(_HEIGHT) if bmp[0, y] != 0)
        # Column 1 (30°F): find topmost lit row (Bresenham may add intermediate pixels,
        # but the topmost in column 1 is still above the cold dot at y=22)
        cold_row_first = next(y for y in range(_HEIGHT) if bmp[1, y] != 0)
        assert hot_row < cold_row_first

    def test_extreme_cold_clamped_to_bottom(self, sim_display):
        _run(sim_display, [make_hour(-200)])
        assert sim_display.temperature_forecast_bitmap[0, _HEIGHT - 1] != 0

    def test_extreme_hot_clamped_to_top(self, sim_display):
        _run(sim_display, [make_hour(200)])
        assert sim_display.temperature_forecast_bitmap[0, 0] != 0

    def test_unused_columns_are_cleared(self, sim_display):
        """Columns beyond the number of hours plotted must be zeroed."""
        _run(sim_display, [make_hour(50)])  # only 1 hour → 63 unused columns
        bmp = sim_display.temperature_forecast_bitmap
        for col in range(1, _WIDTH):
            for y in range(_HEIGHT):
                assert bmp[col, y] == 0, f"Expected blank at column {col}, row {y}"


# ---------------------------------------------------------------------------
# Temperature color from historical data
# ---------------------------------------------------------------------------

class TestTemperatureColor:
    def test_average_temp_gets_neutral_palette_index(self, sim_display):
        hours = [make_hour(45)]  # between ave-low=35 and ave-high=55
        _run(sim_display, hours, historical=_HISTORICAL)
        idx = sim_display.temperature_forecast_bitmap[0, _temp_y(45)]
        assert idx == _PALETTE_CENTER

    def test_above_ave_high_gets_warm_index(self, sim_display):
        hours = [make_hour(60)]  # above ave-high=55
        _run(sim_display, hours, historical=_HISTORICAL)
        idx = sim_display.temperature_forecast_bitmap[0, _temp_y(60)]
        assert idx > _PALETTE_CENTER

    def test_below_ave_low_gets_cold_index(self, sim_display):
        hours = [make_hour(25)]  # below ave-low=35
        _run(sim_display, hours, historical=_HISTORICAL)
        idx = sim_display.temperature_forecast_bitmap[0, _temp_y(25)]
        assert idx < _PALETTE_CENTER

    def test_no_historical_gives_neutral_index(self, sim_display):
        """Without historical data, any temperature maps to the center (neutral) index."""
        hours = [make_hour(80)]  # would be warm if history were present
        _run(sim_display, hours, historical=_NO_HISTORICAL)
        idx = sim_display.temperature_forecast_bitmap[0, _temp_y(80)]
        assert idx == _PALETTE_CENTER


# ---------------------------------------------------------------------------
# Precipitation bars
# ---------------------------------------------------------------------------

class TestPrecipitationBars:
    def test_zero_precip_column_is_transparent(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=0)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(_HEIGHT):
            assert bmp[0, y] == 0

    def test_full_rain_fills_entire_column(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=100, snow_fraction=0.0)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(_HEIGHT):
            assert bmp[0, y] == _RAIN_INDEX

    def test_half_rain_fills_bottom_half(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=50, snow_fraction=0.0)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(16):
            assert bmp[0, y] == 0, f"Row {y} should be transparent"
        for y in range(16, _HEIGHT):
            assert bmp[0, y] == _RAIN_INDEX, f"Row {y} should be rain"

    def test_full_snow_fills_entire_column(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=100, snow_fraction=1.0)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(_HEIGHT):
            assert bmp[0, y] == _SNOW_INDEX

    def test_half_precip_all_snow(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=50, snow_fraction=1.0)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(16):
            assert bmp[0, y] == 0
        for y in range(16, _HEIGHT):
            assert bmp[0, y] == _SNOW_INDEX

    def test_mixed_rain_and_snow(self, sim_display):
        """Full precipitation, 50% snow: first half rain, second half snow."""
        _run(sim_display, [make_hour(50, precipitation=100, snow_fraction=0.5)])
        bmp = sim_display.precipitation_forecast_bitmap
        # rain_row_count = round(0.5 * 32) = 16 → rows 0-15 rain, 16-31 snow
        for y in range(16):
            assert bmp[0, y] == _RAIN_INDEX, f"Row {y} should be rain"
        for y in range(16, _HEIGHT):
            assert bmp[0, y] == _SNOW_INDEX, f"Row {y} should be snow"


# ---------------------------------------------------------------------------
# Expired hour handling
# ---------------------------------------------------------------------------

class TestExpiredHours:
    def test_all_expired_returns_zero(self, sim_display):
        hours = [
            make_hour(50, end="2026-05-07T08:00:00"),
            make_hour(50, end="2026-05-07T08:30:00"),
        ]
        assert _run(sim_display, hours) == 0

    def test_expired_hours_leave_bitmap_clear(self, sim_display):
        hours = [make_hour(50, end="2026-05-07T08:00:00")]
        _run(sim_display, hours)
        bmp = sim_display.temperature_forecast_bitmap
        for y in range(_HEIGHT):
            assert bmp[0, y] == 0

    def test_expired_hours_not_counted(self, sim_display):
        hours = [
            make_hour(50, end="2026-05-07T08:00:00"),  # expired
            make_hour(50, end="2026-05-07T08:00:00"),  # expired
            make_hour(50),                              # active
            make_hour(50),                              # active
        ]
        assert _run(sim_display, hours) == 2

    def test_first_active_hour_sets_current_temp_label(self, sim_display):
        hours = [
            make_hour(40, end="2026-05-07T08:00:00"),  # expired — should be skipped
            make_hour(72),                              # first active hour
        ]
        _run(sim_display, hours)
        assert sim_display.current_temp_label.text == "72°"


# ---------------------------------------------------------------------------
# Return count
# ---------------------------------------------------------------------------

class TestReturnCount:
    def test_returns_hours_plotted(self, sim_display):
        hours = [make_hour(50)] * 5
        assert _run(sim_display, hours) == 5

    def test_returns_up_to_display_width(self, sim_display):
        hours = [make_hour(50)] * 80  # more than 64 columns
        assert _run(sim_display, hours) == _WIDTH


# ---------------------------------------------------------------------------
# Clock/temp overlay repositioning
# ---------------------------------------------------------------------------

class TestOverlayRepositioning:
    def test_midpoint_temps_keep_overlay_at_top(self, sim_display):
        """Temperature line near the middle → peakpoint >= 8 → overlay stays at y=0."""
        hours = [make_hour(50)] * _WIDTH
        _run(sim_display, hours)
        assert sim_display.timetemp_group.y == 0

    def test_extreme_hot_temps_push_overlay_down(self, sim_display):
        """Temperature line near the top row → peakpoint < 8 → overlay pushed down."""
        hours = [make_hour(200)] * _WIDTH  # clamps to y=0 for every column
        _run(sim_display, hours)
        assert sim_display.timetemp_group.y > 0


# ---------------------------------------------------------------------------
# Status labels
# ---------------------------------------------------------------------------

class TestStatusLabels:
    def test_set_status_sets_text(self, sim_display):
        sim_display.set_status("network", "success", "Connected")
        assert sim_display.network_label.text == "Connected"

    def test_set_status_success_color(self, sim_display):
        from display import SUCCESS_COLOR
        sim_display.set_status("network", "success", "ok")
        assert sim_display.network_label.color == SUCCESS_COLOR

    def test_set_status_failure_color(self, sim_display):
        from display import FAILURE_COLOR
        sim_display.set_status("location", "failure", "No fix")
        assert sim_display.location_label.color == FAILURE_COLOR

    def test_set_status_query_color(self, sim_display):
        from display import QUERY_COLOR
        sim_display.set_status("station", "query", "Looking...")
        assert sim_display.station_label.color == QUERY_COLOR

    def test_set_status_shows_status_group(self, sim_display):
        sim_display.status_group.hidden = True
        sim_display.set_status("network", "success", "ok")
        assert not sim_display.status_group.hidden

    def test_clear_status_hides_group(self, sim_display):
        sim_display.set_status("network", "success", "ok")
        sim_display.clear_status()
        assert sim_display.status_group.hidden

    def test_clear_status_resets_all_text(self, sim_display):
        sim_display.set_status("network", "success", "A")
        sim_display.set_status("location", "success", "B")
        sim_display.set_status("station", "success", "C")
        sim_display.clear_status()
        assert sim_display.network_label.text == ""
        assert sim_display.location_label.text == ""
        assert sim_display.station_label.text == ""

    def test_set_status_unknown_label_raises(self, sim_display):
        with pytest.raises(ValueError):
            sim_display.set_status("bogus", "success", "text")

    def test_set_status_unknown_status_raises(self, sim_display):
        with pytest.raises(ValueError):
            sim_display.set_status("network", "bogus", "text")


# ---------------------------------------------------------------------------
# Current temperature label
# ---------------------------------------------------------------------------

class TestCurrentTempLabel:
    def test_first_hour_sets_temp_label(self, sim_display):
        _run(sim_display, [make_hour(72)])
        assert sim_display.current_temp_label.text == "72°"

    def test_temp_label_color_matches_palette(self, sim_display):
        """current_temp_label.color is taken from temperature_palette at render time."""
        _run(sim_display, [make_hour(50)])  # midpoint → palette index 6
        expected = sim_display.temperature_palette[_PALETTE_CENTER]
        assert sim_display.current_temp_label.color == expected


# ---------------------------------------------------------------------------
# Temp-range calibration screen
# ---------------------------------------------------------------------------

class TestDisplayTempRange:
    def test_temprange_group_hidden_by_default(self, sim_display):
        assert sim_display.temprange_group.hidden is True

    def test_show_temp_range_makes_group_visible(self, sim_display):
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_group.hidden is False

    def test_show_temp_range_max_label_text(self, sim_display):
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert "101" in sim_display.temprange_max_label.text

    def test_show_temp_range_min_label_text(self, sim_display):
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert "-10" in sim_display.temprange_min_label.text

    def test_show_temp_range_city_label_text(self, sim_display):
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_city_label.text == "Boston"

    def test_show_temp_range_station_id_label_text(self, sim_display):
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_id_label.text == "KBOS"

    def test_show_temp_range_max_label_uses_hot_color(self, sim_display):
        """Max label must use palette index 11 (hottest orange)."""
        hot_color = sim_display.temperature_palette[11]
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_max_label.color == hot_color

    def test_show_temp_range_min_label_uses_cold_color(self, sim_display):
        """Min label must use palette index 1 (coldest blue)."""
        cold_color = sim_display.temperature_palette[1]
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_min_label.color == cold_color

    def test_clear_status_hides_temprange_group(self, sim_display):
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_group.hidden is False
        sim_display.clear_status()
        assert sim_display.temprange_group.hidden is True

    def test_set_temp_range_updates_scale(self, sim_display):
        sim_display.set_temp_range(-20, 110)
        assert sim_display.temp_min == -20
        assert sim_display.temp_max == 110

    def test_show_temp_range_hides_status_group(self, sim_display):
        """Status labels must be hidden when temp-range screen is shown."""
        sim_display.set_status("network", "success", "MyNet")
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.status_group.hidden is True

    def test_show_temp_range_none_city_shows_empty(self, sim_display):
        """None city and station_id should not raise — show empty string."""
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range(None, None)
        assert sim_display.temprange_city_label.text == ""
        assert sim_display.temprange_id_label.text == ""
