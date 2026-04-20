"""Tests for station.py pure utility functions: _parse_utc_key and _add_days."""
import pytest

from station import _parse_utc_key, _add_days


class TestParseUtcKey:
    """Convert local ISO time with offset to UTC hour key."""

    def test_negative_offset(self):
        assert _parse_utc_key("2026-03-22T19:00:00-04:00") == "2026-03-22T23"

    def test_positive_offset(self):
        assert _parse_utc_key("2026-03-22T19:00:00+05:00") == "2026-03-22T14"

    def test_zero_offset(self):
        assert _parse_utc_key("2026-06-15T08:00:00+00:00") == "2026-06-15T08"

    def test_day_rollover_forward(self):
        # 11 PM minus -5 offset = 4 AM next day
        assert _parse_utc_key("2026-03-22T23:00:00-05:00") == "2026-03-23T04"

    def test_day_rollover_backward(self):
        # 1 AM plus +5 offset = 8 PM previous day
        assert _parse_utc_key("2026-03-22T01:00:00+05:00") == "2026-03-21T20"

    def test_month_boundary_forward(self):
        # March 31, 11 PM EST → April 1, 4 AM UTC
        result = _parse_utc_key("2026-03-31T23:00:00-05:00")
        assert result == "2026-04-01T04"

    def test_month_boundary_backward(self):
        # April 1, 1 AM with +5 offset → March 31, 8 PM UTC
        result = _parse_utc_key("2026-04-01T01:00:00+05:00")
        assert result == "2026-03-31T20"

    def test_year_boundary_forward(self):
        result = _parse_utc_key("2026-12-31T23:00:00-05:00")
        assert result == "2027-01-01T04"


class TestAddDays:
    """Date arithmetic with month/year rollovers."""

    def test_simple_add(self):
        assert _add_days("2026-03-15", 3) == "2026-03-18"

    def test_simple_subtract(self):
        assert _add_days("2026-03-15", -3) == "2026-03-12"

    def test_add_zero(self):
        assert _add_days("2026-06-15", 0) == "2026-06-15"

    def test_month_rollover_forward(self):
        assert _add_days("2026-01-30", 3) == "2026-02-02"

    def test_month_rollover_backward(self):
        assert _add_days("2026-03-02", -3) == "2026-02-27"

    def test_year_rollover_forward(self):
        assert _add_days("2026-12-30", 5) == "2027-01-04"

    def test_year_rollover_backward(self):
        assert _add_days("2026-01-03", -5) == "2025-12-29"

    def test_leap_year_feb_28_to_29(self):
        assert _add_days("2028-02-28", 1) == "2028-02-29"

    def test_leap_year_feb_29_to_mar_1(self):
        assert _add_days("2028-02-29", 1) == "2028-03-01"

    def test_non_leap_year_feb_28_to_mar_1(self):
        assert _add_days("2026-02-28", 1) == "2026-03-01"

    def test_add_30_days(self):
        assert _add_days("2026-01-15", 30) == "2026-02-14"

    def test_subtract_across_multiple_months(self):
        assert _add_days("2026-05-01", -60) == "2026-03-02"

    def test_huge_offset_raises(self):
        """Extremely large offsets should raise instead of looping forever."""
        with pytest.raises(ValueError):
            _add_days("2026-01-01", 100000)
