"""Sample-forecast-driven tests for Station forecast and historical parsing.

Replays recorded NOAA hourly + griddata + historical JSON through Station
methods via monkeypatched network.get_stream() / network.request(),
verifying the full pipeline from raw API response through to snow_fraction and
historical baseline.
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import network
from stream_helpers import (
    make_hourly_stream,
    make_griddata_stream,
    make_stream_router,
    dict_to_stream as _dict_to_stream,
)
from station import Station, Hour, FORECAST_HOURS, SNOW_HINT_MINIMUMS, _apply_snow_hint, _parse_utc_key, _iter_time_series

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"


def _load(name):
    with open(SAMPLE_DIR / name) as f:
        return json.load(f)


def _load_bytes(name):
    return (SAMPLE_DIR / name).read_bytes()



@pytest.fixture
def station():
    config = {
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
    monkeypatch.setattr(network, "get_stream", make_stream_router(
        make_hourly_stream(f"{name}_hourly.json"),
        make_griddata_stream(f"{name}_griddata.json"),
    ))
    station.get_hourly_forecast()
    station.get_griddata()
    return station


# ---------------------------------------------------------------------------
# Soda Springs: rain-to-snow transition
# ---------------------------------------------------------------------------

class TestSodaSpringsSnow:
    """Soda Springs CA: rain/snow mix transitioning to heavy snow."""

    def test_early_hours_no_snow(self, station, monkeypatch):
        """Hours with no snow keywords in the text forecast should have snow_fraction == 0.

        The first four Soda Springs hours are a mix: indices 0 and 3 are
        'Partly Cloudy' and 'Chance Rain' (no snow keyword → 0.0), while
        indices 1 and 2 are 'Slight Chance Rain And Snow' / 'Chance Rain And Snow'
        (snow keyword present → text-hint minimum applied, not zero).
        """
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        no_keyword_hours = [h for h in list(station.hourly.values())[:4]
                            if "Snow" not in (h.forecast or "")]
        assert len(no_keyword_hours) > 0, "Expected at least one non-snow hour in first four"
        for h in no_keyword_hours:
            assert h.snow_fraction == 0.0, f"Hour {h.start} ({h.forecast}) should have no snow"

    def test_later_hours_have_snow(self, station, monkeypatch):
        """Hours with 'Heavy Snow' forecast should have snow_fraction > 0."""
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        snow_hours = [h for h in station.hourly.values() if "Snow" in (h.forecast or "") and "Rain" not in (h.forecast or "")]
        assert len(snow_hours) > 0, "Expected some snow-only hours in Soda Springs sample"
        for h in snow_hours:
            assert h.snow_fraction > 0, f"Hour {h.start} ({h.forecast}) should have snow_fraction > 0"

    def test_rain_and_snow_hours_get_hint(self, station, monkeypatch):
        """Hours with 'Rain And Snow' text but zero griddata snow should get the
        'Snow' tier minimum (0.3), not 0.0.

        This is the core regression test for the griddata granularity mismatch:
        the first non-zero snowfallAmount window in the Soda Springs sample starts
        hours after the text forecast starts saying 'Rain And Snow'.
        """
        _run_hourly_and_griddata(station, "soda_springs", monkeypatch)
        rain_and_snow_hours = [h for h in station.hourly.values() if "Rain And Snow" in (h.forecast or "")]
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
        rain_only_hours = [h for h in station.hourly.values() if h.forecast == "Chance Rain"]
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
    """Unit tests for _apply_snow_hint keyword matching without network calls.

    These exercise the hint logic directly via synthetic Hour objects, verifying
    each keyword tier and edge cases.
    """

    def _make_hour(self, forecast):
        h = Hour()
        h.snow_fraction = 0.0
        h.forecast = forecast
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
        _apply_snow_hint(h)
        assert h.snow_fraction == expected, (
            f"{forecast!r}: expected {expected}, got {h.snow_fraction}"
        )

    def test_compound_snow_sleet_takes_max(self):
        """'Snow/Sleet' matches both 'Snow' (0.3) and 'Sleet' (0.5); max wins."""
        h = self._make_hour("Snow/Sleet")
        _apply_snow_hint(h)
        assert h.snow_fraction == 0.5

    def test_blowing_snow_gets_snow_tier(self):
        """'Blowing Snow' is an NWS obstruction (wind-blown existing snow), not
        falling precip, but it contains 'Snow' so it picks up the 0.3 hint.
        For a display panel this is acceptable — there is clearly snow present.
        """
        h = self._make_hour("Blowing Snow")
        _apply_snow_hint(h)
        assert h.snow_fraction == 0.3

    def test_no_keyword_no_hint(self):
        """Forecasts with no frozen-precip keywords should not be modified."""
        for forecast in ("Partly Cloudy", "Chance Rain", "Rain", "Sunny", "Fog", "Thunderstorm"):
            h = self._make_hour(forecast)
            _apply_snow_hint(h)
            assert h.snow_fraction == 0.0, f"{forecast!r} should not receive a snow hint"

    def test_existing_nonzero_fraction_not_overwritten(self):
        """The hint loop only fires when snow_fraction == 0.0; existing values are preserved."""
        h = self._make_hour("Rain And Snow Likely")
        h.snow_fraction = 0.8  # already set by griddata
        _apply_snow_hint(h)
        assert h.snow_fraction == 0.8, "Non-zero snow_fraction should not be overwritten by hint"

    def test_none_forecast_no_crash(self):
        """An Hour with forecast=None should not raise and should stay at 0.0."""
        h = self._make_hour(None)
        _apply_snow_hint(h)
        assert h.snow_fraction == 0.0


# ---------------------------------------------------------------------------
# Yosemite: all-snow
# ---------------------------------------------------------------------------

class TestYosemiteSnow:
    """Yosemite high country: simpler all-snow progression."""

    def test_has_snow_fraction_after_griddata(self, station, monkeypatch):
        _run_hourly_and_griddata(station, "yosemite", monkeypatch)
        snow_hours = [h for h in station.hourly.values() if h.snow_fraction and h.snow_fraction > 0]
        assert len(snow_hours) > 0, "Yosemite sample should have hours with snow"


# ---------------------------------------------------------------------------
# Phoenix: dry, zero precip baseline
# ---------------------------------------------------------------------------

class TestPhoenixDry:
    """Phoenix AZ: hot, dry — should have zero snow and minimal precip."""

    def test_no_snow(self, station, monkeypatch):
        _run_hourly_and_griddata(station, "phoenix", monkeypatch)
        for h in station.hourly.values():
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
        qpf_keys = {key for key, _ in _iter_time_series(qpf_values)}
        snow_keys = {key for key, _ in _iter_time_series(snow_values)}
        griddata_keys = qpf_keys | snow_keys

        periods = hourly_data["properties"]["periods"][:FORECAST_HOURS]
        matched = 0
        for p in periods:
            utc_key = _parse_utc_key(p["startTime"])
            if utc_key in griddata_keys:
                matched += 1

        assert matched > 30, (
            f"Only {matched}/{FORECAST_HOURS} hourly UTC keys matched griddata — "
            "likely a timezone conversion bug"
        )

    def test_snow_hours_have_matching_griddata(self, station, monkeypatch):
        """Hours marked 'Heavy Snow' in the forecast should have matching
        griddata keys with non-zero snowfallAmount."""
        hourly_data = _load("soda_springs_hourly.json")
        griddata_data = _load("soda_springs_griddata.json")

        snow_values = griddata_data["properties"]["snowfallAmount"]["values"]
        snow_by_hour = {}
        for key, val in _iter_time_series(snow_values):
            snow_by_hour.setdefault(key, val)

        snow_forecast_hours = [
            p for p in hourly_data["properties"]["periods"][:FORECAST_HOURS]
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
        monkeypatch.setattr(network, "get_stream", make_hourly_stream(f"{name}_hourly.json"))
        count = station.get_hourly_forecast()
        assert 0 < count <= FORECAST_HOURS, (
            f"Expected 1–{FORECAST_HOURS} periods, got {count}"
        )
        assert len(station.hourly) == count

    def test_griddata_populates_snow_fraction(self, station, monkeypatch, name):
        _run_hourly_and_griddata(station, name, monkeypatch)
        for h in station.hourly.values():
            assert h.snow_fraction is not None
            assert 0.0 <= h.snow_fraction <= 1.0


# ---------------------------------------------------------------------------
# Historical baseline parsing
# ---------------------------------------------------------------------------

HISTORICAL_SAMPLES = [p.stem.replace("_historical", "")
                      for p in sorted(SAMPLE_DIR.glob("*_historical.json"))]

HISTORICAL_LATLONS = {
    "albuquerque":        ("35.09",   "-106.65"),
    "anchorage_ak":       ("61.22",   "-149.90"),
    "austin":             ("30.27",   "-97.74"),
    "boston":              ("42.36",   "-71.06"),
    "cape_flattery_wa":   ("48.39",   "-124.72"),
    "chicago_il":         ("41.88",   "-87.63"),
    "dallas":             ("32.78",   "-96.80"),
    "death_valley_ca":    ("36.46",   "-116.87"),
    "denver_co":          ("39.74",   "-104.98"),
    "elkhart":            ("41.68",   "-85.97"),
    "eugene_or":          ("44.05",   "-123.09"),
    "evanston_il":        ("42.05",   "-87.68"),
    "everglades":         ("25.42",   "-80.89"),
    "fargo":              ("46.87",   "-96.79"),
    "franklin_county_ms": ("31.47",   "-90.91"),
    "grand_junction":     ("39.06",   "-108.55"),
    "honolulu":           ("21.31",   "-157.86"),
    "jefferson_wi":       ("43.00",   "-88.80"),
    "ketchikan_ak":       ("55.34",   "-131.65"),
    "lebanon_ks":         ("39.8283", "-98.5795"),
    "miami_fl":           ("25.77",   "-80.19"),
    "mt_washington_nh":   ("44.27",   "-71.30"),
    "key_west_fl":        ("24.5551", "-81.8039"),
    "new_orleans_la":     ("29.95",   "-90.07"),
    "oklahoma_city_ok":   ("35.47",   "-97.52"),
    "phoenix":            ("33.45",   "-112.07"),
    "tucson_az":          ("32.22",   "-110.97"),
    "san_antonio":        ("29.42",   "-98.49"),
    "seattle":            ("47.61",   "-122.33"),
    "soda_springs":       ("39.32",   "-120.38"),
    "somerville":         ("42.39",   "-71.10"),
    "somerville_may2026":  ("42.39",   "-71.10"),
    "somerville_may2026b": ("42.39",   "-71.10"),
    "yosemite":           ("37.86",   "-119.54"),
    "gardner_ma":         ("42.5751", "-71.998"),
    "hillsborough_nh":    ("43.1145", "-71.902"),
    "prinsburg_mn":       ("45.0724", "-95.1855"),
}


@pytest.fixture
def hist_station():
    config = {
        "GRIDPOINT_API": "https://test/points",
        "HISTORICAL_API": "https://test/historical",
    }
    s = Station(config)
    s.station_id = "TEST"
    return s


@pytest.mark.parametrize("name", HISTORICAL_SAMPLES)
class TestHistoricalParsing:
    """Replay recorded RCC ACIS responses through get_historical_day().

    Every location with a *_historical.json fixture gets run through the
    real code path — the same path the device takes regardless of whether
    ACIS returns data or an empty body.  Locations where ACIS has no
    coverage (e.g. Alaska) store null in their fixture; the correct device
    behavior is get_historical_day returning None, which is what we assert.
    """

    def _setup(self, hist_station, monkeypatch, name):
        """Load fixture and configure station — shared by all tests."""
        hist_data = _load(f"{name}_historical.json")
        lat, lon = HISTORICAL_LATLONS[name]
        hist_station.lat = lat
        hist_station.lon = lon
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: hist_data)
        return hist_data

    def test_parses_without_error(self, hist_station, monkeypatch, name):
        """get_historical_day completes without raising for every fixture."""
        hist_data = self._setup(hist_station, monkeypatch, name)

        result = hist_station.get_historical_day(0, "2026-04-21")
        if hist_data is None:
            assert result is None
            assert hist_station.historical[0] is None
        else:
            assert result is not None
            slot = hist_station.historical[0]
            assert slot is not None
            for key in ("low", "ave-low", "high", "ave-high", "date"):
                assert key in slot

    def test_date_stored_in_slot(self, hist_station, monkeypatch, name):
        """Each slot stores the correct calendar date offset from today.

        When the API returns no data, all slots stay None."""
        hist_data = self._setup(hist_station, monkeypatch, name)

        hist_station.get_historical_day(0, "2026-04-21")

        if hist_data is None:
            assert hist_station.historical[0] is None
        else:
            assert hist_station.historical[0]['date'] == "2026-04-21"

            hist_station.get_historical_day(1, "2026-04-21")
            assert hist_station.historical[1]['date'] == "2026-04-22"

            hist_station.get_historical_day(2, "2026-04-21")
            assert hist_station.historical[2]['date'] == "2026-04-23"

            hist_station.get_historical_day(3, "2026-04-21")
            assert hist_station.historical[3]['date'] == "2026-04-24"

    def test_values_match_captured_data(self, hist_station, monkeypatch, name):
        """Regression guard: parsed values should match what is in the JSON.

        For null fixtures, the slot should be None."""
        hist_data = self._setup(hist_station, monkeypatch, name)

        hist_station.get_historical_day(0, "2026-04-21")
        slot = hist_station.historical[0]

        if hist_data is None:
            assert slot is None
        else:
            smry = hist_data["smry"]
            assert slot["low"]      == float(smry[0])
            assert slot["ave-low"]  == float(smry[1])
            assert slot["high"]     == float(smry[2])
            assert slot["ave-high"] == float(smry[3])

    def test_other_slots_untouched(self, hist_station, monkeypatch, name):
        """Fetching one slot must not alter the other three — whether the
        fetch returns data or None."""
        self._setup(hist_station, monkeypatch, name)

        hist_station.get_historical_day(1, "2026-04-21")
        assert hist_station.historical[0] is None
        assert hist_station.historical[2] is None
        assert hist_station.historical[3] is None


# ---------------------------------------------------------------------------
# Historical buffer rotation
# ---------------------------------------------------------------------------

class TestHistoricalRotation:
    """rotate_historical() shifts slots left at midnight and clears stale data."""

    def _make_slot(self, date):
        return {'date': date, 'low': 30.0, 'ave-low': 40.0, 'ave-high': 60.0, 'high': 70.0}

    def test_no_rotation_when_today_matches_slot0(self, hist_station):
        """Buffer stays unchanged when slot 0 already holds today."""
        s0 = self._make_slot("2026-04-21")
        s1 = self._make_slot("2026-04-22")
        s2 = self._make_slot("2026-04-23")
        s3 = self._make_slot("2026-04-24")
        hist_station.historical = [s0, s1, s2, s3]
        hist_station.rotate_historical("2026-04-21")
        assert hist_station.historical[0] is s0
        assert hist_station.historical[1] is s1
        assert hist_station.historical[2] is s2
        assert hist_station.historical[3] is s3

    def test_normal_rotation_shifts_left(self, hist_station):
        """Normal midnight advance: slots shift left, slot 3 cleared."""
        s0 = self._make_slot("2026-04-21")
        s1 = self._make_slot("2026-04-22")
        s2 = self._make_slot("2026-04-23")
        s3 = self._make_slot("2026-04-24")
        hist_station.historical = [s0, s1, s2, s3]
        hist_station.rotate_historical("2026-04-22")
        assert hist_station.historical[0] is s1
        assert hist_station.historical[1] is s2
        assert hist_station.historical[2] is s3
        assert hist_station.historical[3] is None

    def test_stale_buffer_clears_all(self, hist_station):
        """If device was off multiple days, all slots are cleared."""
        s0 = self._make_slot("2026-04-21")
        s1 = self._make_slot("2026-04-22")
        s2 = self._make_slot("2026-04-23")
        s3 = self._make_slot("2026-04-24")
        hist_station.historical = [s0, s1, s2, s3]
        hist_station.rotate_historical("2026-04-25")
        assert hist_station.historical == [None, None, None, None]

    def test_rotation_with_none_slot0(self, hist_station):
        """rotate_historical() is a no-op when slot 0 is None (not yet fetched)."""
        hist_station.historical = [None, None, None, None]
        hist_station.rotate_historical("2026-04-22")
        assert hist_station.historical == [None, None, None, None]

    def test_rotation_with_partially_filled_buffer(self, hist_station):
        """Rotation works correctly when slots 2 and 3 were still None."""
        s0 = self._make_slot("2026-04-21")
        s1 = self._make_slot("2026-04-22")
        hist_station.historical = [s0, s1, None, None]
        hist_station.rotate_historical("2026-04-22")
        assert hist_station.historical[0] is s1
        assert hist_station.historical[1] is None
        assert hist_station.historical[2] is None
        assert hist_station.historical[3] is None

    def test_double_rotation_second_day_ok(self, hist_station):
        """Two consecutive midnight rotations: slot 0 = s2, slot 1 = s3, slots 2 and 3 None."""
        s0 = self._make_slot("2026-04-21")
        s1 = self._make_slot("2026-04-22")
        s2 = self._make_slot("2026-04-23")
        s3 = self._make_slot("2026-04-24")
        hist_station.historical = [s0, s1, s2, s3]
        hist_station.rotate_historical("2026-04-22")
        hist_station.rotate_historical("2026-04-23")
        assert hist_station.historical[0]['date'] == "2026-04-23"
        assert hist_station.historical[1] is s3
        assert hist_station.historical[2] is None
        assert hist_station.historical[3] is None


class TestNullProbabilityOfPrecipitation:
    """get_hourly_forecast() must not crash when probabilityOfPrecipitation.value is null.

    The NWS API defines this field as a nullable number. A null value previously
    propagated to h.precipitation = None, which then crashed the f'{None:3}'
    format spec with TypeError: unsupported format string passed to NoneType.__format__.
    """

    def _make_hourly_with_null_pop(self):
        """Return a minimal hourly JSON payload where one period has null PoP."""
        import copy
        data = copy.deepcopy(_load("boston_hourly.json"))
        data["properties"]["periods"][0]["probabilityOfPrecipitation"]["value"] = None
        return data

    def test_null_pop_does_not_crash(self, station, monkeypatch):
        """get_hourly_forecast() completes without raising when PoP value is null."""
        monkeypatch.setattr(network, "get_stream", _dict_to_stream(self._make_hourly_with_null_pop()))
        count = station.get_hourly_forecast()
        assert 0 < count <= FORECAST_HOURS

    def test_null_pop_becomes_zero(self, station, monkeypatch):
        """A null PoP value should be treated as 0 — not None — on the Hour object."""
        monkeypatch.setattr(network, "get_stream", _dict_to_stream(self._make_hourly_with_null_pop()))
        station.get_hourly_forecast()
        assert next(iter(station.hourly.values())).precipitation == 0, (
            "Null probabilityOfPrecipitation.value should become 0, not None"
        )


# ---------------------------------------------------------------------------
# get_hourly_forecast() — hourly_expires cache header
# ---------------------------------------------------------------------------

class TestHourlyExpires:
    """get_hourly_forecast() reads Cache-Control max-age and sets hourly_expires."""

    def test_sets_hourly_expires_when_cache_control_present(self, station, monkeypatch):
        """When the response includes Cache-Control: max-age, hourly_expires is set
        to a future epoch (current time + max_age)."""
        fake_now = 1_000_000.0
        monkeypatch.setattr(network, "get_stream", make_hourly_stream(
            "soda_springs_hourly.json",
            response_headers={'cache-control': 'public, max-age=3600, s-maxage=3600'},
        ))
        monkeypatch.setattr("station._time", lambda: fake_now)
        station.get_hourly_forecast()

        assert station.hourly_expires == fake_now + 3600

    def test_clamps_short_max_age_to_one_hour(self, station, monkeypatch):
        """A max-age shorter than FORECAST_MIN_CACHE_MINUTES (60 min) is clamped up to 60 minutes.

        This prevents the device from re-fetching more often than once per hour
        even when NOAA returns an unexpectedly short cache window."""
        fake_now = 1_000_000.0
        monkeypatch.setattr(network, "get_stream", make_hourly_stream(
            "soda_springs_hourly.json",
            response_headers={'cache-control': 'public, max-age=300'},
        ))
        monkeypatch.setattr("station._time", lambda: fake_now)
        station.get_hourly_forecast()

        assert station.hourly_expires == fake_now + 3600


# ---------------------------------------------------------------------------
# get_griddata() — griddata_expires cache header
# ---------------------------------------------------------------------------

_MINIMAL_GRIDDATA = {
    "properties": {
        "updateTime": "2026-05-15T10:00:00+00:00",
        "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": []},
        "snowfallAmount":            {"uom": "wmoUnit:mm", "values": []},
    }
}


class TestGriddataExpires:
    """get_griddata() reads Cache-Control max-age and sets griddata_expires."""

    def test_sets_griddata_expires_when_cache_control_present(self, station, monkeypatch):
        """When the response includes Cache-Control: max-age, griddata_expires is set
        to a future epoch (current time + max_age)."""
        fake_now = 1_000_000.0

        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("soda_springs_hourly.json"),
            _dict_to_stream(_MINIMAL_GRIDDATA,
                            response_headers={'cache-control': 'public, max-age=3600, s-maxage=3600'}),
        ))
        monkeypatch.setattr("station._time", lambda: fake_now)
        station.get_hourly_forecast()
        station.get_griddata()

        assert station.griddata_expires == fake_now + 3600

    def test_griddata_expires_not_set_when_request_fails(self, station, monkeypatch):
        """A failed stream (returns None from __enter__) must not update griddata_expires."""
        station.griddata_expires = 9_999_999.0   # pre-set to something

        class _FailedStream:
            headers = {}
            def __enter__(self): return None
            def __exit__(self, *args): return False

        def _hourly_or_fail(url, req_headers=None, min_budget_s=None):
            if 'griddata' in url or 'grid' in url.lower():
                return _FailedStream()
            return make_hourly_stream("soda_springs_hourly.json")(url, req_headers)

        monkeypatch.setattr(network, "get_stream", _hourly_or_fail)
        station.get_hourly_forecast()
        station.get_griddata()

        assert station.griddata_expires == 9_999_999.0

    def test_clamps_short_max_age_to_one_hour(self, station, monkeypatch):
        """A max-age shorter than FORECAST_MIN_CACHE_MINUTES (60 min) is clamped up to 60 minutes.

        This prevents the device from re-fetching more often than once per hour
        even when NOAA returns an unexpectedly short cache window."""
        fake_now = 1_000_000.0

        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("soda_springs_hourly.json"),
            _dict_to_stream(_MINIMAL_GRIDDATA,
                            response_headers={'cache-control': 'public, max-age=300'}),
        ))
        monkeypatch.setattr("station._time", lambda: fake_now)
        station.get_hourly_forecast()
        station.get_griddata()

        assert station.griddata_expires == fake_now + 3600


class TestGriddataMissingUom:
    """get_griddata() must not crash when a series omits 'uom'.

    The NWS API docs state that 'uom' is only present for series that have
    values. When snowfallAmount returns {"values": []} — documented behavior
    for warm-weather windows with no snow — 'uom' is absent. The streaming
    implementation skips uom entirely, so this is a no-op; the tests remain
    as a regression guard against reintroducing a uom access that could crash.
    """

    def test_snowfall_amount_missing_uom_does_not_crash(self, station, monkeypatch):
        """get_griddata() completes without raising when snowfallAmount has no uom."""
        griddata_data = {
            "properties": {
                "updateTime": "2026-05-06T18:00:00+00:00",
                "quantitativePrecipitation": {
                    "uom": "wmoUnit:mm",
                    "values": [],
                },
                "snowfallAmount": {
                    "values": [],
                },
            }
        }

        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            _dict_to_stream(griddata_data),
        ))
        station.get_hourly_forecast()
        station.get_griddata()

        assert all(h.snow_fraction == 0.0 for h in station.hourly.values()), (
            "All hours should have snow_fraction == 0.0 when snowfallAmount has no values"
        )

    def test_qpf_missing_uom_does_not_crash(self, station, monkeypatch):
        """get_griddata() completes without raising when quantitativePrecipitation has no uom."""
        griddata_data = {
            "properties": {
                "updateTime": "2026-05-06T18:00:00+00:00",
                "quantitativePrecipitation": {
                    "values": [],
                },
                "snowfallAmount": {
                    "values": [],
                },
            }
        }

        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            _dict_to_stream(griddata_data),
        ))
        station.get_hourly_forecast()
        station.get_griddata()

        assert all(h.snow_fraction == 0.0 for h in station.hourly.values()), (
            "All hours should have snow_fraction == 0.0 when QPF has no values"
        )


class TestGriddataMissingSeriesKey:
    """get_griddata() must not crash when quantitativePrecipitation or snowfallAmount
    is absent entirely from the griddata properties object.

    Both series are optional in the NWS GridpointForecast schema. When absent,
    the method should treat them as empty (no precipitation data) and continue.
    """

    def _make_griddata(self, *, include_qpf=True, include_snow=True):
        props = {"updateTime": "2026-05-06T18:00:00+00:00"}
        if include_qpf:
            props["quantitativePrecipitation"] = {"uom": "wmoUnit:mm", "values": []}
        if include_snow:
            props["snowfallAmount"] = {"uom": "wmoUnit:mm", "values": []}
        return {"properties": props}

    def _run(self, station, monkeypatch, griddata_data):
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            _dict_to_stream(griddata_data),
        ))
        station.get_hourly_forecast()
        station.get_griddata()

    def test_missing_qpf_does_not_crash(self, station, monkeypatch):
        """get_griddata() completes without raising when quantitativePrecipitation is absent."""
        self._run(station, monkeypatch, self._make_griddata(include_qpf=False))

    def test_missing_snow_does_not_crash(self, station, monkeypatch):
        """get_griddata() completes without raising when snowfallAmount is absent."""
        self._run(station, monkeypatch, self._make_griddata(include_snow=False))

    def test_missing_both_does_not_crash(self, station, monkeypatch):
        """get_griddata() completes without raising when both series are absent."""
        self._run(station, monkeypatch, self._make_griddata(include_qpf=False, include_snow=False))

    def test_missing_qpf_all_snow_fraction_zero(self, station, monkeypatch):
        """All hours should have snow_fraction == 0.0 when QPF series is absent."""
        self._run(station, monkeypatch, self._make_griddata(include_qpf=False))
        assert all(h.snow_fraction == 0.0 for h in station.hourly.values()), (
            "All hours should have snow_fraction == 0.0 when quantitativePrecipitation is absent"
        )

    def test_missing_snow_all_snow_fraction_zero(self, station, monkeypatch):
        """All hours should have snow_fraction == 0.0 when snowfallAmount series is absent."""
        self._run(station, monkeypatch, self._make_griddata(include_snow=False))
        assert all(h.snow_fraction == 0.0 for h in station.hourly.values()), (
            "All hours should have snow_fraction == 0.0 when snowfallAmount is absent"
        )



class TestGriddataMetadataPrint:
    """get_griddata() prints a single summary line for the known NOAA metadata fields.

    elevation, forecastOffice, gridId, gridX, and gridY appear in every
    GridpointForecast response before the time-series properties. They are
    consumed from the stream and printed as one human-readable line.
    """

    def _make_griddata(self):
        return {
            "properties": {
                "updateTime": "2026-05-17T18:48:24+00:00",
                "validTimes": "2026-05-17T12:00:00+00:00/P7DT13H",
                "elevation": {"unitCode": "wmoUnit:m", "value": 14.0208},
                "forecastOffice": "https://api.weather.gov/offices/BOX",
                "gridId": "BOX",
                "gridX": 70,
                "gridY": 102,
                "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": []},
                "snowfallAmount": {"uom": "wmoUnit:mm", "values": []},
            }
        }

    def _run(self, station, monkeypatch):
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            _dict_to_stream(self._make_griddata()),
        ))
        station.get_hourly_forecast()
        station.get_griddata()

    def test_metadata_summary_line_present(self, station, monkeypatch, capsys):
        """get_griddata() prints a metadata summary line containing grid ID and coordinates."""
        self._run(station, monkeypatch)
        out = capsys.readouterr().out
        assert "  Grid: BOX (70,102)  Elevation: 14.0 m" in out, (
            f"Expected metadata summary line not found in output:\n{out}"
        )

    def test_no_skipping_lines_for_metadata(self, station, monkeypatch, capsys):
        """No 'Skipping' message is emitted for any of the five metadata fields."""
        self._run(station, monkeypatch)
        out = capsys.readouterr().out
        skipping = [ln for ln in out.splitlines() if "Skipping" in ln
                    and any(f in ln for f in
                            ("elevation", "forecastOffice", "gridId", "gridX", "gridY"))]
        assert skipping == [], f"Unexpected Skipping lines: {skipping}"

    def test_metadata_absent_no_crash(self, station, monkeypatch):
        """get_griddata() does not crash when metadata fields are absent from the response."""
        minimal = {
            "properties": {
                "updateTime": "2026-05-17T18:48:24+00:00",
                "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": []},
                "snowfallAmount": {"uom": "wmoUnit:mm", "values": []},
            }
        }
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            _dict_to_stream(minimal),
        ))
        station.get_hourly_forecast()
        station.get_griddata()  # must not raise


class TestQpfPreservationAcrossHourlyRefresh:
    """qpf_mm survives a get_hourly_forecast() call when griddata is not re-fetched.

    With separate _hourly_store and _griddata_store, griddata values are never
    overwritten by an hourly-only fetch — preservation is now structural rather
    than a save/restore hack.  These tests verify the end-to-end behavior via
    station.hourly remains correct.
    """

    def test_qpf_mm_is_set_after_griddata(self, station, monkeypatch):
        """Baseline: after get_griddata(), every hour has a float qpf_mm (not None)."""
        _run_hourly_and_griddata(station, "boston", monkeypatch)
        assert all(h.qpf_mm is not None for h in station.hourly.values()), (
            "Every hour should have a float qpf_mm after get_griddata()"
        )

    def test_qpf_mm_preserved_after_second_hourly_fetch(self, station, monkeypatch):
        """After a griddata fetch followed by a second hourly-only fetch, qpf_mm
        must still be non-None — not wiped back to the Hour.__init__ default."""
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            make_griddata_stream("boston_griddata.json"),
        ))

        station.get_hourly_forecast()
        station.get_griddata()

        # Simulate a subsequent 5-minute hourly refresh with no griddata fetch.
        station.get_hourly_forecast()

        assert all(h.qpf_mm is not None for h in station.hourly.values()), (
            "qpf_mm should be preserved across a hourly-only refresh — "
            "griddata is stale for up to 20 minutes between fetches"
        )

    def test_qpf_mm_values_match_after_second_hourly_fetch(self, station, monkeypatch):
        """The preserved qpf_mm values should equal the originals from get_griddata()."""
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            make_griddata_stream("boston_griddata.json"),
        ))

        station.get_hourly_forecast()
        station.get_griddata()

        original_qpf = {h.start: h.qpf_mm for h in station.hourly.values()}

        station.get_hourly_forecast()

        for h in station.hourly.values():
            if h.start in original_qpf:
                assert h.qpf_mm == original_qpf[h.start], (
                    f"qpf_mm mismatch at {h.start}: "
                    f"expected {original_qpf[h.start]}, got {h.qpf_mm}"
                )


class TestHistoricalFailure:
    """get_historical_day() should handle failures gracefully."""

    def test_post_returns_none(self, hist_station, monkeypatch):
        hist_station.lat = "42.36"
        hist_station.lon = "-71.06"
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: None)

        result = hist_station.get_historical_day(0, "2026-04-21")
        assert result is None
        assert hist_station.historical[0] is None

    def test_missing_smry_key(self, hist_station, monkeypatch):
        hist_station.lat = "42.36"
        hist_station.lon = "-71.06"
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"other": "data"})

        result = hist_station.get_historical_day(0, "2026-04-21")
        assert result is None
        assert hist_station.historical[0] is None

    def test_no_lat_lon(self, hist_station, monkeypatch):
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [1, 2, 3, 4]})

        result = hist_station.get_historical_day(0, "2026-04-21")
        assert result is None

    def test_failure_leaves_other_slots_intact(self, hist_station, monkeypatch):
        """A failed fetch for one slot must not disturb already-filled slots."""
        hist_station.lat = "42.36"
        hist_station.lon = "-71.06"
        existing = {'date': '2026-04-22', 'low': 30.0, 'ave-low': 40.0,
                    'ave-high': 60.0, 'high': 70.0}
        hist_station.historical[1] = existing
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: None)

        hist_station.get_historical_day(0, "2026-04-21")
        assert hist_station.historical[1] is existing


# ---------------------------------------------------------------------------
# HISTORY_YEARS config — sdate calculation
# ---------------------------------------------------------------------------

class TestHistoryYearsConfig:
    """Station.history_years controls the ACIS query date range.

    The sdate sent to ACIS must be ``history_years`` years before the anchor
    year (which is target_date+1). These tests capture the querydata dict
    passed to network.request and check the sdate field directly.
    """

    _FAKE_RESPONSE = {"smry": ["30.0", "40.0", "70.0", "60.0"]}

    def _make_station(self, extra_config=None):
        config = {
            "GRIDPOINT_API":   "https://test/points",
            "HISTORICAL_API":  "https://test/historical",
        }
        if extra_config:
            config.update(extra_config)
        s = Station(config)
        s.lat = "42.36"
        s.lon = "-71.06"
        return s

    def _capture_sdate(self, station, monkeypatch, today):
        """Call get_historical_day(0, today), return the sdate that was posted."""
        posted = {}
        monkeypatch.setattr(
            network, "request",
            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: posted.update(body) or self._FAKE_RESPONSE,
        )
        station.get_historical_day(0, today)
        return posted.get("sdate")

    def test_default_is_10_years(self, monkeypatch):
        """When HISTORY_YEARS is absent from config, sdate is 10 years before the anchor."""
        s = self._make_station()
        assert s.history_years == 10
        sdate = self._capture_sdate(s, monkeypatch, "2026-04-21")
        # anchor = 2026-04-22; sdate year = 2026-04-22 - 10 = 2016
        assert sdate is not None
        assert sdate.startswith("2016-")

    def test_custom_years_from_int_config(self, monkeypatch):
        """HISTORY_YEARS = 5 (integer) produces an sdate 5 years before the anchor."""
        s = self._make_station({"HISTORY_YEARS": 5})
        assert s.history_years == 5
        sdate = self._capture_sdate(s, monkeypatch, "2026-04-21")
        assert sdate is not None
        assert sdate.startswith("2021-")

    def test_custom_years_from_string_config(self, monkeypatch):
        """HISTORY_YEARS = '15' (string, as saved by the portal) is coerced to int."""
        s = self._make_station({"HISTORY_YEARS": "15"})
        assert s.history_years == 15
        sdate = self._capture_sdate(s, monkeypatch, "2026-04-21")
        assert sdate is not None
        assert sdate.startswith("2011-")

    def test_sdate_month_and_day_match_anchor(self, monkeypatch):
        """sdate month/day come from the anchor (target+1), not today."""
        s = self._make_station({"HISTORY_YEARS": 10})
        sdate = self._capture_sdate(s, monkeypatch, "2026-04-21")
        # anchor = 2026-04-22; sdate = 2016-04-22
        assert sdate == "2016-04-22"


# ---------------------------------------------------------------------------
# New-location smoke tests — 2026-05-08 live fixtures
# ---------------------------------------------------------------------------

_NEW_LOCATIONS = [
    "anchorage_ak",
    "cape_flattery_wa",
    "chicago_il",
    "death_valley_ca",
    "denver_co",
    "eugene_or",
    "evanston_il",
    "franklin_county_ms",
    "ketchikan_ak",
    "lebanon_ks",
    "key_west_fl",
    "miami_fl",
    "mt_washington_nh",
    "new_orleans_la",
    "oklahoma_city_ok",
]


def _run_full_pipeline(name, monkeypatch, station):
    """Parse hourly + griddata + historical for a new-location fixture."""
    hist_data = _load(f"{name}_historical.json")  # may be None (Alaska)

    monkeypatch.setattr(network, "get_stream", make_stream_router(
        make_hourly_stream(f"{name}_hourly.json"),
        make_griddata_stream(f"{name}_griddata.json"),
    ))
    monkeypatch.setattr(network, "request",
        lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: hist_data)

    station.lat = "0.0"  # non-empty so get_historical_day proceeds
    station.lon = "0.0"
    station.get_hourly_forecast()
    station.get_griddata()

    today = next(iter(station.hourly.values())).start[:10]
    station.get_historical_day(0, today)
    return station


class TestNewLocationSmoke:
    """Parametrized smoke tests for all 14 new live-fixture locations.

    Verifies that the full pipeline — hourly parsing, griddata snow-fraction
    population, and historical baseline fetch — runs without error and
    produces well-formed output for each location.  For Alaska locations
    (anchorage_ak, ketchikan_ak) the historical fixture contains null, so
    get_historical_day returns None without raising — the same graceful
    degradation that occurs on the live device.
    """

    @pytest.mark.parametrize("location", _NEW_LOCATIONS)
    def test_all_hours_have_required_fields(self, station, monkeypatch, location):
        """Every Hour object has non-None temperature, precipitation, start, and end."""
        monkeypatch.setattr(network, "get_stream", make_hourly_stream(f"{location}_hourly.json"))
        station.get_hourly_forecast()
        for h in station.hourly.values():
            assert h.temperature  is not None, f"{location}: hour {h.start} missing temperature"
            assert h.precipitation is not None, f"{location}: hour {h.start} missing precipitation"
            assert h.start        is not None, f"{location}: missing start"
            assert h.end          is not None, f"{location}: missing end"

    @pytest.mark.parametrize("location", _NEW_LOCATIONS)
    def test_historical_does_not_raise(self, station, monkeypatch, location):
        """get_historical_day() returns a valid slot dict or None — never raises."""
        s = _run_full_pipeline(location, monkeypatch, station)
        slot = s.historical[0]
        if slot is not None:
            assert "low"      in slot
            assert "ave-low"  in slot
            assert "high"     in slot
            assert "ave-high" in slot
            assert "date"     in slot


class TestNewLocationScenarios:
    """Scenario-specific assertions for notable new locations."""

    def test_mt_washington_nh_has_snow_fraction(self, station, monkeypatch):
        """Mt. Washington has snow showers in the forecast — some hours should
        have snow_fraction > 0 even in May."""
        _run_full_pipeline("mt_washington_nh", monkeypatch, station)
        snow_hours = [h for h in station.hourly.values() if h.snow_fraction > 0]
        assert len(snow_hours) > 0, (
            "Mt. Washington forecast includes snow showers — expected snow_fraction > 0 for some hours"
        )

    def test_ketchikan_ak_all_rain_no_snow_fraction(self, station, monkeypatch):
        """Ketchikan is 48–52°F with only rain in the forecast — snow_fraction
        should be 0.0 throughout."""
        _run_full_pipeline("ketchikan_ak", monkeypatch, station)
        for h in station.hourly.values():
            assert h.snow_fraction == 0.0, (
                f"Ketchikan hour {h.start} ({h.forecast!r}): "
                f"expected snow_fraction == 0.0, got {h.snow_fraction}"
            )

    def test_cape_flattery_wa_zero_precip_zero_snow(self, station, monkeypatch):
        """Cape Flattery has near-zero precipitation and no frozen precip —
        snow_fraction should be 0.0 for all hours."""
        _run_full_pipeline("cape_flattery_wa", monkeypatch, station)
        for h in station.hourly.values():
            assert h.snow_fraction == 0.0, (
                f"Cape Flattery hour {h.start} ({h.forecast!r}): "
                f"expected snow_fraction == 0.0, got {h.snow_fraction}"
            )

    def test_eugene_or_zero_precip_zero_snow(self, station, monkeypatch):
        """Eugene has 0% precipitation all hours — snow_fraction should be 0.0."""
        _run_full_pipeline("eugene_or", monkeypatch, station)
        for h in station.hourly.values():
            assert h.snow_fraction == 0.0, (
                f"Eugene hour {h.start} ({h.forecast!r}): "
                f"expected snow_fraction == 0.0, got {h.snow_fraction}"
            )

    def test_anchorage_ak_historical_returns_none(self, station, monkeypatch):
        """Anchorage: ACIS returns empty body (stored as null in fixture) —
        historical slot should be None after get_historical_day()."""
        s = _run_full_pipeline("anchorage_ak", monkeypatch, station)
        assert s.historical[0] is None, (
            "Anchorage historical slot should be None — ACIS grid 21 has no Alaska coverage"
        )



# ---------------------------------------------------------------------------
# get_temp_range — ACIS all-time temperature range
# ---------------------------------------------------------------------------

class TestGetTempRange:
    def test_returns_tuple_on_success(self, station, monkeypatch):
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-10, 101]})
        result = station.get_temp_range()
        assert result == (-10, 101)

    def test_sets_temp_min_max_on_success(self, station, monkeypatch):
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-10, 101]})
        station.get_temp_range()
        assert station.temp_min == -10
        assert station.temp_max == 101

    def test_rounds_float_values(self, station, monkeypatch):
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-9.6, 100.4]})
        result = station.get_temp_range()
        assert result == (-10, 100)

    def test_returns_none_when_no_lat(self, station, monkeypatch):
        station.lat = None
        called = []
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: called.append(1) or {})
        result = station.get_temp_range()
        assert result is None
        assert not called

    def test_returns_none_when_no_lon(self, station, monkeypatch):
        station.lon = None
        called = []
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: called.append(1) or {})
        result = station.get_temp_range()
        assert result is None
        assert not called

    def test_returns_none_on_api_failure(self, station, monkeypatch):
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: None)
        result = station.get_temp_range()
        assert result is None

    def test_does_not_set_attrs_on_api_failure(self, station, monkeypatch):
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: None)
        station.get_temp_range()
        assert station.temp_min is None
        assert station.temp_max is None

    def test_returns_none_on_missing_smry_key(self, station, monkeypatch):
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"data": []})
        result = station.get_temp_range()
        assert result is None

    def test_returns_none_on_empty_smry(self, station, monkeypatch):
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": []})
        result = station.get_temp_range()
        assert result is None

    def test_returns_none_on_non_numeric_smry(self, station, monkeypatch):
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": ["bad", "data"]})
        result = station.get_temp_range()
        assert result is None

    def test_query_uses_historical_api(self, station, monkeypatch):
        posted_urls = []
        monkeypatch.setattr(
            network, "request",
            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: posted_urls.append(url) or {"smry": [-5, 100]},
        )
        station.get_temp_range()
        assert posted_urls == [station.historical_api]

    def test_query_includes_degreeF_units(self, station, monkeypatch):
        payloads = []
        monkeypatch.setattr(
            network, "request",
            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: payloads.append(body) or {"smry": [-5, 100]},
        )
        station.get_temp_range()
        assert payloads
        elems = payloads[0]["elems"]
        assert all(e.get("units") == "degreeF" for e in elems)

    def test_query_covers_mint_and_maxt(self, station, monkeypatch):
        payloads = []
        monkeypatch.setattr(
            network, "request",
            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: payloads.append(body) or {"smry": [-5, 100]},
        )
        station.get_temp_range()
        names = {e["name"] for e in payloads[0]["elems"]}
        assert "mint" in names
        assert "maxt" in names

    def test_returns_none_on_prism_sentinel_value(self, station, monkeypatch):
        """-999 is PRISM's missing-data sentinel; treat it as invalid."""
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-999, -999]})
        result = station.get_temp_range()
        assert result is None

    def test_does_not_set_attrs_on_sentinel(self, station, monkeypatch):
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-999, -999]})
        station.get_temp_range()
        assert station.temp_min is None
        assert station.temp_max is None

    def test_returns_none_on_absurd_low(self, station, monkeypatch):
        """Values below -150°F are non-physical — reject them."""
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-200, 100]})
        result = station.get_temp_range()
        assert result is None

    def test_returns_none_on_absurd_high(self, station, monkeypatch):
        """Values above 160°F are non-physical — reject them."""
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-10, 200]})
        result = station.get_temp_range()
        assert result is None

    def test_accepts_extreme_but_valid_values(self, station, monkeypatch):
        """US all-time extremes (~-80°F and 134°F) must pass the sanity check."""
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-80, 134]})
        result = station.get_temp_range()
        assert result == (-80, 134)

    def test_edate_is_three_days_before_today(self, station, monkeypatch):
        """edate must be 3 days before today to avoid PRISM processing lag."""
        fake_now = time.struct_time((2026, 5, 12, 14, 0, 0, 0, 132, 1))
        payloads = []
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: payloads.append(body) or {"smry": [-5, 100]})
        with patch("station.localtime", return_value=fake_now):
            station.get_temp_range()
        assert payloads[0]["edate"] == "2026-05-09"

    def test_returns_none_when_range_too_narrow(self, station, monkeypatch):
        """A span < 32°F (1°F/pixel) produces a compressed scale — reject it."""
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [50, 81]})  # span=31
        result = station.get_temp_range()
        assert result is None

    def test_accepts_exactly_32_degree_span(self, station, monkeypatch):
        """A span of exactly 32°F is the minimum allowed (1°F per display pixel)."""
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [50, 82]})  # span=32
        result = station.get_temp_range()
        assert result == (50, 82)

    # Fixture files are committed ACIS responses captured from the real API.

    # These tests exercise the full parse path (int rounding, sanity checks)
    # with the actual JSON format returned by ACIS — not synthetic payloads.
    @pytest.mark.parametrize("fixture,expected", [
        ("boston_temp_range.json",          (-10, 101)),
        ("chicago_il_temp_range.json",      (-26, 102)),
        ("death_valley_ca_temp_range.json",  (22, 129)),
        ("elkhart_temp_range.json",         (-22, 103)),
        ("eugene_or_temp_range.json",        (-1, 109)),
        ("fargo_temp_range.json",           (-35, 105)),
        ("key_west_fl_temp_range.json",      (42,  95)),
        ("mt_washington_nh_temp_range.json", (-38,  82)),
        ("phoenix_temp_range.json",          (27, 120)),
        ("tucson_az_temp_range.json",        (18, 115)),
    ])
    def test_parses_committed_acis_fixtures(self, station, monkeypatch, fixture, expected):
        """Each committed ACIS fixture must parse to the expected (min, max) pair."""
        data = _load(fixture)
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: data)
        assert station.get_temp_range() == expected


# ---------------------------------------------------------------------------
# get_temp_range — cascade and fallback date logic
# ---------------------------------------------------------------------------

class TestGetTempRangeCascade:
    """Tests for the three-edate cascade added to get_temp_range()."""

    def test_stops_on_first_success(self, station, monkeypatch):
        """If the first date range succeeds, no further requests are made."""
        calls = []
        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            calls.append(body['edate'])
            return {"smry": [-10, 100]}
        monkeypatch.setattr(network, "request", fake_request)
        with patch('station.localtime', return_value=time.struct_time((2026, 5, 12, 0, 0, 0, 0, 0, 0))):
            station.get_temp_range()
        assert len(calls) == 1
        assert calls[0] == "2026-05-09"   # today-3

    def test_falls_back_to_end_of_last_year(self, station, monkeypatch):
        """If today-3 fails, try {last_year}-12-31."""
        responses = iter([None, {"smry": [-10, 100]}])
        edates = []
        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            edates.append(body['edate'])
            return next(responses)
        monkeypatch.setattr(network, "request", fake_request)
        with patch('station.localtime', return_value=time.struct_time((2027, 3, 10, 0, 0, 0, 0, 0, 0))):
            result = station.get_temp_range()
        assert result == (-10, 100)
        assert edates[0] == "2027-03-07"   # today-3
        assert edates[1] == "2026-12-31"   # end of last year

    def test_falls_back_to_hardcoded_2025(self, station, monkeypatch):
        """If both dynamic dates fail, try the hardcoded 2025-12-31."""
        responses = iter([None, None, {"smry": [-10, 100]}])
        edates = []
        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            edates.append(body['edate'])
            return next(responses)
        monkeypatch.setattr(network, "request", fake_request)
        with patch('station.localtime', return_value=time.struct_time((2027, 3, 10, 0, 0, 0, 0, 0, 0))):
            result = station.get_temp_range()
        assert result == (-10, 100)
        assert edates[2] == "2025-12-31"

    def test_returns_none_when_all_attempts_fail(self, station, monkeypatch):
        """When every edate returns None, get_temp_range() returns None."""
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: None)
        with patch('station.localtime', return_value=time.struct_time((2027, 3, 10, 0, 0, 0, 0, 0, 0))):
            result = station.get_temp_range()
        assert result is None

    def test_deduplicates_edates_in_year_of_hardcoded_date(self, station, monkeypatch):
        """In 2026, {last_year}-12-31 == '2025-12-31', so only two requests are made."""
        calls = []
        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            calls.append(body['edate'])
        monkeypatch.setattr(network, "request", fake_request)
        with patch('station.localtime', return_value=time.struct_time((2026, 6, 1, 0, 0, 0, 0, 0, 0))):
            station.get_temp_range()
        assert len(calls) == 2
        assert "2025-12-31" in calls

    def test_sets_is_fallback_false_on_acis_success(self, station, monkeypatch):
        """A successful ACIS fetch clears the fallback flag."""
        station.temp_range_is_fallback = True
        monkeypatch.setattr(network, "request",
                            lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: {"smry": [-10, 100]})
        station.get_temp_range()
        assert station.temp_range_is_fallback is False

    def test_does_not_set_attrs_when_all_attempts_fail(self, station, monkeypatch):
        """temp_min and temp_max remain None when all attempts fail."""
        monkeypatch.setattr(network, "request", lambda verb, url, body=None, headers=None, out_headers=None, min_budget_s=None: None)
        station.get_temp_range()
        assert station.temp_min is None
        assert station.temp_max is None


# ---------------------------------------------------------------------------
# QPF/snow iteration bounds guards
# ---------------------------------------------------------------------------

class TestGriddataBoundsGuards:
    """get_griddata() skips QPF/snow entries outside the hourly window.

    Entries before first_key are skipped via continue; entries after last_key
    short-circuit the series loop via break. Neither should apply qpf_mm or
    snow_fraction to any Hour.
    """

    def _one_hour_before(self, key):
        """Return an ISO 8601 instant one hour before the given UTC hour key."""
        dt = datetime.fromisoformat(key + ":00:00+00:00")
        return (dt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00+00:00")

    def _one_hour_after(self, key):
        """Return an ISO 8601 instant one hour after the given UTC hour key."""
        dt = datetime.fromisoformat(key + ":00:00+00:00")
        return (dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00+00:00")

    def _griddata(self, qpf_values=None, snow_values=None):
        return {
            "properties": {
                "updateTime": "2026-05-15T10:00:00+00:00",
                "quantitativePrecipitation": {"values": qpf_values or []},
                "snowfallAmount":            {"values": snow_values or []},
            }
        }

    def test_qpf_entry_before_window_not_applied(self, station, monkeypatch):
        """A QPF entry whose hour falls before the first hourly key must not set qpf_mm."""
        monkeypatch.setattr(network, "get_stream", make_hourly_stream("boston_hourly.json"))
        station.get_hourly_forecast()

        first_key = next(iter(station.hourly))
        before = self._one_hour_before(first_key)

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._griddata(qpf_values=[{"validTime": f"{before}/PT1H", "value": 50.0}])
        ))
        station.get_griddata()

        assert all(h.qpf_mm == 0.0 for h in station.hourly.values()), (
            "A QPF entry before the hourly window should not set qpf_mm on any Hour"
        )

    def test_qpf_entry_after_window_not_applied(self, station, monkeypatch):
        """A QPF entry whose hour falls after the last hourly key must not set qpf_mm."""
        monkeypatch.setattr(network, "get_stream", make_hourly_stream("boston_hourly.json"))
        station.get_hourly_forecast()

        last_key = next(reversed(station.hourly))
        after = self._one_hour_after(last_key)

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._griddata(qpf_values=[{"validTime": f"{after}/PT1H", "value": 50.0}])
        ))
        station.get_griddata()

        assert all(h.qpf_mm == 0.0 for h in station.hourly.values()), (
            "A QPF entry after the hourly window should not set qpf_mm on any Hour"
        )

    def test_snow_entry_before_window_not_applied(self, station, monkeypatch):
        """A snowfall entry before the first hourly key must not set snow_fraction."""
        monkeypatch.setattr(network, "get_stream", make_hourly_stream("boston_hourly.json"))
        station.get_hourly_forecast()

        first_key = next(iter(station.hourly))
        before = self._one_hour_before(first_key)

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._griddata(snow_values=[{"validTime": f"{before}/PT1H", "value": 100.0}])
        ))
        station.get_griddata()

        assert all(h.snow_fraction == 0.0 for h in station.hourly.values()), (
            "A snowfall entry before the hourly window should not set snow_fraction on any Hour"
        )

    def test_snow_entry_after_window_not_applied(self, station, monkeypatch):
        """A snowfall entry after the last hourly key must not set snow_fraction."""
        monkeypatch.setattr(network, "get_stream", make_hourly_stream("boston_hourly.json"))
        station.get_hourly_forecast()

        last_key = next(reversed(station.hourly))
        after = self._one_hour_after(last_key)

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._griddata(snow_values=[{"validTime": f"{after}/PT1H", "value": 100.0}])
        ))
        station.get_griddata()

        assert all(h.snow_fraction == 0.0 for h in station.hourly.values()), (
            "A snowfall entry after the hourly window should not set snow_fraction on any Hour"
        )

    def test_in_window_qpf_applied_despite_out_of_window_neighbours(self, station, monkeypatch):
        """In-window QPF entries are applied even when flanked by out-of-window entries."""
        monkeypatch.setattr(network, "get_stream", make_hourly_stream("boston_hourly.json"))
        station.get_hourly_forecast()

        first_key = next(iter(station.hourly))
        last_key = next(reversed(station.hourly))
        before = self._one_hour_before(first_key)
        after = self._one_hour_after(last_key)

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(self._griddata(
            qpf_values=[
                {"validTime": f"{before}/PT1H",                  "value": 99.0},
                {"validTime": f"{first_key}:00:00+00:00/PT1H",  "value": 7.0},
                {"validTime": f"{last_key}:00:00+00:00/PT1H",   "value": 7.0},
                {"validTime": f"{after}/PT1H",                   "value": 99.0},
            ]
        )))
        station.get_griddata()

        assert station.hourly[first_key].qpf_mm == 7.0, (
            "First in-window QPF entry should be applied"
        )
        assert station.hourly[last_key].qpf_mm == 7.0, (
            "Last in-window QPF entry should be applied"
        )
        assert all(h.qpf_mm != 99.0 for h in station.hourly.values()), (
            "Sentinel out-of-window QPF value (99.0) must not appear on any Hour"
        )


# ---------------------------------------------------------------------------
# Griddata temperature fallback
# ---------------------------------------------------------------------------

class TestGriddataTemperatureFallback:
    """station.hourly uses griddata temperature when griddata is fresher than hourly.

    Hourly temperature is used when hourly is fresher or when griddata has no
    temperature data.  The griddata_store is independent of the hourly_store,
    so a subsequent hourly-only re-fetch does not discard griddata temperatures.
    """

    # UTC key and matching local startTime/endTime used in all subtests.
    _UTC_KEY  = "2026-05-18T14"
    _START    = "2026-05-18T10:00:00-04:00"
    _END      = "2026-05-18T11:00:00-04:00"
    _VALID    = "2026-05-18T14:00:00+00:00/PT1H"

    # A griddata temperature of 20 °C converts to round(20*9/5+32) = 68 °F.
    _TEMP_C   = 20.0
    _TEMP_F   = 68          # expected °F after conversion
    _HOURLY_F = 72          # temperature from the hourly endpoint

    def _hourly_payload(self, update_time):
        return {
            "properties": {
                "updateTime": update_time,
                "periods": [{
                    "number": 1,
                    "startTime": self._START,
                    "endTime":   self._END,
                    "temperature": self._HOURLY_F,
                    "temperatureUnit": "F",
                    "probabilityOfPrecipitation": {
                        "unitCode": "wmoUnit:percent",
                        "value": 0,
                    },
                    "shortForecast": "Sunny",
                }],
            }
        }

    def _griddata_payload(self, update_time, include_temperature=True):
        props = {"updateTime": update_time}
        if include_temperature:
            props["temperature"] = {
                "values": [{"validTime": self._VALID, "value": self._TEMP_C}]
            }
        props["quantitativePrecipitation"] = {"values": []}
        props["snowfallAmount"]            = {"values": []}
        return {"properties": props}

    def test_griddata_temperature_applied_when_fresher(self, station, monkeypatch):
        """Griddata temperature overrides hourly when griddata model is newer."""
        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._hourly_payload("2026-05-18T08:00:00+00:00")
        ))
        station.get_hourly_forecast()

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._griddata_payload("2026-05-18T10:00:00+00:00")
        ))
        station.get_griddata()

        h = station.hourly.get(self._UTC_KEY)
        assert h is not None, "Expected hour not found in combined view"
        assert h.temperature == self._TEMP_F, (
            f"Expected griddata temperature {self._TEMP_F}°F, got {h.temperature}°F"
        )

    def test_hourly_temperature_used_when_fresher(self, station, monkeypatch):
        """Hourly temperature is kept when hourly model is newer than griddata."""
        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._hourly_payload("2026-05-18T12:00:00+00:00")
        ))
        station.get_hourly_forecast()

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._griddata_payload("2026-05-18T08:00:00+00:00")
        ))
        station.get_griddata()

        h = station.hourly.get(self._UTC_KEY)
        assert h is not None
        assert h.temperature == self._HOURLY_F, (
            f"Expected hourly temperature {self._HOURLY_F}°F, got {h.temperature}°F"
        )

    def test_griddata_temperature_not_present(self, station, monkeypatch):
        """Hourly temperature is used when griddata response has no temperature field."""
        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._hourly_payload("2026-05-18T08:00:00+00:00")
        ))
        station.get_hourly_forecast()

        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._griddata_payload("2026-05-18T10:00:00+00:00", include_temperature=False)
        ))
        station.get_griddata()

        h = station.hourly.get(self._UTC_KEY)
        assert h is not None
        assert h.temperature == self._HOURLY_F, (
            "Hourly temperature should be used when griddata has no temperature data"
        )

    def test_griddata_temperature_preserved_across_hourly_refetch(self, station, monkeypatch):
        """A second hourly fetch with a still-stale model continues using griddata temps.

        _griddata_store is independent of _hourly_store — re-fetching hourly does
        not clear griddata values.  The hourly property re-evaluates freshness on
        every access, so as long as griddata_model_updated > hourly_model_updated
        the griddata temperature is used.
        """
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            _dict_to_stream(self._hourly_payload("2026-05-18T08:00:00+00:00")),
            _dict_to_stream(self._griddata_payload("2026-05-18T10:00:00+00:00")),
        ))
        station.get_hourly_forecast()
        station.get_griddata()

        # Re-fetch hourly — model is still older than griddata.
        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._hourly_payload("2026-05-18T08:00:00+00:00")
        ))
        station.get_hourly_forecast()

        h = station.hourly.get(self._UTC_KEY)
        assert h is not None
        assert h.temperature == self._TEMP_F, (
            "Griddata temperature should persist across a hourly-only re-fetch"
        )

    def test_griddata_temperature_dropped_when_hourly_becomes_fresher(self, station, monkeypatch):
        """Once hourly model catches up, hourly temperature takes over."""
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            _dict_to_stream(self._hourly_payload("2026-05-18T08:00:00+00:00")),
            _dict_to_stream(self._griddata_payload("2026-05-18T10:00:00+00:00")),
        ))
        station.get_hourly_forecast()
        station.get_griddata()

        # New hourly fetch — model is now fresher than griddata.
        monkeypatch.setattr(network, "get_stream", _dict_to_stream(
            self._hourly_payload("2026-05-18T12:00:00+00:00")
        ))
        station.get_hourly_forecast()

        h = station.hourly.get(self._UTC_KEY)
        assert h is not None
        assert h.temperature == self._HOURLY_F, (
            "Hourly temperature should be used once hourly model is fresher than griddata"
        )


# ---------------------------------------------------------------------------
# validTimes informational log
# ---------------------------------------------------------------------------
