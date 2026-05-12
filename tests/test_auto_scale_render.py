"""Render tests for AUTO_SCALE: calibration screen and scaled forecast display.

AUTO_SCALE queries RCC ACIS PRISM for the all-time record high/low at the
device location and uses those values as the temperature color scale instead
of the fixed TEMP_MIN/TEMP_MAX defaults (-5 / 105 °F).

Two test classes:

  TestCalibrationScreenRender
      Renders the four-line calibration screen (max temp, city, station ID,
      min temp) for five locations with meaningfully different temperature
      ranges.  Each test checks that:
        - the screen is visible (temprange_group not hidden)
        - the rendered image matches the reference PNG

      Locations and their all-time PRISM ranges (1981–2025):

        Location            | Low  | High | Note
        --------------------|------|------|-------------------------------
        Boston MA           | -10  |  101 | Close to defaults
        Chicago IL          | -26  |  102 | Cold min; compressed cold half
        Death Valley CA     |  22  |  129 | Extreme heat; highest US max
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

The historical-day ``network.post()`` call is distinguished from the
temp-range call by the number of elements in the query: the historical
query sends 4 elems (mint×2, maxt×2) while the temp-range query sends 2.
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

    Monkeypatches ``network.post`` to serve either the historical-slot JSON
    or the temp-range JSON depending on the number of elems in the query body
    (4 → historical, 2 → temp-range).

    Returns the populated Station (with temp_min / temp_max set).
    """
    griddata_json   = _load(f"{name}_griddata.json")
    hist_json       = _load(f"{name}_historical.json")
    temp_range_json = _load(f"{name}_temp_range.json")
    points_json     = _load(f"{name}_points.json")
    stations_json   = _load(f"{name}_stations.json")

    def _post(url, data):
        if len(data.get("elems", [])) == 2:
            return temp_range_json   # get_temp_range() query
        return hist_json             # get_historical_day() query

    monkeypatch.setattr(network, "get_stream", make_hourly_stream(f"{name}_hourly.json"))
    monkeypatch.setattr(network, "get",  lambda url, headers=None: griddata_json)
    monkeypatch.setattr(network, "post", _post)

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
    ("mt_washington_nh", -38,  82),
    ("tucson_az",         18, 115),
]


class TestCalibrationScreenRender:
    """Render the AUTO_SCALE calibration screen for five representative locations.

    Each test loads the location's NOAA points/stations metadata (for city name
    and station ID), applies the ACIS temp range, and renders the four-label
    screen.  Reference PNGs are saved on first run.
    """

    @pytest.mark.parametrize("location,exp_min,exp_max", _CALIBRATION_LOCATIONS)
    def test_calibration_screen(self, sim_display, request, monkeypatch,
                                location, exp_min, exp_max):
        """Render calibration screen for {location} and compare to reference."""
        temp_range_json = _load(f"{location}_temp_range.json")
        points_json     = _load(f"{location}_points.json")
        stations_json   = _load(f"{location}_stations.json")

        # Verify fixture values match expectations.
        assert temp_range_json["smry"][0] == exp_min
        assert temp_range_json["smry"][1] == exp_max

        props       = points_json["properties"]
        loc         = props["relativeLocation"]["properties"]
        city        = loc["city"]
        station_url = stations_json["features"][0]["id"]
        station_id  = station_url.split("/")[-1]

        sim_display.set_temp_range(exp_min, exp_max)
        sim_display.show_temp_range(city, station_id)

        assert not sim_display.temprange_group.hidden, (
            "temprange_group must be visible after show_temp_range()"
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
            f"calibration_{location}",
            state_dict=state,
        )

    def test_calibration_max_label_hot_color(self, sim_display, monkeypatch):
        """Max-temp label must use the hottest palette color (index 11)."""
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_max_label.color == sim_display.temperature_palette[11]

    def test_calibration_min_label_cold_color(self, sim_display, monkeypatch):
        """Min-temp label must use the coldest palette color (index 1)."""
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        assert sim_display.temprange_min_label.color == sim_display.temperature_palette[1]

    def test_calibration_screen_cleared_by_clear_status(self, sim_display):
        """clear_status() must hide the calibration screen."""
        sim_display.set_temp_range(-10, 101)
        sim_display.show_temp_range("Boston", "KBOS")
        sim_display.clear_status()
        assert sim_display.temprange_group.hidden


# ---------------------------------------------------------------------------
# Forecast rendering with auto-scaled display
# ---------------------------------------------------------------------------

class TestAutoScaleForecastRender:
    """Render hourly forecasts with the display scale set from ACIS temp-range data.

    Each test uses a real forecast fixture and the corresponding *_temp_range.json
    to replicate the full AUTO_SCALE pipeline: get_temp_range() populates
    station.temp_min / station.temp_max, then display.set_temp_range() shifts
    the forecast color scale before update_hourly_forecast() renders.

    Reference images are compared against committed PNGs.  Use --update-refs
    to regenerate them after intentional display changes.
    """

    def test_death_valley_auto_scale(self, sim_display, request, monkeypatch):
        """Death Valley: 22–129°F scale vs default -5–105°F.

        Default midpoint 50°F → auto-scale midpoint 75.5°F.  The 106–107°F
        forecast peak is now within the scale rather than clamped to the top."""
        station = _load_station_with_temp_range("death_valley_ca", monkeypatch)

        sim_display.set_temp_range(station.temp_min, station.temp_max)
        sim_display.update_hourly_forecast(
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

        sim_display.set_temp_range(station.temp_min, station.temp_max)
        sim_display.update_hourly_forecast(
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

        def _post(url, data):
            if len(data.get("elems", [])) == 2:
                return temp_range_json
            return hist_json

        monkeypatch.setattr(network, "get_stream",
                            make_hourly_stream("boston_now_hourly.json"))
        monkeypatch.setattr(network, "get",  lambda url, headers=None: griddata_json)
        monkeypatch.setattr(network, "post", _post)

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

        sim_display.set_temp_range(s.temp_min, s.temp_max)
        sim_display.update_hourly_forecast(
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

        sim_display.set_temp_range(station.temp_min, station.temp_max)
        sim_display.update_hourly_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )

        state = snapshot_state(station=station, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_tucson_az_auto_scale",
            state_dict=state,
        )

    def test_mt_washington_nh_auto_scale(self, sim_display, request, monkeypatch):
        """Mt. Washington NH: -38–82°F scale vs default -5–105°F.

        Scale narrows significantly (120°F span → 82°F-wide cold-biased range).
        The cold May temperatures (snow at 31°F) land deep in the cold palette."""
        station = _load_station_with_temp_range("mt_washington_nh", monkeypatch)

        sim_display.set_temp_range(station.temp_min, station.temp_max)
        sim_display.update_hourly_forecast(
            station.hourly, station.historical, station.hourly[0].start
        )

        state = snapshot_state(station=station, display=sim_display)
        state["auto_scale"] = True
        compare_or_save(
            request, sim_display,
            "forecast_mt_washington_nh_auto_scale",
            state_dict=state,
        )
