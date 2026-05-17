"""Tests for clock time formatting and timezone offset output.

Uses real dstrule timezone math with monkeypatched time.time() to produce
deterministic local times without network or hardware dependencies.
"""
import calendar


from clock import Clock, COLOR_NORMAL, COLOR_ERROR, COLOR_UNCERTAIN


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

    def test_no_tz_produces_no_output(self, monkeypatch, capsys):
        ts = _utc_ts(2026, 1, 15, 17, 0, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        config = {'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'}
        c = Clock(config)
        _ = c.pretty_time
        _ = c.pretty_time
        _ = c.pretty_time
        assert capsys.readouterr().out == ""

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



# ---------------------------------------------------------------------------
# isotime: ISO 8601 offset formatting
# ---------------------------------------------------------------------------

class TestIsotime:
    """isotime() must produce the correct full ISO 8601 format with zero-padded offsets.

    display.py compares hour.end < isotime as a lexicographic string comparison
    against NOAA's ISO timestamps, which always use zero-padded offsets like
    -04:00.  A malformed offset (-4:00 instead of -04:00) would silently break
    the expired-hour filter.
    """

    def test_isotime_format_structure(self, monkeypatch):
        """Full format: YYYY-MM-DDTHH:MM:SS-HH:00, EST offset zero-padded."""
        ts = _utc_ts(2026, 1, 15, 12, 30, 45)  # 7:30:45 EST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock()
        iso = c.isotime
        assert iso == "2026-01-15T07:30:45-05:00"

    def test_dst_format_structure(self, monkeypatch):
        """Summer: UTC-4 offset, correct date/time, offset zero-padded."""
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

    def test_no_tz_produces_no_output(self, monkeypatch, capsys):
        ts = _utc_ts(2026, 1, 15, 12, 0, 0)
        monkeypatch.setattr("clock.time.time", lambda: ts)
        config = {'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'}
        c = Clock(config)
        _ = c.isotime
        _ = c.isotime
        _ = c.isotime
        assert capsys.readouterr().out == ""

    def test_alaska_standard_offset(self, monkeypatch):
        """AKST is UTC-9; verify two-digit padding: -09:00."""
        ts = _utc_ts(2026, 1, 15, 21, 0, 0)  # 12:00 AKST
        monkeypatch.setattr("clock.time.time", lambda: ts)
        c = _make_clock(tz='America/Anchorage')
        iso = c.isotime
        assert iso.endswith("-09:00"), f"Expected -09:00 suffix, got: {iso!r}"


# ---------------------------------------------------------------------------
# set_tz: coverage for sub-region timezone strings
# ---------------------------------------------------------------------------

class TestSetTzCoverage:
    """Verify that all US IANA sub-region timezone names map to the correct rule."""

    def _offset(self, tz):
        """Return the DST rule's standard UTC offset in hours (positive = west)."""
        c = Clock({'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'})
        c.set_tz(tz)
        return c._Clock__dstrule.timezone // 3600 if c._Clock__dstrule else None

    # Indiana zones (prefix "America/Indiana/") — already tested implicitly;
    # include one representative to guard against regression.
    def test_indiana_indianapolis(self):
        assert self._offset("America/Indiana/Indianapolis") == 5  # Eastern

    # Kentucky
    def test_kentucky_louisville(self):
        assert self._offset("America/Kentucky/Louisville") == 5  # Eastern

    def test_kentucky_monticello(self):
        assert self._offset("America/Kentucky/Monticello") == 5  # Eastern

    # North Dakota
    def test_north_dakota_center(self):
        assert self._offset("America/North_Dakota/Center") == 6  # Central

    def test_north_dakota_new_salem(self):
        assert self._offset("America/North_Dakota/New_Salem") == 6  # Central

    def test_north_dakota_beulah(self):
        assert self._offset("America/North_Dakota/Beulah") == 6  # Central

    def test_unknown_tz_leaves_dstrule_none(self):
        c = Clock({'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'})
        c.set_tz("Europe/London")
        assert c._Clock__dstrule is None


# ---------------------------------------------------------------------------
# set_tz: color behavior
# ---------------------------------------------------------------------------

class TestSetTzColor:
    """set_tz() must set the clock color based on what's currently known."""

    def _clock(self):
        return Clock({'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'})

    def test_known_tz_before_sync_is_uncertain(self):
        """Known timezone + no NTP sync → UNCERTAIN (purple), not ERROR (magenta)."""
        c = self._clock()
        assert c.color == COLOR_ERROR  # starts as error before anything is known
        c.set_tz("America/New_York")
        assert c.color == COLOR_UNCERTAIN

    def test_known_tz_after_sync_is_normal(self):
        """Known timezone + NTP already synced → NORMAL (white)."""
        c = self._clock()
        c._synced = True
        c.set_tz("America/New_York")
        assert c.color == COLOR_NORMAL

    def test_unknown_tz_leaves_color_unchanged(self):
        """Unrecognized timezone → color stays at whatever it was before."""
        c = self._clock()
        c.set_tz("Europe/London")
        assert c.color == COLOR_ERROR  # still the initial error color

    def test_set_tz_twice_upgrades_color_on_sync(self):
        """set_tz before sync then after sync: color transitions correctly."""
        c = self._clock()
        c.set_tz("America/Chicago")
        assert c.color == COLOR_UNCERTAIN
        c._synced = True
        c.set_tz("America/Chicago")
        assert c.color == COLOR_NORMAL


# ---------------------------------------------------------------------------
# wait() with no timezone set: silent but still blocks until the minute rolls
# ---------------------------------------------------------------------------

class TestWaitNoTz:
    def test_wait_no_tz_produces_no_output(self, monkeypatch, capsys):
        """wait() must not print anything when the timezone is unset."""
        # :30 into minute 5 → minute 6 after the sleep
        t_start = _utc_ts(2026, 1, 15, 12, 5, 30)
        t_end   = _utc_ts(2026, 1, 15, 12, 6,  0)
        times = iter([t_start, t_end])
        monkeypatch.setattr("clock.time.time", lambda: next(times))
        monkeypatch.setattr("clock.time.sleep", lambda _: None)
        config = {'CLOCK_TWENTYFOUR': False, 'CLOCK_DELIMITER': ':'}
        c = Clock(config)
        c.wait()
        assert capsys.readouterr().out == ""

