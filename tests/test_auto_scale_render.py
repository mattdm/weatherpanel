"""Render tests for AUTO_SCALE: scale preview screen and scaled forecast display.

AUTO_SCALE queries RCC ACIS PRISM for the all-time record high/low at the
device location and uses those values as the temperature color scale instead
of the fixed TEMP_MIN/TEMP_MAX defaults (-5 / 105 °F).

Two test classes:

  TestScalePreviewRender
      Renders the four-line scale preview screen (max temp, city, station ID,
      min temp) for six locations with meaningfully different temperature
      ranges.  Each test checks that:
        - the screen is visible (_status_group not hidden)
        - the rendered image matches the reference PNG

      Locations and their all-time PRISM ranges (1981–2025):

        Location            | Low  | High | Note
        --------------------|------|------|-------------------------------
        Boston MA           | -10  |  101 | Close to defaults
        Chicago IL          | -26  |  102 | Cold min; compressed cold half
        Death Valley CA     |  22  |  129 | Extreme heat; highest US max
        Key West FL         |  42  |   95 | Narrowest span; both bounds above default
        Mt. Washington NH   | -38  |   82 | Coldest min; highest-wind US peak
        Tucson AZ           |  18  |  115 | Hot desert; both ends above default

  TestAutoScaleForecastRender
      Renders a full hourly forecast with the display temp scale set to the
      ACIS-queried range rather than the defaults (-5 / 105 °F).

      Chosen for maximum visual contrast — locations where AUTO_SCALE shifts
      the midpoint and/or extends the range enough to change color assignments:

        death_valley_ca:  22–129 vs default  -5–105 → midpoint 75 vs 50 °F;
                          the 106–107 °F forecast peak is ON-scale (was clipped)
        chicago_il:       -26–102 vs default  -5–105 → midpoint 38 vs 50 °F;
                          50 °F hours look noticeably warmer against the colder scale
        boston_now:       -10–101 vs default  -5–105 → midpoint 45 vs 50 °F;
                          a subtle but measurable shift visible in the reference image

Fixtures
--------
Temperature-range responses are stored as ``{location}_temp_range.json``,
pulled once from RCC ACIS on 2026-05-12 and committed.  The format matches
the real API response for a two-element smry query:

    {"smry": [<all_time_low_degF>, <all_time_high_degF>]}

All other forecast fixtures (hourly, griddata, historical, points, stations)
are reused from the existing ``sample-forecasts/`` collection.

The historical-day POST call is distinguished from the temp-range call
by the number of elements in the query: the historical query sends 4 elems
(mint×2, maxt×2) while the temp-range query sends 2.
"""
import json
from pathlib import Path

import pytest

import network
from stream_helpers import make_hourly_stream
from station import Station
from render_helpers import compare_or_save
from state_snapshot import snapshot_state

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(name):
    path = SAMPLE_DIR / name
    if not path.exists():
        pytest.fail(f"Missing fixture: {path}")
    with open(path) as f:
        return json.load(f)


def _make_station():
    return Station({
        "GRIDPOINT_API":   "https://test/points",
        "HISTORICAL_API":  "https://test/historical",
    })


def _load_station_with_temp_range(name, monkeypatch):
    """Parse forecast + temp-range fixtures for a location.

    Monkeypatches ``network.request`` to serve either the historical-slot JSON
    or the temp-range JSON depending on the number of elems in the query body
    (4 → historical, 2 → temp-range) for POST, and griddata for GET.

    Returns the populated Station (with temp_min / temp_max set).
    """
    griddata_json   = _load(f"{name}_griddata.json")
    hist_json       = _load(f"{name}_historical.json")
    temp_range_json = _load(f"{name}_temp_range.json")
    points_json     = _load(f"{name}_points.json")
    stations_json   = _load(f"{name}_stations.json")

    def fake_request(verb, url, body=None, headers=None):
        if verb == "POST":
            if len(body.get("elems", [])) == 2:
                return temp_range_json   # get_temp_range() query
            return hist_json             # get_historical_day() query
        return griddata_json

    monkeypatch.setattr(network, "get_stream", make_hourly_stream(f"{name}_hourly.json"))
    monkeypatch.setattr(network, "request", fake_request)

    s = _make_station()
    s.hourly_url   = "https://test/hourly"
    s.griddata_url = "https://test/griddata"

    props = points_json["properties"]
    loc   = props["relativeLocation"]["properties"]
    s.city  = loc["city"]
    s.state = loc["state"]
    s.tz    = props["timeZone"]
    coord_str = props["@id"].split("/points/")[1]
    s.lat, s.lon = coord_str.split(",")
    s.location = f"{s.lat},{s.lon}"

    station_url  = stations_json["features"][0]["id"]
    s.station_id = station_url.split("/")[-1]

    s.get_hourly_forecast()
    s.get_griddata()

    today = s.hourly[0].start[:10]
    for slot in range(4):
        s.get_historical_day(slot, today)

    s.get_temp_range()   # populates s.temp_min / s.temp_max
    return s


# ---------------------------------------------------------------------------
# Calibration screen — five locations
# ---------------------------------------------------------------------------

# (name, expected_min, expected_max) — values from ACIS PRISM 1981–2025
_CALIBRATION_LOCATIONS = [
    ("boston",           -10, 101),
    ("chicago_il",       -26, 102),
    ("death_valley_ca",   22, 129),
    ("key_west_fl",       42,  95),
    ("mt_washington_nh", -38,  82),
    ("tucson_az",         18, 115),
]


class TestScalePreviewRender:
    """Render the AUTO_SCALE scale preview screen for five representative locations.

    Each test loads the location's NOAA points/stations metadata (for city name
    and station ID), applies the ACIS temp range, and renders the scale preview.
    The preview shows max temp (orange, top), the 68–72°F comfort zone band
    (dim green), and min temp (blue, bottom); city and station labels are blank.
    Reference PNGs are saved on first run.
    """

    @pytest.mark.parametrize("location,exp_min,exp_max", _CALIBRATION_LOCATIONS)
    def test_scale_preview_screen(self, sim_display, request, monkeypatch,
                                  location, exp_min, exp_max):
        """Render scale preview screen for {location} and compare to reference."""
        points_json   = _load(f"{location}_points.json")
        stations_json = _load(f"{location}_stations.json")

        props       = points_json["properties"]
        loc         = props["relativeLocation"]["properties"]
        city        = loc["city"]
        station_url = stations_json["features"][0]["id"]
        station_id  = station_url.split("/")[-1]

        sim_display.set_temp_scale(exp_min, exp_max)
        sim_display.show_scale(city, station_id)

        assert not sim_display._status_group.hidden, (
            "_status_group must be visible after show_scale()"
        )

        state = {
            "location": location,
            "temp_min": exp_min,
            "temp_max": exp_max,
            "city": city,
            "station_id": station_id,
        }
        compare_or_save(
            request, sim_display,
            f"auto_scale_{location}",
            state_dict=state,
        )

    def test_scale_max_label_hot_color(self, sim_display, monkeypatch):
        """Max-temp label must use the hottest palette color (index 11)."""
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display._top_label.color == sim_display.temperature_palette[11]

    def test_scale_min_label_cold_color(self, sim_display, monkeypatch):
        """Min-temp label must use the coldest palette color (index 1)."""
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        assert sim_display.network_label.color == sim_display.temperature_palette[1]

    def test_show_weather_hides_scale_preview(self, sim_display):
        """show_weather() must hide the scale preview screen."""
        sim_display.set_temp_scale(-10, 101)
        sim_display.show_scale("Boston", "KBOS")
        sim_display.show_weather()
        assert sim_display._status_group.hidden


# ---------------------------------------------------------------------------
# Forecast rendering with auto-scaled display
# ---------------------------------------------------------------------------

class TestAutoScaleForecastRender:
    """Render hourly forecasts with the display scale set from ACIS temp-range data.

    Each test uses a real forecast fixture and the corresponding *_temp_range.json
    to replicate the full AUTO_SCALE pipeline: get_temp_range() populates
    station.temp_min / station.temp_max, then display.set_temp_scale() shifts
    the forecast color scale before update_forecast() renders.

    Reference images are compared against committed PNGs.  Use --update-refs
    to regenerate them after intentional display changes.
    """

    def test_death_valley_auto_scale(self, sim_display, request, monkeypatch):
        """Death Valley: 22–129°F scale vs default -5–105°F.

        Default midpoint 50°F → auto-scale midpoint 75.5°F.  The 106–107°F
        forecast peak is now within the scale rather than clamped to the top."""
        station = _load_station_with_temp_range("death_valley_ca", monkeypatch)

        sim_display.set_temp_scale(station.temp_min, station.temp_max)
        sim_display.update_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )

        state = snapshot_state(station=station, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_death_valley_ca_auto_scale",
            state_dict=state,
        )

    def test_chicago_il_auto_scale(self, sim_display, request, monkeypatch):
        """Chicago IL: -26–102°F scale vs default -5–105°F.

        Cold-half palette now spans 64°F of headroom (vs 45°F).  Temperatures
        around 40–50°F look noticeably cooler relative to the wider cold range."""
        station = _load_station_with_temp_range("chicago_il", monkeypatch)

        sim_display.set_temp_scale(station.temp_min, station.temp_max)
        sim_display.update_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )

        state = snapshot_state(station=station, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_chicago_il_auto_scale",
            state_dict=state,
        )

    def test_boston_now_auto_scale(self, sim_display, request, monkeypatch):
        """Boston: -10–101°F scale vs default -5–105°F.

        A subtle but measurable shift: midpoint moves from 50°F to 45.5°F,
        making the 46–63°F current forecast appear slightly warmer-coded."""
        # boston_now fixtures; temp_range from boston_temp_range.json.
        temp_range_json = _load("boston_temp_range.json")
        griddata_json   = _load("boston_now_griddata.json")
        hist_json       = _load("boston_now_hist_10yr.json")
        points_json     = _load("boston_points.json")
        stations_json   = _load("boston_stations.json")

        def fake_request(verb, url, body=None, headers=None):
            if verb == "POST":
                if len(body.get("elems", [])) == 2:
                    return temp_range_json
                return hist_json
            return griddata_json

        monkeypatch.setattr(network, "get_stream",
                            make_hourly_stream("boston_now_hourly.json"))
        monkeypatch.setattr(network, "request", fake_request)

        s = _make_station()
        s.hourly_url   = "https://test/hourly"
        s.griddata_url = "https://test/griddata"

        props = points_json["properties"]
        loc   = props["relativeLocation"]["properties"]
        s.city  = loc["city"]
        s.state = loc["state"]
        s.tz    = props["timeZone"]
        coord_str = props["@id"].split("/points/")[1]
        s.lat, s.lon = coord_str.split(",")
        s.location = f"{s.lat},{s.lon}"
        station_url  = stations_json["features"][0]["id"]
        s.station_id = station_url.split("/")[-1]

        s.get_hourly_forecast()
        s.get_griddata()
        today = s.hourly[0].start[:10]
        for slot in range(4):
            s.get_historical_day(slot, today)
        s.get_temp_range()

        sim_display.set_temp_scale(s.temp_min, s.temp_max)
        sim_display.update_forecast(
            s.hourly, s.historical, s.hourly[0].start
        )

        state = snapshot_state(station=s, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_boston_now_auto_scale",
            state_dict=state,
        )

    def test_tucson_az_auto_scale(self, sim_display, request, monkeypatch):
        """Tucson AZ: 18–115°F scale vs default -5–105°F.

        Both bounds shift up.  Midpoint moves from 50°F to 66.5°F — the
        103°F forecast peak sits comfortably within the warm half rather than
        near the top of the scale."""
        station = _load_station_with_temp_range("tucson_az", monkeypatch)

        sim_display.set_temp_scale(station.temp_min, station.temp_max)
        sim_display.update_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )

        state = snapshot_state(station=station, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_tucson_az_auto_scale",
            state_dict=state,
        )

    def test_key_west_fl_auto_scale(self, sim_display, request, monkeypatch):
        """Key West FL: 42–95°F scale vs default -5–105°F.

        Both bounds shift up; midpoint moves from 50°F to 68.5°F — the largest
        midpoint displacement in the test suite.  The narrow 53°F span compresses
        the palette so each bucket covers only ~4.4°F instead of the default ~9°F."""
        station = _load_station_with_temp_range("key_west_fl", monkeypatch)

        sim_display.set_temp_scale(station.temp_min, station.temp_max)
        sim_display.update_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )

        state = snapshot_state(station=station, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_key_west_fl_auto_scale",
            state_dict=state,
        )

    def test_mt_washington_nh_auto_scale(self, sim_display, request, monkeypatch):
        """Mt. Washington NH: -38–82°F scale vs default -5–105°F.

        Scale narrows significantly (120°F span → 82°F-wide cold-biased range).
        The cold May temperatures (snow at 31°F) land deep in the cold palette."""
        station = _load_station_with_temp_range("mt_washington_nh", monkeypatch)

        sim_display.set_temp_scale(station.temp_min, station.temp_max)
        sim_display.update_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )

        state = snapshot_state(station=station, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_mt_washington_nh_auto_scale",
            state_dict=state,
        )


# ---------------------------------------------------------------------------
# Scale comparison — same forecast, different min/max
# ---------------------------------------------------------------------------

# (location, auto_min, auto_max)
_COMPARISON_LOCATIONS = [
    ("death_valley_ca", 22,  129),  # 110-111°F peak clamped at default, on-scale at auto
    ("chicago_il",      -26, 102),  # midpoint 50→38°F; 50°F hours look different
    ("key_west_fl",      42,  95),  # midpoint 50→68.5°F; entire scale shifted warm
    ("tucson_az",        18, 115),  # midpoint 50→66.5°F; 103°F peak near-extreme at default
]


def _make_display_with_scale(temp_min, temp_max):
    """Create a fresh Display with specific TEMP_MIN/TEMP_MAX.

    Font redirect and matrix stub are already patched by the sim_display
    fixture that is always present in the enclosing test.
    """
    import display as _display_module
    return _display_module.Display({
        'TEMP_MIN': temp_min,
        'TEMP_MAX': temp_max,
        'SWAP_GREEN_BLUE': False,
    })


def _topmost_lit_row(bitmap, col):
    """Return the topmost (lowest y index) non-transparent pixel row in column."""
    for row in range(bitmap.height):
        if bitmap[col, row] != 0:
            return row
    return None   # column is all-transparent


class TestScaleComparison:
    """Prove that the same forecast renders visually differently at default vs auto scale.

    Each test loads a location with a temp range well outside the default
    -5/105°F window, renders the forecast twice (once per scale), and asserts
    the pixel output differs.  This makes the scale machinery observable rather
    than just structural.
    """

    @pytest.mark.parametrize("location,auto_min,auto_max", _COMPARISON_LOCATIONS)
    def test_auto_scale_renders_differ_from_default(
        self, sim_display, monkeypatch, location, auto_min, auto_max
    ):
        """Same forecast must render differently at auto scale vs default -5/105°F."""
        station = _load_station_with_temp_range(location, monkeypatch)
        t = station.hourly[0].start

        # Default scale render (sim_display already uses -5/105).
        sim_display.update_forecast(station.hourly, station.historical, t)
        pixels_default = sim_display._display.render_to_pixels()

        # Auto scale render — fresh Display, same patched environment.
        d_auto = _make_display_with_scale(auto_min, auto_max)
        d_auto.update_forecast(station.hourly, station.historical, t)
        pixels_auto = d_auto._display.render_to_pixels()

        assert pixels_default != pixels_auto, (
            f"{location}: expected renders to differ between default scale "
            f"(-5/105) and auto scale ({auto_min}/{auto_max}), but they are identical"
        )

    def test_death_valley_peak_unclamped_on_auto_scale(self, sim_display, monkeypatch):
        """Death Valley 110-111°F hours are clamped to row 0 at default scale.

        At auto scale (max=129°F) they map to a non-zero row — the temperature
        line sits below the top edge, proving the scale is actually applied.

        Column 53 (2026-05-10T16) has the 111°F peak.
        """
        station = _load_station_with_temp_range("death_valley_ca", monkeypatch)
        t = station.hourly[0].start
        peak_col = 53   # 111°F at column 53 — confirmed from fixture

        # Default scale: 111°F clamps to row 0.
        sim_display.update_forecast(station.hourly, station.historical, t)
        row_default = _topmost_lit_row(sim_display.temperature_forecast_bitmap, peak_col)
        assert row_default == 0, (
            f"Expected 111°F to clamp to row 0 at default scale, got row {row_default}"
        )

        # Auto scale (22/129): 111°F maps to row ~5, well away from the edge.
        d_auto = _make_display_with_scale(station.temp_min, station.temp_max)
        d_auto.update_forecast(station.hourly, station.historical, t)
        row_auto = _topmost_lit_row(d_auto.temperature_forecast_bitmap, peak_col)
        assert row_auto is not None and row_auto > 0, (
            f"Expected 111°F to be off row 0 at auto scale (22/129), got row {row_auto}"
        )
        assert row_auto > row_default, (
            f"Expected auto-scale row ({row_auto}) > default row ({row_default}) "
            "for 111°F peak — auto scale should move it away from the clamped top"
        )
