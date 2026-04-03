"""Minimal DST rule engine for CircuitPython timezone conversion.

Implements M-rule (month/week/weekday) DST transitions for US timezones.
CircuitPython lacks zoneinfo; this provides basic timezone support.

Based on: https://emergent.unpythonic.net/01595021837
"""
# circuit -*- python -*-

import time

YEAR_BASE = 1900
EPOCH_YEAR = 1970
EPOCH_WDAY = 4
EPOCH_YEARS_SINCE_LEAP = 2
EPOCH_YEARS_SINCE_CENTURY = 70
EPOCH_YEARS_SINCE_LEAP_CENTURY = 370
SECSPERDAY = 86400
SECSPERHOUR = 3600
DAYSPERWEEK = 7

# Days in each month: byte arrays for non-leap and leap years
month_lengths = [
b'\x1f\x1c\x1f\x1e\x1f\x1e\x1f\x1f\x1e\x1f\x1e\x1f',
b'\x1f\x1d\x1f\x1e\x1f\x1e\x1f\x1f\x1e\x1f\x1e\x1f'
]

if hasattr(time, 'gmtime'):
    gmtime = time.gmtime
else:
    gmtime = time.localtime

def isleap(year):
    return ((((year) % 4) == 0 and ((year) % 100) != 0) or ((year) % 400) == 0)

class TzInfo:
    """Base class for timezone info with DST rules."""
    @classmethod
    def localtime(cls, utc=None):
        if utc is None: utc = time.time()
        is_dst = cls.dstrule(utc)
        offset = cls.altzone if is_dst else cls.timezone
        r = gmtime(utc - offset)
        return time.struct_time(r[:8] + (is_dst,))

class MRuleTimeZone(TzInfo):
    """Timezone using M-rule DST transitions (month/week/weekday).
    
    US timezones: DST starts 2nd Sunday of March, ends 1st Sunday of November."""
    start = (3, 2, 0)
    end = (11, 1, 0)

    @classmethod
    def dstrule(cls, utc):
        r = gmtime(utc - cls.timezone)
        cls._calc(r.tm_year)
        if cls._north:
            return utc >= cls._change[0] and utc < cls._change[1]
        else:
            return utc >= cls._change[0] or utc < cls._change[1]

    @classmethod
    def _calc(cls, year):
        if cls._year == year: return
        cls._year = year
        cls._change = (
            cls._calc1(year, cls.timezone, *cls.start),
            cls._calc1(year, cls.altzone, *cls.end)
        )
        cls._north = cls._change[0] < cls._change[1]

    @classmethod
    def _calc1(cls, year, offset, m, n, d):
        yleap = isleap(year)
        year = year - EPOCH_YEAR
        days = (year * 365 + 
            (year - 1 + EPOCH_YEARS_SINCE_LEAP) // 4 -
            (year - 1 + EPOCH_YEARS_SINCE_CENTURY) // 100 +
            (year - 1 + EPOCH_YEARS_SINCE_LEAP_CENTURY) // 400)

        lengths = month_lengths[yleap]

        for j in range(1, m):
            days += lengths[j-1]

        m_wday = (EPOCH_WDAY + days) % DAYSPERWEEK
        wday_diff = d - m_wday
        if wday_diff < 0:
            wday_diff += DAYSPERWEEK
        m_day = (n-1) * DAYSPERWEEK + wday_diff

        while m_day >= lengths[j-1]:
            m_day -= DAYSPERWEEK

        days += m_day

        # DST transitions happen at 2:00 AM local time
        return offset + days * SECSPERDAY + 2 * SECSPERHOUR

    _year = None
    _north = None
    _change = None

# Extend if you need this to work outside of the continental US
# timezone/altzone are UTC offsets in seconds (positive = west), per POSIX convention
class US_Eastern(MRuleTimeZone):
    tzname = ('EST', 'EDT')
    timezone = 18000
    altzone = 14400

class US_Central(MRuleTimeZone):
    tzname = ('CST', 'CDT')
    timezone = 21600
    altzone = 18000

class US_Mountain(MRuleTimeZone):
    tzname = ('MST', 'MDT')
    timezone = 25200
    altzone = 21600

class US_Arizona(MRuleTimeZone):
    tzname = ('MST', 'MST')
    timezone = 25200
    altzone = 25200

class US_Pacific(MRuleTimeZone):
    tzname = ('PST', 'PDT')
    timezone = 28800
    altzone = 25200
