"""Tests for Display rendering using the CPython displayio/font/text simulations.

The sim layer replaces MagicMock stubs for displayio, adafruit_bitmap_font,
adafruit_display_text, and the matrix hardware module — so the real Display
class runs on CPython and we can inspect the actual pixel data that would
appear on the LED matrix.
"""

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
_SNOW_INDEX = 4      # bright snow
_DIM_SNOW_INDEX = 5  # dim snow (off-pixel in pattern)

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


def make_hour(temperature, precipitation=0, snow_fraction=0.0, qpf_mm=0.0,
              start="2026-05-07T10:00:00", end="2026-05-07T11:00:00"):
    """Build a minimal Hour with the given weather values."""
    from station import Hour
    h = Hour()
    h.start = start
    h.end = end
    h.temperature = temperature
    h.precipitation = precipitation
    h.snow_fraction = snow_fraction
    h.qpf_mm = qpf_mm
    h.forecast = "Sim"
    return h


def _run(d, hours, historical=None):
    """Convenience wrapper around update_forecast."""
    if historical is None:
        historical = _NO_HISTORICAL
    if isinstance(hours, list):
        hours = dict(enumerate(hours))
    return d.update_forecast(hours, historical, _CURRENT_TIME)




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
        _run(sim_display, [make_hour(50, precipitation=100, snow_fraction=0.0, qpf_mm=2.5)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(_HEIGHT):
            assert bmp[0, y] == _RAIN_INDEX

    def test_half_rain_fills_bottom_half(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=50, snow_fraction=0.0, qpf_mm=2.5)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(16):
            assert bmp[0, y] == 0, f"Row {y} should be transparent"
        for y in range(16, _HEIGHT):
            assert bmp[0, y] == _RAIN_INDEX, f"Row {y} should be rain"

    def test_full_snow_fills_entire_column(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=100, snow_fraction=1.0)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(_HEIGHT):
            assert bmp[0, y] in (_SNOW_INDEX, _DIM_SNOW_INDEX)

    def test_half_precip_all_snow(self, sim_display):
        _run(sim_display, [make_hour(50, precipitation=50, snow_fraction=1.0)])
        bmp = sim_display.precipitation_forecast_bitmap
        for y in range(16):
            assert bmp[0, y] == 0
        for y in range(16, _HEIGHT):
            assert bmp[0, y] in (_SNOW_INDEX, _DIM_SNOW_INDEX)

    def test_mixed_rain_and_snow(self, sim_display):
        """Full precipitation, 50% snow: first half rain, second half snow."""
        _run(sim_display, [make_hour(50, precipitation=100, snow_fraction=0.5, qpf_mm=5.0)])
        bmp = sim_display.precipitation_forecast_bitmap
        # rain_row_count = round(0.5 * 32) = 16 → rows 0-15 rain, 16-31 snow
        for y in range(16):
            assert bmp[0, y] == _RAIN_INDEX, f"Row {y} should be rain"
        for y in range(16, _HEIGHT):
            assert bmp[0, y] in (_SNOW_INDEX, _DIM_SNOW_INDEX), f"Row {y} should be snow"


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
        assert sim_display._clock_group.y == 0

    def test_extreme_hot_temps_push_overlay_down(self, sim_display):
        """Temperature line near the top row → peakpoint < 8 → overlay pushed down."""
        hours = [make_hour(200)] * _WIDTH  # clamps to y=0 for every column
        _run(sim_display, hours)
        assert sim_display._clock_group.y > 0


# ---------------------------------------------------------------------------
# Status overlay
# ---------------------------------------------------------------------------

class TestStatusLabels:
    def test_show_status_makes_group_visible(self, sim_display):
        sim_display.show_weather()
        assert sim_display._status_group.hidden is True
        sim_display.show_status()
        assert sim_display._status_group.hidden is False

    def test_show_weather_hides_group(self, sim_display):
        sim_display.show_weather()
        assert sim_display._status_group.hidden is True


# ---------------------------------------------------------------------------
# Display.screen state tracking
# ---------------------------------------------------------------------------

class TestDisplayScreen:
    def test_initial_screen_is_boot(self, sim_display):
        assert sim_display.screen == sim_display.SCREEN_BOOT

    def test_show_status_sets_boot_screen(self, sim_display):
        sim_display.show_scale("Boston", "KBOS")
        sim_display.show_status()
        assert sim_display.screen == sim_display.SCREEN_BOOT

    def test_show_scale_sets_scale_screen(self, sim_display):
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display.screen == sim_display.SCREEN_SCALE

    def test_show_weather_sets_weather_screen(self, sim_display):
        sim_display.show_weather()
        assert sim_display.screen == sim_display.SCREEN_WEATHER


# ---------------------------------------------------------------------------
# set_location() — smart coordinate layout
# ---------------------------------------------------------------------------

class TestSetLocation:
    """set_location() routes text to the correct layout based on coordinate format."""

    def test_plain_text_sets_main_label(self, sim_display):
        """Plain text goes straight to _loc_main_label unchanged."""
        from display import QUERY_COLOR
        sim_display.set_location("Locating...", QUERY_COLOR)
        assert sim_display._loc_main_label.text  == "Locating..."
        assert sim_display._loc_main_label.color == QUERY_COLOR
        assert sim_display._loc_lon_label.text   == ""
        assert sim_display._loc_neg_tg.x         == -99

    def test_plain_error_text(self, sim_display):
        """Error-state plain text is rendered in a single label."""
        from display import FAILURE_COLOR
        sim_display.set_location("Location?", FAILURE_COLOR)
        assert sim_display._loc_main_label.text  == "Location?"
        assert sim_display._loc_main_label.color == FAILURE_COLOR
        assert sim_display._loc_lon_label.text   == ""

    def test_empty_string_hides_extras(self, sim_display):
        """Empty string (show_scale state) leaves all location elements blank."""
        from display import QUERY_COLOR
        sim_display.set_location("", QUERY_COLOR)
        assert sim_display._loc_main_label.text == ""
        assert sim_display._loc_lon_label.text  == ""
        assert sim_display._loc_neg_tg.x        == -99

    def test_none_text_does_not_crash(self, sim_display):
        """None must not crash — rendered as empty string."""
        from display import QUERY_COLOR
        sim_display.set_location(None, QUERY_COLOR)  # must not raise
        assert sim_display._loc_main_label.text == ""
        assert sim_display._loc_lon_label.text  == ""
        assert sim_display._loc_neg_tg.x        == -99

    def test_2digit_lon_east_coast(self, sim_display):
        """2-digit longitude (east coast) renders as single label with space separator."""
        from display import SUCCESS_COLOR
        sim_display.set_location("42.39,-71.11", SUCCESS_COLOR)
        assert sim_display._loc_main_label.text  == "42.39, -71.11"
        assert sim_display._loc_main_label.color == SUCCESS_COLOR
        assert sim_display._loc_lon_label.text   == ""
        assert sim_display._loc_neg_tg.x         == -99

    def test_2digit_lon_puerto_rico(self, sim_display):
        """Puerto Rico (southernmost US, 2-digit lon) fits in one label."""
        from display import SUCCESS_COLOR
        sim_display.set_location("18.47,-66.12", SUCCESS_COLOR)
        assert sim_display._loc_main_label.text == "18.47, -66.12"
        assert sim_display._loc_lon_label.text  == ""

    def test_2digit_lon_rounds_to_2dp(self, sim_display):
        """Coordinates with more than 2 decimal places are rounded to 2dp."""
        from display import SUCCESS_COLOR
        sim_display.set_location("42.395,-71.116", SUCCESS_COLOR)
        assert sim_display._loc_main_label.text == "42.40, -71.12"

    def test_3digit_lon_west_coast(self, sim_display):
        """3-digit longitude (west coast) splits across main label, drawn dash, and lon label."""
        from display import SUCCESS_COLOR
        sim_display.set_location("47.61,-122.33", SUCCESS_COLOR)
        assert sim_display._loc_main_label.text  == "47.61,"
        assert sim_display._loc_main_label.color == SUCCESS_COLOR
        assert sim_display._loc_lon_label.text   == "122.33"
        assert sim_display._loc_lon_label.color  == SUCCESS_COLOR
        assert sim_display._loc_neg_tg.x         >= 0   # visible, not parked

    def test_3digit_lon_neg_indicator_x_matches_main_width(self, sim_display):
        """Drawn minus is positioned at x = main label width."""
        from display import SUCCESS_COLOR
        sim_display.set_location("49.00,-124.07", SUCCESS_COLOR)
        assert sim_display._loc_neg_tg.x == sim_display._loc_main_label.width

    def test_3digit_lon_lon_label_x_is_neg_plus_2(self, sim_display):
        """Lon label starts 2px after the drawn minus indicator."""
        from display import SUCCESS_COLOR
        sim_display.set_location("49.00,-124.07", SUCCESS_COLOR)
        assert sim_display._loc_lon_label.x == sim_display._loc_neg_tg.x + 2

    def test_3digit_lon_alaska_extreme(self, sim_display):
        """Widest US coords (Alaska) parse and split correctly."""
        from display import SUCCESS_COLOR
        sim_display.set_location("71.29,-156.79", SUCCESS_COLOR)
        assert sim_display._loc_main_label.text == "71.29,"
        assert sim_display._loc_lon_label.text  == "156.79"

    def test_3digit_lon_total_width_fits_display(self, sim_display):
        """Widest realistic 3-digit lon layout stays within 64px."""
        from display import SUCCESS_COLOR
        sim_display.set_location("49.38,-179.99", SUCCESS_COLOR)
        total = sim_display._loc_lon_label.x + sim_display._loc_lon_label.width
        assert total <= 64, f"Layout is {total}px wide — exceeds 64px display"

    def test_2digit_lon_total_width_fits_display(self, sim_display):
        """Widest 2-digit lon layout (Puerto Rico) stays within 64px."""
        from display import SUCCESS_COLOR
        sim_display.set_location("18.91,-66.12", SUCCESS_COLOR)
        assert sim_display._loc_main_label.width <= 64

    def test_color_propagates_to_all_elements_3digit(self, sim_display):
        """Color is applied to both labels and the neg indicator palette in 3-digit mode."""
        from display import FAILURE_COLOR
        sim_display.set_location("47.61,-122.33", FAILURE_COLOR)
        assert sim_display._loc_main_label.color  == FAILURE_COLOR
        assert sim_display._loc_lon_label.color   == FAILURE_COLOR
        assert sim_display._loc_neg_palette[1]    == FAILURE_COLOR

    def test_switching_from_3digit_to_plain_hides_extras(self, sim_display):
        """After showing 3-digit coords, switching to plain text hides split elements."""
        from display import SUCCESS_COLOR, QUERY_COLOR
        sim_display.set_location("47.61,-122.33", SUCCESS_COLOR)
        sim_display.set_location("Seattle", QUERY_COLOR)
        assert sim_display._loc_main_label.text == "Seattle"
        assert sim_display._loc_lon_label.text  == ""
        assert sim_display._loc_neg_tg.x        == -99

    def test_show_scale_clears_location_slot(self, sim_display):
        """show_scale() blanks all location elements regardless of prior set_location call."""
        from display import SUCCESS_COLOR
        sim_display.set_location("47.61,-122.33", SUCCESS_COLOR)
        sim_display.show_scale("Seattle", "KSEA")
        assert sim_display._loc_main_label.text == ""
        assert sim_display._loc_lon_label.text  == ""
        assert sim_display._loc_neg_tg.x        == -99


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
# Temperature scale preview screen
# ---------------------------------------------------------------------------

class TestDisplayScalePreview:
    def test_status_group_visible_by_default(self, sim_display):
        """Status overlay is visible at startup for the boot-progress screen."""
        assert sim_display._status_group.hidden is False

    def test_show_scale_keeps_status_group_visible(self, sim_display):
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display._status_group.hidden is False

    def test_show_scale_top_label_text(self, sim_display):
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert "101" in sim_display._top_label.text

    def test_show_scale_network_label_text(self, sim_display):
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert "-10" in sim_display.network_label.text

    def test_show_scale_location_label_blank(self, sim_display):
        """Location slot is blanked on the scale preview — comfort band reads without text noise."""
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display._loc_main_label.text == ""
        assert sim_display._loc_lon_label.text  == ""

    def test_show_scale_station_label_blank(self, sim_display):
        """Station label is blanked on the scale preview — comfort band reads without text noise."""
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display.station_label.text == ""

    def test_show_scale_top_label_uses_hot_color(self, sim_display):
        """Max-temp label must use palette index 11 (hottest orange)."""
        hot_color = sim_display.temperature_palette[11]
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display._top_label.color == hot_color

    def test_show_scale_network_label_uses_cold_color(self, sim_display):
        """Min-temp label must use palette index 1 (coldest blue)."""
        cold_color = sim_display.temperature_palette[1]
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display.network_label.color == cold_color

    def test_show_weather_hides_status_group(self, sim_display):
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display._status_group.hidden is False
        sim_display.show_weather()
        assert sim_display._status_group.hidden is True

    def test_set_temp_scale_updates_scale(self, sim_display):
        sim_display.set_temp_scale(-20, 110)
        assert sim_display.temp_min == -20
        assert sim_display.temp_max == 110

    def test_show_scale_middle_labels_always_blank(self, sim_display):
        """show_scale() blanks the location slot and station label regardless of arguments."""
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display._loc_main_label.text == ""
        assert sim_display._loc_lon_label.text  == ""
        assert sim_display.station_label.text   == ""

    def test_show_scale_none_city_does_not_raise(self, sim_display):
        """None city and station_id must not raise."""
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale(None, None)  # must not raise


# ---------------------------------------------------------------------------
# Comfort zone band (_draw_comfort_zone)
# ---------------------------------------------------------------------------

class TestComfortZone:
    """_draw_comfort_zone() draws a horizontal band at COMFORT_LOW–COMFORT_HIGH °F."""

    def test_show_scale_draws_comfort_band(self, sim_display):
        """show_scale() populates at least one row of the comfort bitmap."""
        sim_display.show_scale("Boston", "KBOS")
        bmp = sim_display._comfort_bitmap
        lit = [y for y in range(32) if bmp[0, y] != 0]
        assert lit, "Expected at least one comfort-zone row to be lit"

    def test_offscale_fixed_max_below_comfort(self, sim_display):
        """Fixed scale max below COMFORT_LOW (extreme hot-only scale) — no crash, no draw."""
        sim_display.set_temp_scale(80, 120)
        sim_display._comfort_bitmap.fill(0)
        sim_display._draw_comfort_zone()
        bmp = sim_display._comfort_bitmap
        # Comfort zone is below the visible scale — all clamped to y=31.
        # y_top and y_bottom are both clamped to 31, so only row 31 may be lit.
        # The important thing is no crash and no pixels outside row 31.
        for y in range(0, 31):
            assert bmp[0, y] == 0, f"Unexpected pixel at row {y} for hot-only scale"

    def test_comfort_palette_uses_comfort_color(self, sim_display):
        """Comfort grid palette index 1 must be COMFORT_COLOR."""
        from display import COMFORT_COLOR
        assert sim_display._comfort_grid.pixel_shader[1] == COMFORT_COLOR

    def test_show_status_does_not_draw_comfort_band(self, sim_display):
        """show_status() is for boot progress — it must not touch the comfort bitmap."""
        sim_display._comfort_bitmap.fill(0)  # establish known-blank state; prior tests may have drawn
        sim_display.show_status()
        bmp = sim_display._comfort_bitmap
        lit = [y for y in range(32) if bmp[0, y] != 0]
        assert not lit, f"show_status() must not draw comfort zone; got lit rows {lit}"

    def test_second_show_scale_replaces_first(self, sim_display):
        """Calling show_scale() twice with different scales must produce only the second band.

        Simulates the scheduler fallback→ACIS-success path, where show_scale() is
        called first with a narrow fallback range and then with the real ACIS range.
        """
        # First call: narrow scale (Key West 42–95°F) → band near the middle
        sim_display.set_temp_scale(42, 95)
        sim_display.show_scale("Key West", "KEYW")

        # Second call: wide scale (Mt. Washington -38–82°F) → band near the top
        sim_display.set_temp_scale(-38, 82)
        sim_display.show_scale("Mt. Washington", "KMWN")

        bmp = sim_display._comfort_bitmap
        # Mt. Washington: midpoint=22, scale_factor=120/32=3.75
        # raw_top  = 16+(22-72)/3.75 = 2.67 → floor → 2
        # raw_bottom = 16+(22-68)/3.75 = 3.73 → ceil  → 4
        for y in range(2, 5):
            assert bmp[0, y] == 1, f"Row {y} should be lit for Mt. Washington scale"
        # Key West rows (13–17) must be cleared — no stale pixels.
        for y in range(13, 18):
            assert bmp[0, y] == 0, f"Row {y} is a stale Key West pixel that was not cleared"

    def test_narrow_scale_gives_wider_band(self, sim_display):
        """A narrower temperature scale spreads 4°F across more pixels.

        Key West (42–95°F, 53°F span) vs default (-5–105°F, 110°F span):
        the comfort band should occupy more rows on the Key West scale.
        """
        # Default scale band width — restore default scale explicitly since prior
        # tests in this class may have changed it.
        sim_display.set_temp_scale(-5, 105)
        sim_display.show_scale("Default", "KXXX")
        bmp = sim_display._comfort_bitmap
        default_rows = sum(1 for y in range(32) if bmp[0, y] != 0)

        # Key West (42–95°F) scale band width
        sim_display.set_temp_scale(42, 95)
        sim_display.show_scale("Key West", "KEYW")
        keyw_rows = sum(1 for y in range(32) if bmp[0, y] != 0)

        assert keyw_rows > default_rows, (
            f"Key West (53°F span) should have a wider comfort band than default "
            f"(110°F span): got {keyw_rows} vs {default_rows} rows"
        )

    def test_comfort_band_position_key_west(self, sim_display):
        """Key West (42–95°F): comfort zone rows at expected positions.

        midpoint=68.5, scale_factor=53/32≈1.65625
        raw_top    = 16+(68.5-72)/1.65625 ≈ 13.89 → floor → 13
        raw_bottom = 16+(68.5-68)/1.65625 ≈ 16.30 → ceil  → 17
        """
        sim_display.set_temp_scale(42, 95)
        sim_display.show_scale("Key West", "KEYW")
        bmp = sim_display._comfort_bitmap
        for y in range(13, 18):
            assert bmp[0, y] == 1, f"Row {y} should be lit for Key West comfort zone"
        for y in list(range(0, 13)) + list(range(18, 32)):
            assert bmp[0, y] == 0, f"Row {y} should be dark outside Key West comfort zone"


