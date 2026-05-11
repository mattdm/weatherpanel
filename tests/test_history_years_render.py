"""Render tests showing the effect of different HISTORY_YEARS on color coding.

Uses a live Boston forecast (fetched 2026-05-11) with five historical windows
from the ACIS PRISM grid-21 dataset:

  window | record low | ave-low | ave-high | record high
  -------|------------|---------|----------|------------
    1 yr |     47 °F  |  48 °F  |   62 °F  |    70 °F
    5 yr |     44 °F  |  49 °F  |   62 °F  |    81 °F
   10 yr |     34 °F  |  48 °F  |   62 °F  |    81 °F
   20 yr |     34 °F  |  50 °F  |   64 °F  |    88 °F
   44 yr |     34 °F  |  49 °F  |   65 °F  |    90 °F  ← PRISM maximum (data starts 1981)

Current forecast: 46–63 °F.  Key visual effects:

- **1 yr** — record extremes are very close to the averages (47–70).  Any
  temperature just 1–2 °F below the 48 °F average immediately hits maximum
  blue intensity because the entire cold half of the palette spans only 1 °F
  (48 → 47).  Similarly above the 62 °F average the scale is compressed to
  8 °F (62 → 70).

- **5–10 yr** — record low drops to 34–44 °F, stretching the cold half of the
  palette.  The same 46–47 °F temperatures now appear as light-to-mid blue
  rather than maximum blue.

- **20–44 yr** — the average-low creeps up to 49–50 °F, classifying more
  hours as "below normal", but the record range is widest so the gradient is
  most gradual.  The record high grows to 88–90 °F, giving more room for warm
  colors above the average high.

Note: PRISM data starts in 1981, so 50-year windows return an error.  44 yr
(sdate=1982) is the longest reliable window and is used in place of 50 yr.

The 20 yr and 44 yr windows have different ACIS smry values (ave-low 50 vs
49 °F; record high 88 vs 90 °F) but render identically for this particular
forecast snapshot.  The 8-bucket color palette quantizes both to the same
integer bucket indices for every temperature in the 46–63 °F forecast range:
the 1 °F difference in ave-low and the 2 °F difference in record high both
fall below the ~3 °F bucket width.  This is expected — the two reference
images are intentionally equal, demonstrating that palette quantization limits
the visible precision of the historical window.
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

_WINDOWS = [
    (1,  "boston_now_hist_1yr.json"),
    (5,  "boston_now_hist_5yr.json"),
    (10, "boston_now_hist_10yr.json"),
    (20, "boston_now_hist_20yr.json"),
    (44, "boston_now_hist_44yr.json"),   # PRISM max; 30yr = 20yr for this dataset
]


def _load(name):
    path = SAMPLE_DIR / name
    if not path.exists():
        pytest.fail(f"Missing fixture: {path}")
    with open(path) as f:
        return json.load(f)


def _load_boston_now(history_years, hist_file, monkeypatch):
    """Parse the live Boston forecast with a specific historical window.

    Uses boston_now_hourly.json and boston_now_griddata.json for the forecast,
    the specified hist_file for the climate baseline, and the existing
    boston_points.json / boston_stations.json for station metadata.
    Returns a Station with history_years set so snapshot_state captures it.
    """
    griddata_json = _load("boston_now_griddata.json")
    hist_json     = _load(hist_file)
    points_json   = _load("boston_points.json")
    stations_json = _load("boston_stations.json")

    monkeypatch.setattr(network, "get_stream", make_hourly_stream("boston_now_hourly.json"))
    monkeypatch.setattr(network, "get",        lambda url, headers=None: griddata_json)
    monkeypatch.setattr(network, "post",       lambda url, data: hist_json)

    config = {
        "GEOLOCATION_API": "http://test/geo",
        "GRIDPOINT_API":   "https://test/points",
        "HISTORICAL_API":  "https://test/historical",
        "HISTORY_YEARS":   history_years,
    }
    s = Station(config)
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

    return s


class TestHistoryYearsRender:
    """Render the same live Boston forecast with five historical windows.

    Each test produces one reference image; together they illustrate how the
    color-coding scale compresses or stretches depending on how many years of
    climate data define the record extremes.
    """

    @pytest.mark.parametrize("years,hist_file", _WINDOWS)
    def test_boston_now_history_window(
        self, sim_display, request, monkeypatch, years, hist_file
    ):
        """Render live Boston forecast with a {years}-year PRISM baseline."""
        station = _load_boston_now(years, hist_file, monkeypatch)
        current_time = station.hourly[0].start

        sim_display.update_hourly_forecast(
            station.hourly, station.historical, current_time
        )

        state = snapshot_state(station=station, display=sim_display)
        state["history_years"] = years

        compare_or_save(
            request, sim_display,
            f"boston_now_history_{years}yr",
            state_dict=state,
        )
