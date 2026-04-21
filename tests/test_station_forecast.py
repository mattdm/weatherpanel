"""Sample-forecast-driven tests for Station forecast and historical parsing.

Replays recorded NOAA hourly + griddata + historical JSON through Station
methods via monkeypatched network.get() / network.post(), verifying the full
pipeline from raw API response through to snow_fraction and historical baseline.
"""
import json
from pathlib import Path

import pytest

import network
from station import Station, Hour, SNOW_HINT_MINIMUMS, _parse_utc_key, _expand_time_series

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"


def _load(name):
    with open(SAMPLE_DIR / name) as f:
        return json.load(f)


@pytest.fixture
def station():
    config = {
        "GEOLOCATION_API": "http://test/geo",
        "GRIDPOINT_API": "https://test/points",
        "HISTORICAL_API": "https://test/historical",
    }
    s = Station(config)
    s.hourly_url = "https://test/hourly"
    s.griddata_url = "https://test/griddata"
    s.station_id = "TEST"
    s.lat = "39.317"
    s.lon = "-120.333"
    s.location = "39.317,-120.333"
    return s


def _run_hourly_and_griddata(station, name, monkeypatch):
    """Load sample hourly + griddata for `name`, replay through Station methods."""
    hourly_data = _load(f"{name}_hourly.json")
    griddata_data = _load(f"{name}_griddata.json")

    call_count = {"n": 0}

    def fake_get(url, headers=None):
        call_count["n"] += 1
        if "hourly" in url or call_count["n"] == 1:
            return hourly_data
        return griddata_data

    monkeypatch.setattr(network, "get", fake_get)
    station.get_hourly_forecast()
    station.get_griddata()
    return station


# ---------------------------------------------------------------------------
# Soda Springs: rain-to-snow transition
# ---------------------------------------------------------------------------

class TestSodaSpringsSnow:
    """Soda Springs CA: rain/snow mix transitioning to heavy snow."""

    def test_hourly_parses_all_periods(self, station, monkeypatch):
        hourly_data = _load("soda_springs_hourly.json")
        monkeypatch.setattr(network, "get", lambda url, headers=None: hourly_data)
        count = station.get_hourly_forecast()
        assert count == 65

    def test_hourly_has_temperature_and_precip(self, station, monkeypatch):
        hourly_data = _load("soda_springs_hourly.json")
        monkeypatch.setattr(network, "get", lambda url, headers=None: hourly_data)
        station.get_hourly_forecast()
        for h in station.hourly:
            assert h.temperature is not None
            assert h.precipitation is not None
            assert h.start is not None
            assert h.end is not None

    def test_griddata_populates_snow_fraction(self, station, monkeypatch):
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        fractions = [h.snow_fraction for h in station.hourly]
        assert all(f is not None for f in fractions), "Every hour should have a snow_fraction after get_griddata"

    def test_early_hours_no_snow(self, station, monkeypatch):
        """Hours with no snow keywords in the text forecast should have snow_fraction == 0.

        The first four Soda Springs hours are a mix: indices 0 and 3 are
        'Partly Cloudy' and 'Chance Rain' (no snow keyword → 0.0), while
        indices 1 and 2 are 'Slight Chance Rain And Snow' / 'Chance Rain And Snow'
        (snow keyword present → text-hint minimum applied, not zero).
        """
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        no_keyword_hours = [h for h in station.hourly[:4]
                            if "Snow" not in (h.forecast or "")]
        assert len(no_keyword_hours) > 0, "Expected at least one non-snow hour in first four"
        for h in no_keyword_hours:
            assert h.snow_fraction == 0.0, f"Hour {h.start} ({h.forecast}) should have no snow"

    def test_later_hours_have_snow(self, station, monkeypatch):
        """Hours with 'Heavy Snow' forecast should have snow_fraction > 0."""
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        snow_hours = [h for h in station.hourly if "Snow" in (h.forecast or "") and "Rain" not in (h.forecast or "")]
        assert len(snow_hours) > 0, "Expected some snow-only hours in Soda Springs sample"
        for h in snow_hours:
            assert h.snow_fraction > 0, f"Hour {h.start} ({h.forecast}) should have snow_fraction > 0"

    def test_rain_to_snow_transition(self, station, monkeypatch):
        """The forecast transitions from rain/no-snow to snow — verify the boundary."""
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        saw_zero = False
        saw_positive = False
        for h in station.hourly:
            if h.snow_fraction == 0.0:
                saw_zero = True
            if h.snow_fraction > 0:
                saw_positive = True
        assert saw_zero, "Should have some hours with no snow"
        assert saw_positive, "Should have some hours with snow"

    def test_snow_fraction_in_valid_range(self, station, monkeypatch):
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        for h in station.hourly:
            assert 0.0 <= h.snow_fraction <= 1.0, f"snow_fraction {h.snow_fraction} out of range for {h.start}"

    def test_rain_and_snow_hours_get_hint(self, station, monkeypatch):
        """Hours with 'Rain And Snow' text but zero griddata snow should get the
        'Snow' tier minimum (0.3), not 0.0.

        This is the core regression test for the griddata granularity mismatch:
        the first non-zero snowfallAmount window in the Soda Springs sample starts
        hours after the text forecast starts saying 'Rain And Snow'.
        """
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        rain_and_snow_hours = [h for h in station.hourly if "Rain And Snow" in (h.forecast or "")]
        assert len(rain_and_snow_hours) > 0, "Expected 'Rain And Snow' hours in Soda Springs sample"
        snow_hint = SNOW_HINT_MINIMUMS["Snow"]
        for h in rain_and_snow_hours:
            assert h.snow_fraction >= snow_hint, (
                f"Hour {h.start} ({h.forecast!r}) should have snow_fraction >= {snow_hint}, "
                f"got {h.snow_fraction}"
            )

    def test_pure_rain_hours_no_hint(self, station, monkeypatch):
        """Hours with 'Chance Rain' (no snow keyword) should stay at snow_fraction == 0.0."""
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        rain_only_hours = [h for h in station.hourly if h.forecast == "Chance Rain"]
        assert len(rain_only_hours) > 0, "Expected 'Chance Rain' hours in Soda Springs sample"
        for h in rain_only_hours:
            assert h.snow_fraction == 0.0, (
                f"Hour {h.start} ({h.forecast!r}) should have snow_fraction == 0.0, "
                f"got {h.snow_fraction}"
            )


# ---------------------------------------------------------------------------
# Snow text-hint keyword tier unit tests (no network)
# ---------------------------------------------------------------------------

class TestSnowHintKeywords:
    """Unit tests for SNOW_HINT_MINIMUMS keyword matching without network calls.

    These exercise the hint logic directly via synthetic Hour objects, verifying
    each keyword tier and edge cases.
    """

    def _make_hour(self, forecast):
        h = Hour()
        h.snow_fraction = 0.0
        h.forecast = forecast
        return h

    def _apply_hints(self, h):
        """Replicate the hint logic from get_griddata() for isolated testing."""
        if h.snow_fraction == 0.0:
            hints = [v for kw, v in SNOW_HINT_MINIMUMS.items() if kw in (h.forecast or "")]
            if hints:
                h.snow_fraction = max(hints)
        return h

    @pytest.mark.parametrize("forecast,expected", [
        ("Wintry Mix",                  0.5),
        ("Chance Wintry Mix",           0.5),
        ("Sleet",                       0.5),
        ("Rain/Sleet Likely",           0.5),
        ("Chance Rain/Sleet",           0.5),
        ("Flurries",                    0.4),
        ("Chance Flurries",             0.4),
        ("Flurries/Rain Likely",        0.4),
        ("Snow",                        0.3),
        ("Heavy Snow",                  0.3),
        ("Rain And Snow Likely",        0.3),
        ("Chance Rain And Snow",        0.3),
        ("Slight Chance Rain And Snow", 0.3),
        ("Freezing Rain",               0.1),
        ("Chance Freezing Rain",        0.1),
        ("Freezing Drizzle",            0.1),
        ("Chance Freezing Drizzle",     0.1),
    ])
    def test_keyword_tier(self, forecast, expected):
        h = self._make_hour(forecast)
        self._apply_hints(h)
        assert h.snow_fraction == expected, (
            f"{forecast!r}: expected {expected}, got {h.snow_fraction}"
        )

    def test_compound_snow_sleet_takes_max(self):
        """'Snow/Sleet' matches both 'Snow' (0.3) and 'Sleet' (0.5); max wins."""
        h = self._make_hour("Snow/Sleet")
        self._apply_hints(h)
        assert h.snow_fraction == 0.5

    def test_blowing_snow_gets_snow_tier(self):
        """'Blowing Snow' is an NWS obstruction (wind-blown existing snow), not
        falling precip, but it contains 'Snow' so it picks up the 0.3 hint.
        For a display panel this is acceptable — there is clearly snow present.
        """
        h = self._make_hour("Blowing Snow")
        self._apply_hints(h)
        assert h.snow_fraction == 0.3

    def test_no_keyword_no_hint(self):
        """Forecasts with no frozen-precip keywords should not be modified."""
        for forecast in ("Partly Cloudy", "Chance Rain", "Rain", "Sunny", "Fog", "Thunderstorm"):
            h = self._make_hour(forecast)
            self._apply_hints(h)
            assert h.snow_fraction == 0.0, f"{forecast!r} should not receive a snow hint"

    def test_existing_nonzero_fraction_not_overwritten(self):
        """The hint loop only fires when snow_fraction == 0.0; existing values are preserved."""
        h = self._make_hour("Rain And Snow Likely")
        h.snow_fraction = 0.8  # already set by griddata
        self._apply_hints(h)
        assert h.snow_fraction == 0.8, "Non-zero snow_fraction should not be overwritten by hint"

    def test_none_forecast_no_crash(self):
        """An Hour with forecast=None should not raise and should stay at 0.0."""
        h = self._make_hour(None)
        self._apply_hints(h)
        assert h.snow_fraction == 0.0


# ---------------------------------------------------------------------------
# Yosemite: all-snow
# ---------------------------------------------------------------------------

class TestYosemiteSnow:
    """Yosemite high country: simpler all-snow progression."""

    def test_hourly_parses(self, station, monkeypatch):
        hourly_data = _load("yosemite_hourly.json")
        monkeypatch.setattr(network, "get", lambda url, headers=None: hourly_data)
        count = station.get_hourly_forecast()
        assert count == 65

    def test_has_snow_fraction_after_griddata(self, station, monkeypatch):
        _run_hourly_and_griddata(station, "yosemite", monkeypatch)
        snow_hours = [h for h in station.hourly if h.snow_fraction and h.snow_fraction > 0]
        assert len(snow_hours) > 0, "Yosemite sample should have hours with snow"

    def test_snow_fraction_valid_range(self, station, monkeypatch):
        _run_hourly_and_griddata(station, "yosemite", monkeypatch)
        for h in station.hourly:
            assert 0.0 <= h.snow_fraction <= 1.0


# ---------------------------------------------------------------------------
# Phoenix: dry, zero precip baseline
# ---------------------------------------------------------------------------

class TestPhoenixDry:
    """Phoenix AZ: hot, dry — should have zero snow and minimal precip."""

    def test_hourly_parses(self, station, monkeypatch):
        hourly_data = _load("phoenix_hourly.json")
        monkeypatch.setattr(network, "get", lambda url, headers=None: hourly_data)
        count = station.get_hourly_forecast()
        assert count == 65

    def test_no_snow(self, station, monkeypatch):
        _run_hourly_and_griddata(station, "phoenix", monkeypatch)
        for h in station.hourly:
            assert h.snow_fraction == 0.0, f"Phoenix should have no snow at {h.start}"


# ---------------------------------------------------------------------------
# UTC key alignment: verify hourly local times match griddata UTC keys
# ---------------------------------------------------------------------------

class TestUtcKeyAlignment:
    """Verify that _parse_utc_key aligns hourly periods with griddata windows.

    This is the highest-value test: if the UTC conversion is off by an hour or
    day, snow_fraction silently lands on the wrong hours or misses entirely."""

    def test_soda_springs_utc_keys_hit_griddata(self, station, monkeypatch):
        """Every hourly period's UTC key should exist somewhere in the
        expanded griddata time series (at least for the overlapping window)."""
        hourly_data = _load("soda_springs_hourly.json")
        griddata_data = _load("soda_springs_griddata.json")

        qpf_values = griddata_data["properties"]["quantitativePrecipitation"]["values"]
        snow_values = griddata_data["properties"]["snowfallAmount"]["values"]
        qpf_keys = set(_expand_time_series(qpf_values).keys())
        snow_keys = set(_expand_time_series(snow_values).keys())
        griddata_keys = qpf_keys | snow_keys

        periods = hourly_data["properties"]["periods"][:65]
        matched = 0
        for p in periods:
            utc_key = _parse_utc_key(p["startTime"])
            if utc_key in griddata_keys:
                matched += 1

        assert matched > 30, (
            f"Only {matched}/65 hourly UTC keys matched griddata — "
            "likely a timezone conversion bug"
        )

    def test_snow_hours_have_matching_griddata(self, station, monkeypatch):
        """Hours marked 'Heavy Snow' in the forecast should have matching
        griddata keys with non-zero snowfallAmount."""
        hourly_data = _load("soda_springs_hourly.json")
        griddata_data = _load("soda_springs_griddata.json")

        snow_values = griddata_data["properties"]["snowfallAmount"]["values"]
        snow_by_hour = _expand_time_series(snow_values)

        snow_forecast_hours = [
            p for p in hourly_data["properties"]["periods"][:65]
            if "Heavy Snow" in p["shortForecast"]
        ]
        assert len(snow_forecast_hours) > 0

        matched_with_snow = 0
        for p in snow_forecast_hours:
            utc_key = _parse_utc_key(p["startTime"])
            if snow_by_hour.get(utc_key, 0) > 0:
                matched_with_snow += 1

        assert matched_with_snow > 0, (
            "No 'Heavy Snow' hours matched non-zero snowfallAmount in griddata — "
            "UTC key alignment is broken"
        )


# ---------------------------------------------------------------------------
# Parametrized: every sample parses without error
# ---------------------------------------------------------------------------

ALL_SAMPLES = [p.stem.replace("_hourly", "")
               for p in sorted(SAMPLE_DIR.glob("*_hourly.json"))]


@pytest.mark.parametrize("name", ALL_SAMPLES)
class TestAllSamplesParse:
    """Every captured sample should parse through get_hourly_forecast and get_griddata."""

    def test_hourly_parses(self, station, monkeypatch, name):
        hourly_data = _load(f"{name}_hourly.json")
        monkeypatch.setattr(network, "get", lambda url, headers=None: hourly_data)
        count = station.get_hourly_forecast()
        assert count == 65
        assert len(station.hourly) == 65

    def test_griddata_populates_snow_fraction(self, station, monkeypatch, name):
        _run_hourly_and_griddata(station, name, monkeypatch)
        for h in station.hourly:
            assert h.snow_fraction is not None
            assert 0.0 <= h.snow_fraction <= 1.0


# ---------------------------------------------------------------------------
# Historical baseline parsing
# ---------------------------------------------------------------------------

HISTORICAL_SAMPLES = [p.stem.replace("_historical", "")
                      for p in sorted(SAMPLE_DIR.glob("*_historical.json"))]

HISTORICAL_LATLONS = {
    "boston":      ("42.36", "-71.06"),
    "phoenix":    ("33.45", "-112.07"),
    "fargo":      ("46.87", "-96.79"),
    "honolulu":   ("21.31", "-157.86"),
    "somerville": ("42.39", "-71.10"),
    "elkhart":    ("41.68", "-85.97"),
}


@pytest.fixture
def hist_station():
    config = {
        "GEOLOCATION_API": "http://test/geo",
        "GRIDPOINT_API": "https://test/points",
        "HISTORICAL_API": "https://test/historical",
    }
    s = Station(config)
    s.station_id = "TEST"
    return s


@pytest.mark.parametrize("name", HISTORICAL_SAMPLES)
class TestHistoricalParsing:
    """Replay recorded RCC ACIS responses through get_historical()."""

    def test_parses_four_values(self, hist_station, monkeypatch, name):
        hist_data = _load(f"{name}_historical.json")
        lat, lon = HISTORICAL_LATLONS[name]
        hist_station.lat = lat
        hist_station.lon = lon
        monkeypatch.setattr(network, "post", lambda url, data: hist_data)

        result = hist_station.get_historical("2026-04-21")
        assert result is not None
        for key in ("low", "ave-low", "high", "ave-high", "date"):
            assert key in hist_station.historical

    def test_values_are_floats(self, hist_station, monkeypatch, name):
        hist_data = _load(f"{name}_historical.json")
        lat, lon = HISTORICAL_LATLONS[name]
        hist_station.lat = lat
        hist_station.lon = lon
        monkeypatch.setattr(network, "post", lambda url, data: hist_data)

        hist_station.get_historical("2026-04-21")
        for key in ("low", "ave-low", "high", "ave-high"):
            assert isinstance(hist_station.historical[key], float)

    def test_sanity_ordering(self, hist_station, monkeypatch, name):
        """Record low <= average low <= average high <= record high."""
        hist_data = _load(f"{name}_historical.json")
        lat, lon = HISTORICAL_LATLONS[name]
        hist_station.lat = lat
        hist_station.lon = lon
        monkeypatch.setattr(network, "post", lambda url, data: hist_data)

        hist_station.get_historical("2026-04-21")
        h = hist_station.historical
        assert h["low"] <= h["ave-low"], f"low {h['low']} > ave-low {h['ave-low']}"
        assert h["ave-low"] <= h["ave-high"], f"ave-low {h['ave-low']} > ave-high {h['ave-high']}"
        assert h["ave-high"] <= h["high"], f"ave-high {h['ave-high']} > high {h['high']}"

    def test_values_match_captured_data(self, hist_station, monkeypatch, name):
        """Regression guard: parsed values should match what is in the JSON."""
        hist_data = _load(f"{name}_historical.json")
        lat, lon = HISTORICAL_LATLONS[name]
        hist_station.lat = lat
        hist_station.lon = lon
        monkeypatch.setattr(network, "post", lambda url, data: hist_data)

        hist_station.get_historical("2026-04-21")
        smry = hist_data["smry"]
        assert hist_station.historical["low"] == float(smry[0][0])
        assert hist_station.historical["ave-low"] == float(smry[0][1])
        assert hist_station.historical["high"] == float(smry[1][0])
        assert hist_station.historical["ave-high"] == float(smry[1][1])


class TestHistoricalFailure:
    """get_historical() should handle failures gracefully."""

    def test_post_returns_none(self, hist_station, monkeypatch):
        hist_station.lat = "42.36"
        hist_station.lon = "-71.06"
        monkeypatch.setattr(network, "post", lambda url, data: None)

        result = hist_station.get_historical("2026-04-21")
        assert result is None
        assert hist_station.historical == {}

    def test_missing_smry_key(self, hist_station, monkeypatch):
        hist_station.lat = "42.36"
        hist_station.lon = "-71.06"
        monkeypatch.setattr(network, "post", lambda url, data: {"other": "data"})

        result = hist_station.get_historical("2026-04-21")
        assert result is None

    def test_no_lat_lon(self, hist_station, monkeypatch):
        monkeypatch.setattr(network, "post", lambda url, data: {"smry": [[1, 2], [3, 4]]})

        result = hist_station.get_historical("2026-04-21")
        assert result is None
