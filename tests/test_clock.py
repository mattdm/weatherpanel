"""Tests for clock time formatting and timezone offset output.

Uses real dstrule timezone math with monkeypatched time.time() to produce
deterministic local times without network or hardware dependencies.
"""
import calendar

import pytest

from clock import Clock


def _utc_ts(year, month, day, hour, minute=0, second=0):
    """Return a UTC POSIX timestamp for the given components."""
    return calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))


def _make_clock(tz='America/New_York', twentyfour=False, delim=':'):
    """Create a Clock with the given settings, bypassing network/hardware."""
    config = {'CLOCK_TWENTYFOUR': twentyfour, 'CLOCK_DELIMITER': delim}
    c = Clock(config)
    c.set_tz(tz)
    return c


# ---------------------------------------------------------------------------
# pretty_time: 12-hour mode edge cases
# ---------------------------------------------------------------------------

class TestPrettyTime12h:
    """12-hour mode: verify AM/PM hour folding for all boundary cases."""

    # 2026-01-15 05:00:00 UTC = 2026-01-15 00:00:00 EST (midnight → 12:xx)
    def test_midnight_shows_as_12(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 5, 0, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        assert c.pretty_time == "12:00"

    # 2026-01-15 17:00:00 UTC = 2026-01-15 12:00:00 EST (noon → 12:xx)
    def test_noon_shows_as_12(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 17, 0, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        assert c.pretty_time == "12:00"

    # 2026-01-15 06:30:00 UTC = 2026-01-15 01:30:00 EST (1 AM → 1:xx)
    def test_1am_shows_as_1(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 6, 30, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        assert c.pretty_time == "1:30"

    # 2026-01-15 18:45:00 UTC = 2026-01-15 13:45:00 EST (1 PM → 1:xx)
    def test_1pm_shows_as_1(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 18, 45, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        assert c.pretty_time == "1:45"

    # 2026-07-15 23:30:00 UTC = 2026-07-15 19:30:00 EDT (7 PM → 7:xx)
    def test_7pm_shows_as_7(self, monkeypatch):
        ts = _utc_ts(2026, 7, 15, 23, 30, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        assert c.pretty_time == "7:30"

    def test_custom_delimiter(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 17, 5, 0)  # 12:05 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock(delim='.')
        assert c.pretty_time == "12.05"

    def test_no_tz_returns_empty(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 17, 0, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        config = {'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'}
        c = Clock(config)
        # set_tz not called -- __dstrule stays None
        assert c.pretty_time == ""

    def test_minutes_zero_padded(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 10, 5, 0)  # 5:05 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        assert c.pretty_time == "5:05"


class TestPrettyTime24h:
    """24-hour mode: hour passes through without AM/PM folding."""

    def test_midnight_shows_as_0(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 5, 0, 0)  # 0:00 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock(twentyfour=True)
        assert c.pretty_time == "0:00"

    def test_noon_shows_as_12(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 17, 0, 0)  # 12:00 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock(twentyfour=True)
        assert c.pretty_time == "12:00"

    def test_1pm_shows_as_13(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 18, 0, 0)  # 13:00 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock(twentyfour=True)
        assert c.pretty_time == "13:00"

    def test_11pm_shows_as_23(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 4, 0, 0)  # 23:00 EST previous UTC day
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock(twentyfour=True)
        assert c.pretty_time == "23:00"


# ---------------------------------------------------------------------------
# isotime: ISO 8601 offset formatting
# ---------------------------------------------------------------------------

class TestIsotime:
    """isotime() must produce zero-padded, correctly-signed offsets.

    This matters because display.py compares hour.end < isotime as a
    lexicographic string comparison against NOAA's ISO timestamps, which
    always use zero-padded offsets like -04:00.
    """

    def test_standard_time_offset_zero_padded(self, monkeypatch):
        """EST is UTC-5; offset must be -05:00, not -5:00."""
        ts = _utc_ts(2026, 1, 15, 12, 0, 0)  # 7:00 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        iso = c.isotime
        assert iso.endswith("-05:00"), f"Expected -05:00 suffix, got: {iso!r}"

    def test_dst_offset_zero_padded(self, monkeypatch):
        """EDT is UTC-4; offset must be -04:00, not -4:00."""
        ts = _utc_ts(2026, 7, 15, 12, 0, 0)  # 8:00 EDT
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        iso = c.isotime
        assert iso.endswith("-04:00"), f"Expected -04:00 suffix, got: {iso!r}"

    def test_isotime_format_structure(self, monkeypatch):
        """Full format: YYYY-MM-DDTHH:MM:SS±HH:00."""
        ts = _utc_ts(2026, 1, 15, 12, 30, 45)  # 7:30:45 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        iso = c.isotime
        assert iso == "2026-01-15T07:30:45-05:00"

    def test_dst_format_structure(self, monkeypatch):
        """Summer EST: UTC-4 offset, correct date/time."""
        ts = _utc_ts(2026, 7, 15, 12, 0, 0)  # 8:00:00 EDT
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        iso = c.isotime
        assert iso == "2026-07-15T08:00:00-04:00"

    def test_no_tz_returns_empty(self, monkeypatch):
        ts = _utc_ts(2026, 1, 15, 12, 0, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        config = {'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'}
        c = Clock(config)
        assert c.isotime == ""

    def test_alaska_standard_offset(self, monkeypatch):
        """AKST is UTC-9; verify two-digit padding: -09:00."""
        ts = _utc_ts(2026, 1, 15, 21, 0, 0)  # 12:00 AKST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock(tz='America/Anchorage')
        iso = c.isotime
        assert iso.endswith("-09:00"), f"Expected -09:00 suffix, got: {iso!r}"

    def test_today_property(self, monkeypatch):
        """today is the first 10 chars of isotime."""
        ts = _utc_ts(2026, 7, 4, 15, 0, 0)  # 11:00 EDT = July 4
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        assert c.today == "2026-07-04"
