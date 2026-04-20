"""RTC and NTP time management with timezone/DST conversion.

Syncs UTC time from NTP pool, converts to local time for display using
hardcoded DST rules (CircuitPython lacks zoneinfo).
"""
import time

import rtc


import network
import dstrule

# Clock display colors signal time sync confidence:
# White = synced, Magenta = error, Purple = uncertain/no timezone
COLOR_NORMAL = 0xFFFFFF
COLOR_ERROR = 0xFF0080
COLOR_UNCERTAIN = 0x8000FF

TIME_UNKNOWN = ""

class Clock():
    """Manages RTC, NTP sync, and local time conversion."""


    def __init__(self,config):
        """Initialize clock with config for 12/24h mode and delimiter."""

        if 'CLOCK_TWENTYFOUR' in config:
            self.twentyfour=bool(config['CLOCK_TWENTYFOUR'])
        else:
            self.twentyfour=False
        if 'CLOCK_DELIMINATOR' in config:  # typo in key name; kept for compatibility
            self.delim=config['CLOCK_DELIMINATOR'][0]
        else:
            self.delim=':'

        self.color = COLOR_ERROR
        self.tz=None
        self.__dstrule=None

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
                print(f"Time is now {self.isotime} (adjusted by {timedelta:+})")
                self.color=COLOR_NORMAL
                break
            except OSError as e:
                print(f"{e}")
                tries += 1
                self.color=COLOR_ERROR
                time.sleep(5)

    ALASKA_ZONES = {
        "America/Anchorage", "America/Juneau", "America/Nome",
        "America/Yakutat", "America/Sitka", "America/Metlakatla",
    }

    def set_tz(self,tz):
        """Set timezone using hardcoded DST rules.

        Supports all 50 US states. Unrecognized timezone strings silently
        leave the DST rule unset, which causes pretty_time and isotime to
        return empty strings and sets the clock color to COLOR_UNCERTAIN."""
        tz = tz.replace(" ", "_")
        self.tz=tz
        if tz=="America/New_York" or tz[:16]=="America/Indiana":
            self.__dstrule=dstrule.US_Eastern
        elif tz=="America/Chicago":
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

    @property
    def utc(self):
        lt = time.localtime(time.time())
        return f"{lt.tm_year}-{lt.tm_mon:02}-{lt.tm_mday:02}T{lt.tm_hour:02}:{lt.tm_min:02}:{lt.tm_sec:02}+00:00"

    @property
    def pretty_time(self):
        """Format local time for display (12h or 24h per config)."""
        if not self.__dstrule:
            print("Timezone not set.")
            self.color=COLOR_UNCERTAIN
            return TIME_UNKNOWN

        try:
            lt = self.__dstrule.localtime(time.time())
        except OverflowError:
            print("\nClock too early!")
            self.color=COLOR_UNCERTAIN
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
        if not self.__dstrule:
            print("Timezone not set.")
            self.color=COLOR_UNCERTAIN
            return ""

        try:
            lt = self.__dstrule.localtime(time.time())
        except OverflowError:
            print("Clock is way too far in the past.")
            self.color=COLOR_UNCERTAIN
            return ""

        if lt.tm_isdst:
            tzoffset=self.__dstrule.altzone // 3600
        else:
            tzoffset=self.__dstrule.timezone // 3600

        return f"{lt.tm_year}-{lt.tm_mon:02}-{lt.tm_mday:02}T{lt.tm_hour:02}:{lt.tm_min:02}:{lt.tm_sec:02}-{tzoffset}:00"


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
        return self.__dstrule.localtime(time.time()).tm_hour

    def uncertain(self):
        """Mark clock as uncertain (purple color)."""
        self.color = COLOR_UNCERTAIN
        print("Clock not certain.")

    def wait(self):
        """Sleep until the minute changes, using two-phase approach.

        Long sleep until :59, then spin-wait for precise minute rollover."""

        if not self.__dstrule:
            print("Timezone not set.")
            time.sleep(1)
            return

        print(f"{self.isotime} Waiting for the minute to change.")

        t = time.localtime(time.time())
        s = t.tm_sec
        m = t.tm_min

        print(f"Waiting from :{s:02} to :59")
        time.sleep(59-s)

        print("Burning the last second.")
        while time.localtime(time.time()).tm_min == m:
            pass

        print(self.isotime)


