"""RTC and NTP time management with timezone/DST conversion.

Syncs UTC time from NTP pool, converts to local time for display using
hardcoded DST rules (CircuitPython lacks zoneinfo).

Module-level color constants (COLOR_NORMAL, COLOR_ERROR, COLOR_UNCERTAIN)
reflect the defaults from COLOR_DEFAULTS for backward compatibility with
tests and any code that imports them directly.  Clock.__init__ sets instance
attributes from the config dict so that per-device overrides take effect.
"""
import time

import rtc


import network
import dstrule
from appconfig import COLOR_DEFAULTS

NTP_MIN_BUDGET_S = 10   # 5s inter-retry sleep plus headroom for the NTP exchange itself

# Module-level constants for backward compatibility — equal to COLOR_DEFAULTS.
COLOR_NORMAL    = COLOR_DEFAULTS['CLOCK_NORMAL_COLOR']
COLOR_ERROR     = COLOR_DEFAULTS['CLOCK_ERROR_COLOR']
COLOR_UNCERTAIN = COLOR_DEFAULTS['CLOCK_UNCERTAIN_COLOR']

TIME_UNKNOWN = ""

class Clock:
    """Manages RTC, NTP sync, and local time conversion."""

    def __init__(self, config):
        """Initialize clock with config for 12/24h mode and delimiter."""

        if 'CLOCK_TWENTYFOUR' in config:
            self.twentyfour = bool(config['CLOCK_TWENTYFOUR'])
        else:
            self.twentyfour = False
        if 'CLOCK_DELIMITER' in config:
            self.delim = config['CLOCK_DELIMITER'][0]
        else:
            self.delim = ':'

        # Per-instance color overrides from config; shadow the module-level defaults.
        self.COLOR_NORMAL    = config.get('CLOCK_NORMAL_COLOR',    COLOR_DEFAULTS['CLOCK_NORMAL_COLOR'])
        self.COLOR_ERROR     = config.get('CLOCK_ERROR_COLOR',     COLOR_DEFAULTS['CLOCK_ERROR_COLOR'])
        self.COLOR_UNCERTAIN = config.get('CLOCK_UNCERTAIN_COLOR', COLOR_DEFAULTS['CLOCK_UNCERTAIN_COLOR'])

        self.color = self.COLOR_ERROR
        self.tz = None
        self.__dstrule = None
        self._synced = False   # True once NTP has succeeded at least once

        self.ntp = network.ntp()
        self.rtc = rtc.RTC()

    def sync_network_time(self):
        """Sync RTC from NTP, retry on failure."""

        timedelta=0
        tries = 0
        while tries < 5:
            try:
                print("Getting network time.")
                network_time = self.ntp.datetime
                timedelta = time.mktime(network_time) - time.mktime(self.rtc.datetime)
                self.rtc.datetime = network_time
                self._synced = True
                self.color = self.COLOR_NORMAL if self.__dstrule else self.COLOR_UNCERTAIN
                print(f"Time is now {self.isotime} (adjusted by {timedelta:+} s)")
                break
            except OSError as e:
                print(f"{e}")
                tries += 1
                self.color = self.COLOR_ERROR
                if not network.has_budget(min_budget_s=NTP_MIN_BUDGET_S):
                    print("Budget exhausted — skipping NTP retry")
                    break
                time.sleep(5)

    ALASKA_ZONES = {
        "America/Anchorage", "America/Juneau", "America/Nome",
        "America/Yakutat", "America/Sitka", "America/Metlakatla",
    }

    def set_tz(self,tz):
        """Set timezone using hardcoded DST rules.

        Supports all 50 US states. Unrecognized timezone strings leave the
        DST rule unset; pretty_time and isotime return empty strings until
        a known timezone is set.

        Updates color based on what's now known: UNCERTAIN (purple) if timezone
        is recognized but NTP hasn't synced yet, NORMAL (white) if both are
        known.  Pre-sync rendering should show purple, not the error magenta
        that color is initialized to."""
        tz = tz.replace(" ", "_")
        self.tz = tz
        if (tz == "America/New_York"
                or tz.startswith("America/Indiana/")
                or tz.startswith("America/Kentucky/")):
            self.__dstrule=dstrule.US_Eastern
        elif tz == "America/Chicago" or tz.startswith("America/North_Dakota/"):
            self.__dstrule=dstrule.US_Central
        elif tz=="America/Denver":
            self.__dstrule=dstrule.US_Mountain
        elif tz=="America/Phoenix":
            self.__dstrule=dstrule.US_Arizona
        elif tz=="America/Los_Angeles":
            self.__dstrule=dstrule.US_Pacific
        elif tz in self.ALASKA_ZONES:
            self.__dstrule=dstrule.US_Alaska
        elif tz=="Pacific/Honolulu":
            self.__dstrule=dstrule.US_Hawaii
        else:
            print(f"Unknown timezone \"{tz}\".")

        # Color reflects current knowledge:
        # - timezone known + NTP synced → NORMAL (white)
        # - timezone known, not yet synced → UNCERTAIN (purple) — pre-sync is not an error
        # - timezone unknown → leave color unchanged
        if self.__dstrule:
            self.color = self.COLOR_NORMAL if self._synced else self.COLOR_UNCERTAIN

    @property
    def utc(self):
        # On CircuitPython there is no system timezone: time.localtime() converts
        # the Unix timestamp directly to a struct_time with no offset applied.
        # The RTC is always set to UTC via NTP, so localtime() returns UTC values
        # and the +00:00 suffix is accurate.
        lt = time.localtime(time.time())
        return f"{lt.tm_year}-{lt.tm_mon:02}-{lt.tm_mday:02}T{lt.tm_hour:02}:{lt.tm_min:02}:{lt.tm_sec:02}+00:00"

    def _get_localtime(self):
        """Return localtime struct, or None if the clock is unavailable.

        Returns None when no DST rule is set (timezone unknown) or when the
        epoch is pre-1970 — indicating NTP has not yet synced a valid time.
        Prints a warning in the latter case so it's visible on the serial
        console."""
        if not self.__dstrule:
            return None
        try:
            return self.__dstrule.localtime(time.time())
        except OverflowError:
            print("\nClock too early!")
            return None

    @property
    def pretty_time(self):
        """Format local time for display (12h or 24h per config)."""
        lt = self._get_localtime()
        if lt is None:
            return TIME_UNKNOWN

        if not self.twentyfour:
            if lt.tm_hour > 12:
                s = f"{lt.tm_hour-12}{self.delim}{lt.tm_min:02}"
            elif lt.tm_hour == 0:
                s = f"12{self.delim}{lt.tm_min:02}"
            else:
                s = f"{lt.tm_hour}{self.delim}{lt.tm_min:02}"
        else:
            s = f"{lt.tm_hour}{self.delim}{lt.tm_min:02}"

        return s

    @property
    def isotime(self):
        """ISO 8601 local time with timezone offset."""
        lt = self._get_localtime()
        if lt is None:
            return ""

        if lt.tm_isdst:
            tzoffset=self.__dstrule.altzone // 3600
        else:
            tzoffset=self.__dstrule.timezone // 3600

        sign = '-' if tzoffset > 0 else '+'
        return f"{lt.tm_year}-{lt.tm_mon:02}-{lt.tm_mday:02}T{lt.tm_hour:02}:{lt.tm_min:02}:{lt.tm_sec:02}{sign}{abs(tzoffset):02}:00"


    @property
    def today(self):
        """Current date in YYYY-MM-DD format."""
        return self.isotime[:10]

    @property
    def minute(self):
        """Raw UTC minute for scheduling (modular arithmetic only, not local time)."""
        return time.localtime(time.time()).tm_min

    @property
    def hour(self):
        """Local hour (0-23)."""
        if not self.__dstrule:
            return None
        return self.__dstrule.localtime(time.time()).tm_hour

    def uncertain(self):
        """Mark clock as uncertain (purple color)."""
        self.color = self.COLOR_UNCERTAIN
        print("Clock not certain.")

    def wait(self):
        """Sleep until the minute changes, using two-phase approach.

        Long sleep until :59, then spin-wait for precise minute rollover.
        Runs even when the timezone is not yet configured, so the scheduler
        loop always starts near second :00 and has a full network budget."""
        t = time.localtime(time.time())
        s = t.tm_sec
        m = t.tm_min

        if self.__dstrule:
            print(f"{self.isotime} Waiting for the minute to change.")
            print(f"Waiting from :{s:02} to :59")

        time.sleep(max(0, 59 - s))

        if self.__dstrule:
            print("Burning the last second.")

        while time.localtime(time.time()).tm_min == m:
            time.sleep(0.001)

        if self.__dstrule:
            print(self.isotime)


