"""Unit tests for Station properties and helpers not covered by forecast tests."""
from time import mktime, struct_time
from unittest.mock import patch

import pytest

import network
from station import ACIS_TEMP_RANGE_MIN_BUDGET_SECONDS, Station


@pytest.fixture
def station():
    config = {
        "GRIDPOINT_API":  "https://test/points",
        "HISTORICAL_API": "https://test/historical",
    }
    return Station(config)


# ---------------------------------------------------------------------------
# Station.hourly_update_age
# ---------------------------------------------------------------------------

# Epoch corresponding to 2026-05-12T10:00:00 UTC, computed the same way the
# property does: via mktime(struct_time(...)).  This is timezone-safe because
# both sides of the subtraction use the same mktime() interpretation.
_UPDATE_EPOCH = mktime(struct_time((2026, 5, 12, 10, 0, 0, 0, -1, -1)))
_UPDATE_ISO   = "2026-05-12T10:00:00+00:00"


class TestHourlyUpdateAge:
    def test_returns_none_when_hourly_model_updated_is_none(self, station):
        assert station.hourly_update_age is None

    def test_returns_zero_when_fetched_right_now(self, station):
        station.hourly_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH):
            assert station.hourly_update_age == 0

    def test_returns_correct_age_in_seconds(self, station):
        station.hourly_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 3600):
            assert station.hourly_update_age == 3600

    def test_parses_different_timestamps(self, station):
        iso = "2026-01-15T03:30:00+00:00"
        expected_epoch = mktime(struct_time((2026, 1, 15, 3, 30, 0, 0, -1, -1)))
        station.hourly_model_updated = iso
        with patch("station._time", return_value=expected_epoch + 7200):
            assert station.hourly_update_age == 7200



# ---------------------------------------------------------------------------
# Station._get_point_info — malformed responses
# ---------------------------------------------------------------------------

class TestGetPointInfo:
    """_get_point_info() must degrade gracefully on unexpected NOAA responses."""

    def test_returns_none_and_no_raise_when_properties_missing(self, station, monkeypatch):
        """A response without 'properties' must return None — not raise KeyError.

        Regression guard: before the fix, json_data['properties'] raised
        KeyError, which propagated through get_station()'s RuntimeError-only
        except clause and crashed the scheduler iteration.
        """
        station.lat = "42.36"
        station.lon = "-71.06"
        monkeypatch.setattr(network, "request",
            lambda *a, **kw: {"type": "Feature", "geometry": None})

        result = station._get_point_info()

        assert result is None
        assert station.hourly_url is None
        assert station.station_id is None

    def test_returns_true_on_valid_response(self, station, monkeypatch):
        """A well-formed response populates URLs and returns True."""
        station.lat = "42.36"
        station.lon = "-71.06"
        monkeypatch.setattr(network, "request", lambda *a, **kw: {
            "properties": {
                "forecastHourly":      "https://noaa/hourly",
                "forecastGridData":    "https://noaa/griddata",
                "observationStations": "https://noaa/stations",
                "timeZone":            "America/New_York",
                "relativeLocation":    {"properties": {"city": "Boston", "state": "MA"}},
            }
        })

        result = station._get_point_info()

        assert result is True
        assert station.hourly_url == "https://noaa/hourly"
        assert station.griddata_url == "https://noaa/griddata"


# ---------------------------------------------------------------------------
# TestGetTempRange — budget guard and PRISM-data-failure fallthrough
# ---------------------------------------------------------------------------

_VALID_PRISM_RESPONSE = {"smry": ["-10", "95"]}
_SENTINEL_PRISM_RESPONSE = {"smry": ["-999", "-999"]}


def _station_with_location():
    """Return a Station with lat/lon set, ready for get_temp_range() calls."""
    config = {
        "GRIDPOINT_API":  "https://api.weather.gov/points",
        "HISTORICAL_API": "https://data.rcc-acis.org/GridData",
    }
    s = Station(config)
    s.lat = "42.36"
    s.lon = "-71.06"
    return s


class TestGetTempRangeBudgetGuard:
    """get_temp_range() must skip all network calls immediately when budget is low."""

    def test_returns_none_without_calling_request(self, monkeypatch):
        """When budget is below the threshold, no POST should be attempted."""
        calls = []
        monkeypatch.setattr(network, "_budget_remaining",
                            lambda: ACIS_TEMP_RANGE_MIN_BUDGET_SECONDS - 1)
        monkeypatch.setattr(network, "request",
                            lambda *a, **kw: calls.append(kw) or _VALID_PRISM_RESPONSE)

        result = _station_with_location().get_temp_range()

        assert result is None
        assert calls == [], "network.request must not be called when budget is exhausted"


class TestGetTempRangePrismFallthrough:
    """When budget is fine but a candidate date returns bad PRISM data, the next is tried."""

    def test_tries_second_candidate_after_network_none(self, monkeypatch):
        """A None return (network error) on the first candidate must not abort the loop."""
        responses = [None, _VALID_PRISM_RESPONSE]
        calls = []

        def fake_request(*args, **kwargs):
            calls.append(args)
            return responses.pop(0)

        monkeypatch.setattr(network, "_budget_remaining",
                            lambda: ACIS_TEMP_RANGE_MIN_BUDGET_SECONDS + 30)
        monkeypatch.setattr(network, "request", fake_request)

        result = _station_with_location().get_temp_range()

        assert result == (-10, 95)
        assert len(calls) == 2, "must attempt a second candidate after the first fails"

    def test_tries_second_candidate_after_prism_sentinel(self, monkeypatch):
        """A -999 sentinel response on the first candidate must not abort the loop."""
        responses = [_SENTINEL_PRISM_RESPONSE, _VALID_PRISM_RESPONSE]

        def fake_request(*args, **kwargs):
            return responses.pop(0)

        monkeypatch.setattr(network, "_budget_remaining",
                            lambda: ACIS_TEMP_RANGE_MIN_BUDGET_SECONDS + 30)
        monkeypatch.setattr(network, "request", fake_request)

        result = _station_with_location().get_temp_range()

        assert result == (-10, 95)

