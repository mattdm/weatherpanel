"""Tests for DST rule engine against known US timezone transitions."""
import calendar

import dstrule


def _utc_timestamp(year, month, day, hour, minute=0, second=0):
    """Create a UTC timestamp from components."""
    return calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))


class TestUSEastern:
    """US Eastern: UTC-5 standard, UTC-4 daylight."""

    def test_standard_time_winter(self):
        ts = _utc_timestamp(2026, 1, 15, 12, 0, 0)
        lt = dstrule.US_Eastern.localtime(ts)
        assert lt.tm_hour == 7
        assert lt.tm_isdst == 0

    def test_dst_summer(self):
        ts = _utc_timestamp(2026, 7, 15, 12, 0, 0)
        lt = dstrule.US_Eastern.localtime(ts)
        assert lt.tm_hour == 8
        assert lt.tm_isdst == 1

    def test_spring_forward_2026(self):
        # 2026: DST starts March 8 at 2:00 AM EST = 7:00 UTC
        before = _utc_timestamp(2026, 3, 8, 6, 59, 59)
        after = _utc_timestamp(2026, 3, 8, 7, 0, 1)

        lt_before = dstrule.US_Eastern.localtime(before)
        lt_after = dstrule.US_Eastern.localtime(after)

        assert lt_before.tm_isdst == 0
        assert lt_after.tm_isdst == 1
        assert lt_before.tm_hour == 1  # 1:59 EST
        assert lt_after.tm_hour == 3   # 3:00 EDT (skips 2:xx)

    def test_fall_back_2026(self):
        # 2026: DST ends November 1 at 2:00 AM EDT = 6:00 UTC
        before = _utc_timestamp(2026, 11, 1, 5, 59, 59)
        after = _utc_timestamp(2026, 11, 1, 6, 0, 1)

        lt_before = dstrule.US_Eastern.localtime(before)
        lt_after = dstrule.US_Eastern.localtime(after)

        assert lt_before.tm_isdst == 1
        assert lt_after.tm_isdst == 0
        assert lt_before.tm_hour == 1  # 1:59 EDT
        assert lt_after.tm_hour == 1   # 1:00 EST (falls back)


class TestUSCentral:
    """US Central: UTC-6 standard, UTC-5 daylight."""

    def test_standard_time(self):
        ts = _utc_timestamp(2026, 1, 15, 18, 0, 0)
        lt = dstrule.US_Central.localtime(ts)
        assert lt.tm_hour == 12
        assert lt.tm_isdst == 0

    def test_dst(self):
        ts = _utc_timestamp(2026, 7, 15, 18, 0, 0)
        lt = dstrule.US_Central.localtime(ts)
        assert lt.tm_hour == 13
        assert lt.tm_isdst == 1


class TestUSMountain:
    """US Mountain: UTC-7 standard, UTC-6 daylight."""

    def test_standard_time(self):
        ts = _utc_timestamp(2026, 12, 25, 19, 0, 0)
        lt = dstrule.US_Mountain.localtime(ts)
        assert lt.tm_hour == 12
        assert lt.tm_isdst == 0

    def test_dst(self):
        ts = _utc_timestamp(2026, 6, 21, 19, 0, 0)
        lt = dstrule.US_Mountain.localtime(ts)
        assert lt.tm_hour == 13
        assert lt.tm_isdst == 1


class TestUSArizona:
    """Arizona: UTC-7 year-round, no DST."""

    def test_summer_hour(self):
        ts = _utc_timestamp(2026, 7, 15, 19, 0, 0)
        lt = dstrule.US_Arizona.localtime(ts)
        assert lt.tm_hour == 12

    def test_winter_hour(self):
        ts = _utc_timestamp(2026, 1, 15, 19, 0, 0)
        lt = dstrule.US_Arizona.localtime(ts)
        assert lt.tm_hour == 12

    def test_same_offset_year_round(self):
        """Arizona uses MST year-round; summer and winter hours should match."""
        summer = _utc_timestamp(2026, 7, 15, 19, 0, 0)
        winter = _utc_timestamp(2026, 1, 15, 19, 0, 0)
        assert dstrule.US_Arizona.localtime(summer).tm_hour == \
               dstrule.US_Arizona.localtime(winter).tm_hour


class TestUSPacific:
    """US Pacific: UTC-8 standard, UTC-7 daylight."""

    def test_standard_time(self):
        ts = _utc_timestamp(2026, 2, 1, 20, 0, 0)
        lt = dstrule.US_Pacific.localtime(ts)
        assert lt.tm_hour == 12
        assert lt.tm_isdst == 0

    def test_dst(self):
        ts = _utc_timestamp(2026, 8, 1, 20, 0, 0)
        lt = dstrule.US_Pacific.localtime(ts)
        assert lt.tm_hour == 13
        assert lt.tm_isdst == 1


class TestUSAlaska:
    """US Alaska: UTC-9 standard, UTC-8 daylight."""

    def test_standard_time_winter(self):
        ts = _utc_timestamp(2026, 1, 15, 21, 0, 0)
        lt = dstrule.US_Alaska.localtime(ts)
        assert lt.tm_hour == 12
        assert lt.tm_isdst == 0

    def test_dst_summer(self):
        ts = _utc_timestamp(2026, 7, 15, 21, 0, 0)
        lt = dstrule.US_Alaska.localtime(ts)
        assert lt.tm_hour == 13
        assert lt.tm_isdst == 1

    def test_spring_forward_2026(self):
        # 2026: DST starts March 8 at 2:00 AM AKST = 11:00 UTC
        before = _utc_timestamp(2026, 3, 8, 10, 59, 59)
        after = _utc_timestamp(2026, 3, 8, 11, 0, 1)

        lt_before = dstrule.US_Alaska.localtime(before)
        lt_after = dstrule.US_Alaska.localtime(after)

        assert lt_before.tm_isdst == 0
        assert lt_after.tm_isdst == 1
        assert lt_before.tm_hour == 1  # 1:59 AKST
        assert lt_after.tm_hour == 3   # 3:00 AKDT (skips 2:xx)


class TestUSHawaii:
    """Hawaii: UTC-10 year-round, no DST."""

    def test_summer_hour(self):
        ts = _utc_timestamp(2026, 7, 15, 22, 0, 0)
        lt = dstrule.US_Hawaii.localtime(ts)
        assert lt.tm_hour == 12

    def test_winter_hour(self):
        ts = _utc_timestamp(2026, 1, 15, 22, 0, 0)
        lt = dstrule.US_Hawaii.localtime(ts)
        assert lt.tm_hour == 12

    def test_same_offset_year_round(self):
        """Hawaii uses HST year-round; summer and winter hours should match."""
        summer = _utc_timestamp(2026, 7, 15, 22, 0, 0)
        winter = _utc_timestamp(2026, 1, 15, 22, 0, 0)
        assert dstrule.US_Hawaii.localtime(summer).tm_hour == \
               dstrule.US_Hawaii.localtime(winter).tm_hour


class TestEdgeCases:
    """Year boundaries and leap years."""

    def test_new_years_eve_utc_midnight(self):
        ts = _utc_timestamp(2027, 1, 1, 0, 0, 0)
        lt = dstrule.US_Eastern.localtime(ts)
        assert lt.tm_year == 2026
        assert lt.tm_mon == 12
        assert lt.tm_mday == 31
        assert lt.tm_hour == 19

    def test_leap_year_feb_29(self):
        ts = _utc_timestamp(2028, 2, 29, 12, 0, 0)
        lt = dstrule.US_Eastern.localtime(ts)
        assert lt.tm_mon == 2
        assert lt.tm_mday == 29
