"""Unit tests for Station properties and helpers not covered by forecast tests."""
from time import mktime, struct_time
from unittest.mock import patch

import pytest

import network
from station import Station


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

    def test_returns_none_when_hourly_model_updated_is_empty_string(self, station):
        station.hourly_model_updated = ""
        assert station.hourly_update_age is None

    def test_returns_zero_when_fetched_right_now(self, station):
        station.hourly_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH):
            assert station.hourly_update_age == 0

    def test_returns_correct_age_in_seconds(self, station):
        station.hourly_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 3600):
            assert station.hourly_update_age == 3600

    def test_returns_exactly_24h_when_one_day_old(self, station):
        station.hourly_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 86400):
            assert station.hourly_update_age == 86400

    def test_returns_more_than_24h_when_older(self, station):
        station.hourly_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 90000):
            assert station.hourly_update_age == 90000

    def test_parses_different_timestamps(self, station):
        iso = "2026-01-15T03:30:00+00:00"
        expected_epoch = mktime(struct_time((2026, 1, 15, 3, 30, 0, 0, -1, -1)))
        station.hourly_model_updated = iso
        with patch("station._time", return_value=expected_epoch + 7200):
            assert station.hourly_update_age == 7200


# ---------------------------------------------------------------------------
# Station.griddata_update_age
# ---------------------------------------------------------------------------

class TestGriddataUpdateAge:
    def test_returns_none_when_griddata_model_updated_is_none(self, station):
        assert station.griddata_update_age is None

    def test_returns_none_when_griddata_model_updated_is_empty_string(self, station):
        station.griddata_model_updated = ""
        assert station.griddata_update_age is None

    def test_returns_zero_when_fetched_right_now(self, station):
        station.griddata_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH):
            assert station.griddata_update_age == 0

    def test_returns_correct_age_in_seconds(self, station):
        station.griddata_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 3600):
            assert station.griddata_update_age == 3600

    def test_returns_exactly_24h_when_one_day_old(self, station):
        station.griddata_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 86400):
            assert station.griddata_update_age == 86400

    def test_returns_more_than_24h_when_older(self, station):
        station.griddata_model_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 90000):
            assert station.griddata_update_age == 90000

    def test_parses_different_timestamps(self, station):
        iso = "2026-01-15T03:30:00+00:00"
        expected_epoch = mktime(struct_time((2026, 1, 15, 3, 30, 0, 0, -1, -1)))
        station.griddata_model_updated = iso
        with patch("station._time", return_value=expected_epoch + 7200):
            assert station.griddata_update_age == 7200


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
