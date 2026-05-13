"""Unit tests for Station properties and helpers not covered by forecast tests."""
from time import mktime, struct_time
from unittest.mock import patch

import pytest

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
    def test_returns_none_when_hourly_updated_is_none(self, station):
        assert station.hourly_update_age is None

    def test_returns_none_when_hourly_updated_is_empty_string(self, station):
        station.hourly_updated = ""
        assert station.hourly_update_age is None

    def test_returns_zero_when_fetched_right_now(self, station):
        station.hourly_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH):
            assert station.hourly_update_age == 0

    def test_returns_correct_age_in_seconds(self, station):
        station.hourly_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 3600):
            assert station.hourly_update_age == 3600

    def test_returns_exactly_24h_when_one_day_old(self, station):
        station.hourly_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 86400):
            assert station.hourly_update_age == 86400

    def test_returns_more_than_24h_when_older(self, station):
        station.hourly_updated = _UPDATE_ISO
        with patch("station._time", return_value=_UPDATE_EPOCH + 90000):
            assert station.hourly_update_age == 90000

    def test_parses_different_timestamps(self, station):
        iso = "2026-01-15T03:30:00+00:00"
        expected_epoch = mktime(struct_time((2026, 1, 15, 3, 30, 0, 0, -1, -1)))
        station.hourly_updated = iso
        with patch("station._time", return_value=expected_epoch + 7200):
            assert station.hourly_update_age == 7200
