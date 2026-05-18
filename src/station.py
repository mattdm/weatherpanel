"""Weather data retrieval from NOAA Weather API and RCC ACIS historical grids.

Fetches hourly forecasts, gridpoint QPF/snowfall data, and n-year historical
temperature baselines for display color-coding.

Historical baselines are stored in a 4-slot circular buffer — one slot per
forecast day (today, tomorrow, day-after-tomorrow, and three days ahead). Each
slot is fetched with a single ACIS call and rotated at midnight so only the
new three-days-ahead slot needs a fresh fetch.
"""
import gc
from collections import OrderedDict
from time import localtime, mktime, sleep, struct_time, time as _time

import network

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
FORECAST_HOURS             = 72   # 64 columns + 8 spare; covers forced-reload window
HOURLY_FORCED_RELOAD_HOURS =  6   # force full re-parse after 6h even if updateTime unchanged
FORECAST_MIN_CACHE_MINUTES = 60   # never re-fetch a forecast more often than once per hour
STALE_THRESHOLD_MINUTES    = 120  # model this old triggers reduced polling
STALE_MAX_CACHE_MINUTES    =  15  # max cache window when model is stale
HISTORY_YEARS_DEFAULT = 10
NOAA_METADATA_MIN_BUDGET_SECONDS       = 15  # fast GET + small JSON; points and stations endpoints
HOURLY_MIN_BUDGET_SECONDS              = 20  # streaming, first 72 periods only
ACIS_HISTORICAL_DAY_MIN_BUDGET_SECONDS = 25  # PRISM POST, 3-day window × N years
GRIDDATA_MIN_BUDGET_SECONDS            = 45  # streaming temperature (~4) + QPF (~26) + snowfall (~28); 10–20 s observed, budget raised for temperature
ACIS_TEMP_RANGE_MIN_BUDGET_SECONDS     = 40  # PRISM POST, full 1981–present record

# Minimum snow_fraction values inferred from shortForecast text when griddata
# shows zero snowfall (6-hour window granularity can lag the hourly text forecast
# at rain-to-snow transition boundaries). Values reflect how "frozen" each type
# is relative to rain. Applied via max() so compound phrases like "Snow/Sleet"
# pick the more-frozen tier.
SNOW_HINT_MINIMUMS = {
    "Wintry Mix":       0.5,   # NWS generic mixed precip; by definition ~50/50
    "Sleet":            0.5,   # ice pellets; frozen all the way through
    "Flurries":         0.4,   # light snow; definitively snow but minimal amounts
    "Snow":             0.3,   # catches "Rain And Snow", "Chance Snow", etc.
    "Freezing Rain":    0.1,   # falls as liquid, freezes on contact
    "Freezing Drizzle": 0.1,   # same as freezing rain
}


def _apply_snow_hint(h):
    """Apply SNOW_HINT_MINIMUMS to a single Hour when griddata shows zero snowfall.

    When the 6-hour griddata window lags the hourly text forecast at a
    rain-to-snow transition, the text may contain frozen-precip keywords while
    snow_fraction is still 0.0. This sets a minimum fraction based on how
    "frozen" each keyword tier implies.  Does nothing when snow_fraction > 0.
    """
    if h.snow_fraction == 0.0:
        hints = [v for kw, v in SNOW_HINT_MINIMUMS.items() if kw in (h.forecast or "")]
        if hints:
            h.snow_fraction = max(hints)


def _iter_time_series(values, distribute=True):
    """Iterate a NOAA griddata time series, yielding (hour_key, per_hour_value) pairs.

    Each entry has a validTime like "2026-04-20T06:00:00+00:00/PT6H" and a value.
    hour_key format: "2026-04-20T06" (UTC).

    When distribute=True (default), values are totals spread evenly across the
    window hours — e.g. QPF where PT6H/12mm yields 2mm per hour.  When
    distribute=False, values are point measurements repeated for each hour in
    the window — e.g. temperature where PT2H/26°C yields 26°C per hour.

    None values are treated as 0.0 when distribute=True and skipped entirely
    when distribute=False (no data for that period).

    When windows overlap (can occur during NOAA forecast updates), the caller
    receives duplicates and should apply only the first."""
    for entry in values:
        valid_time = entry['validTime']
        dt_part, duration = valid_time.split('/')
        n_hours = _parse_iso_duration_hours(duration)
        if n_hours == 0:
            continue
        raw = entry['value']
        if raw is None:
            if distribute:
                raw = 0.0
            else:
                continue
        val = raw / n_hours if distribute else raw
        key_base = dt_part[:13]
        year = int(key_base[:4])
        month = int(key_base[5:7])
        day = int(key_base[8:10])
        base_hour = int(key_base[11:13])
        for i in range(n_hours):
            hh = base_hour + i
            y, m, d = year, month, day
            while hh >= 24:
                hh -= 24
                d += 1
                if d > _days_in_month(y, m):
                    d = 1
                    m += 1
                    if m > 12:
                        m = 1
                        y += 1
            yield f"{y:04}-{m:02}-{d:02}T{hh:02}", val


def _days_in_month(year, month):
    """Return the number of days in the given month."""
    if month == 2:
        if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
            return 29
        return 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _parse_utc_key(start_time):
    """Parse local time with offset to UTC hour key.

    Args:
        start_time: ISO 8601 string like "2026-03-22T19:00:00-04:00"

    Returns:
        UTC hour key like "2026-03-22T23"

    Only handles whole-hour UTC offsets (sufficient for NOAA US data)."""
    date_hour = start_time[:13]
    tz_part = start_time[19:]

    sign = 1 if tz_part[0] == '+' else -1
    tz_hour = int(tz_part[1:3])

    year = int(date_hour[:4])
    month = int(date_hour[5:7])
    day = int(date_hour[8:10])
    hour = int(date_hour[11:13])

    utc_hour = hour - sign * tz_hour

    if utc_hour >= 24:
        utc_hour -= 24
        day += 1
        if day > _days_in_month(year, month):
            day = 1
            month += 1
            if month > 12:
                month = 1
                year += 1
    elif utc_hour < 0:
        utc_hour += 24
        day -= 1
        if day < 1:
            month -= 1
            if month < 1:
                month = 12
                year -= 1
            day = _days_in_month(year, month)

    return f"{year:04}-{month:02}-{day:02}T{utc_hour:02}"


def _add_days(date_str, days):
    """Add days to a date string 'YYYY-MM-DD', handling month/year rollovers."""
    year, month, day = map(int, date_str.split('-'))

    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
        days_in_month[1] = 29

    day += days

    while day > days_in_month[month - 1]:
        day -= days_in_month[month - 1]
        month += 1
        if month > 12:
            month = 1
            year += 1
            days_in_month[1] = 29 if ((year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)) else 28

    while day < 1:
        month -= 1
        if month < 1:
            month = 12
            year -= 1
            days_in_month[1] = 29 if ((year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)) else 28
        day += days_in_month[month - 1]

    return f"{year:04}-{month:02}-{day:02}"


def _parse_iso_duration_hours(duration):
    """Convert an ISO 8601 duration like 'PT6H' or 'P4DT20H' to total hours."""
    hours = 0
    rest = duration[1:]  # strip leading 'P'
    if 'D' in rest:
        day_part, rest = rest.split('D')
        hours += int(day_part) * 24
    if rest.startswith('T'):
        rest = rest[1:]
    if rest.endswith('H'):
        hours += int(rest[:-1])
    return hours



class Hour:
    """One hour of forecast data: temperature, precipitation, snow fraction, and QPF."""

    def __init__(self):
        self.start = None
        self.end = None
        self.is_daytime = None
        self.temperature = None
        self.precipitation = None
        self.snow_fraction = 0.0
        self.qpf_mm = 0.0
        self.forecast = None


class GriddataRecord:
    """Raw per-hour data from the griddata endpoint, before merging with hourly data."""

    def __init__(self):
        self.temperature = None   # °F int converted from °C, or None if not present
        self.qpf_mm = 0.0
        self.snow_fraction = 0.0  # raw; snow hint is applied in Station.hourly


def _parse_griddata_temperature(values, store, first_key, last_key):
    """Parse a griddata temperature time series into store (utc_key → GriddataRecord).

    Values are point measurements in °C, converted to °F and rounded to int.
    Skips hours outside [first_key, last_key]; first window wins on overlaps."""
    seen = set()
    for hour_key, temp_c in _iter_time_series(values, distribute=False):
        if hour_key < first_key:
            continue
        if hour_key > last_key:
            break
        if hour_key in seen:
            continue
        seen.add(hour_key)
        temp_f = round(temp_c * 9 / 5 + 32)
        store.setdefault(hour_key, GriddataRecord()).temperature = temp_f
        print(f"  {hour_key[11:13]}:00  {temp_f}°")


def _parse_griddata_qpf(values, store, first_key, last_key):
    """Parse a griddata QPF time series into store (utc_key → GriddataRecord).

    Values are totals distributed evenly across each window's hours (mm liquid).
    Skips hours outside [first_key, last_key]; first window wins on overlaps."""
    seen = set()
    for hour_key, qpf_val in _iter_time_series(values):
        if hour_key < first_key:
            continue
        if hour_key > last_key:
            break
        if hour_key in seen:
            continue
        seen.add(hour_key)
        store.setdefault(hour_key, GriddataRecord()).qpf_mm = qpf_val
        print(f"  {hour_key[11:13]}:00  {store[hour_key].qpf_mm:.2f}mm")


def _parse_griddata_snowfall(values, store, first_key, last_key):
    """Parse a griddata snowfall time series into store (utc_key → GriddataRecord).

    Converts snowfall (mm) to a snow_fraction using a 10:1 snow-to-liquid ratio,
    then sets it on the matching GriddataRecord if QPF is already populated.
    Skips hours outside [first_key, last_key]; first window wins on overlaps."""
    seen = set()
    for hour_key, snow_val in _iter_time_series(values):
        if hour_key < first_key:
            continue
        if hour_key > last_key:
            break
        if hour_key in seen:
            continue
        seen.add(hour_key)
        if snow_val > 0:
            gd = store.get(hour_key)
            if gd is not None:
                liquid = snow_val / 10.0  # 10:1 snow-to-liquid ratio
                gd.snow_fraction = min(1.0, liquid / gd.qpf_mm) if gd.qpf_mm > 0 else 1.0
                print(f"  {hour_key[11:13]}:00  {gd.qpf_mm:.2f}mm  {gd.snow_fraction * 100:.0f}% snow")


def _parse_max_age(cache_control):
    """Return the max-age integer from a Cache-Control header string, or None.

    Parses values like 'public, max-age=2329, s-maxage=3600'.
    Returns None when the header is absent or contains no max-age directive.
    """
    for part in cache_control.split(','):
        part = part.strip()
        if part.startswith('max-age='):
            try:
                return int(part[8:])
            except ValueError:
                pass
    return None


class Station:
    """Weather station metadata and forecast data for a location."""

    def __init__(self, config):
        """Initialize station with API endpoints from config."""

        self.gridpoint_api = config['GRIDPOINT_API']
        self.historical_api = config['HISTORICAL_API']
        self.configured_lat = config.get('LATITUDE')
        self.configured_lon = config.get('LONGITUDE')
        self.history_years = int(config.get('HISTORY_YEARS', HISTORY_YEARS_DEFAULT))

        self.tz = None
        self.city = None
        self.state = None
        self.lat = None
        self.lon = None
        self.location = None

        self.station_id = None
        self.unsupported = False

        self.station_list_url = None
        self.station_url = None
        self.hourly_url = None
        self.griddata_url = None

        self._hourly_store = OrderedDict()   # pure hourly: start, end, temperature, precipitation, forecast
        self._griddata_store = {}            # pure griddata: keyed by utc_key → GriddataRecord
        self._hourly_store_parsed_at = None  # wall-clock epoch of last full _hourly_store parse
        self.hourly_model_updated = None     # NOAA updateTime from last successful hourly fetch
        self.hourly_expires = None           # UTC epoch when the NOAA hourly cache window closes
        self.griddata_model_updated = None   # NOAA updateTime from last successful griddata fetch
        self.griddata_expires = None         # UTC epoch when the NOAA griddata cache window closes
        # 4-slot circular buffer: [today, tomorrow, day-after, three-days-ahead]
        # None = not yet fetched
        self.historical = [None, None, None, None]

        # All-time temperature range fetched by get_temp_range() when AUTO_SCALE
        # is enabled. None until successfully fetched (or a computed fallback is
        # applied). temp_range_is_fallback is True when the value was computed
        # from historical slots or hard defaults rather than from ACIS — the
        # scheduler retries every loop until it gets a real ACIS result.
        self.temp_min = None
        self.temp_max = None
        self.temp_range_is_fallback = False

    def geolocate(self):
        """Set location from configured latitude and longitude.

        Latitude and longitude are required and must be set via the setup
        portal. If either is missing, location remains None and the scheduler
        will keep retrying on subsequent loop iterations."""
        if self.configured_lat and self.configured_lon:
            print(f"Using configured location: {self.configured_lat}, {self.configured_lon}")
            self.lat = self.configured_lat
            self.lon = self.configured_lon
            self.location = f"{self.lat},{self.lon}"
        else:
            print("No location configured — enter latitude and longitude via the setup portal")

    # Generous bounding box covering all 50 US states (including Alaska and
    # Hawaii).  Anything outside is definitively unsupported; locations inside
    # but outside actual NOAA coverage (e.g. Canada) fall through to the
    # retry-based check in get_station().
    US_LAT_MIN = 17
    US_LAT_MAX = 72
    US_LON_MIN = -180
    US_LON_MAX = -64

    def check_bounds(self):
        """Quick bounding-box check for plausible US coordinates."""
        try:
            lat = float(self.lat)
            lon = float(self.lon)
        except (TypeError, ValueError):
            return
        if not (self.US_LAT_MIN <= lat <= self.US_LAT_MAX and
                self.US_LON_MIN <= lon <= self.US_LON_MAX):
            print(f"Location {lat},{lon} is outside US bounding box")
            self.unsupported = True

    def get_station(self):
        """Fetch NOAA station metadata and forecast URLs for this location."""

        try:
            i = 0
            while not self.griddata_url or not self.hourly_url:

                if self._get_point_info():
                    break
                i += 1

                if i >= MAX_RETRIES:
                    print(f"Can't get information for {self.lat},{self.lon}")
                    return
                if not network.has_budget(min_budget_s=NOAA_METADATA_MIN_BUDGET_SECONDS):
                    print("Budget exhausted in get_station() — will retry next iteration")
                    return
                sleep(RETRY_DELAY_SECONDS)

            i = 0
            while self.station_list_url and not self.station_url:

                if self._get_station_url():
                    break
                i += 1
                if i >= MAX_RETRIES:
                    print(f"Can't get station from {self.station_list_url}")
                    break
                if not network.has_budget(min_budget_s=NOAA_METADATA_MIN_BUDGET_SECONDS):
                    print("Budget exhausted in get_station() — will retry next iteration")
                    break
                sleep(RETRY_DELAY_SECONDS)

        except RuntimeError as err:
            print(f"Error fetching station info: {err}")

    def rotate_historical(self, today):
        """Rotate the circular buffer when the date has changed.

        On a normal date advance, shifts slots left: old tomorrow becomes
        today, old day-after becomes tomorrow, old three-days-ahead becomes
        day-after, and the new three-days-ahead slot is cleared to None for
        a fresh fetch. If the device was off for multiple days (or any slot
        is from a non-consecutive date), all slots are cleared.
        """
        slot0 = self.historical[0]
        if slot0 is None:
            return

        if slot0['date'] == today:
            return

        # Check whether old tomorrow is the new today (normal single-day advance)
        slot1 = self.historical[1]
        if slot1 is not None and slot1['date'] == today:
            print("It's a new day — rotating historical buffer.")
            self.historical[0] = self.historical[1]
            self.historical[1] = self.historical[2]
            self.historical[2] = self.historical[3]
            self.historical[3] = None
        else:
            print("It's a new day — date skipped or buffer stale, clearing historical.")
            self.historical = [None, None, None, None]

    def get_historical_day(self, slot_index, today):
        """Fetch historical baseline for one forecast day and store in the given slot.

        Queries PRISM climate data using a 3-day window centered on the target
        day; the number of years is self.history_years (from HISTORY_YEARS config,
        default 10). ACIS duration:3 looks backward, so the anchor date is
        set to target_day+1 so that the window covers {target_day-1, target_day,
        target_day+1}.

        Slot 0 = today, slot 1 = tomorrow, slot 2 = day-after-tomorrow,
        slot 3 = three days ahead.
        On success, stores a dict into self.historical[slot_index] and returns
        it. On any failure, leaves the slot as None and returns None."""

        if not self.lat or not self.lon:
            print("Need latitude and longitude to get historical data")
            return None

        target_date = _add_days(today, slot_index)

        # Anchor one day ahead so duration:3 covers {target-1, target, target+1}
        anchor = _add_days(target_date, 1)
        (ayear, amonth, aday) = anchor.split("-")

        # PRISM grid 21 (4 km resolution)
        sdate = f"{int(ayear)-self.history_years}-{amonth}-{aday}"
        edate = f"{int(ayear)-1}-{amonth}-{aday}"

        querydata = {"loc": f"{self.lon},{self.lat}",
                     "grid": "21",
                     "sdate": sdate,
                     "edate": edate,
                     "elems": [
                         {"name":"mint","interval":[1,0,0],"duration":3,"reduce":"min",
                          "smry":[{"reduce":"min"}],"smry_only":"1","units":"degreeF"},
                         {"name":"mint","interval":[1,0,0],"duration":3,"reduce":"mean",
                          "smry":[{"reduce":"mean"}],"smry_only":"1","units":"degreeF"},
                         {"name":"maxt","interval":[1,0,0],"duration":3,"reduce":"max",
                          "smry":[{"reduce":"max"}],"smry_only":"1","units":"degreeF"},
                         {"name":"maxt","interval":[1,0,0],"duration":3,"reduce":"mean",
                          "smry":[{"reduce":"mean"}],"smry_only":"1","units":"degreeF"},
                     ],
                     "output":"json"
                    }

        print(f"Fetching historical baseline slot {slot_index} ({target_date})...")
        json_data = network.request("POST", self.historical_api, querydata,
                                    min_budget_s=ACIS_HISTORICAL_DAY_MIN_BUDGET_SECONDS)

        if not json_data:
            return None

        try:
            summary = json_data['smry']
            slot = {
                'date':    target_date,
                'low':     float(summary[0]),
                'ave-low': float(summary[1]),
                'high':    float(summary[2]),
                'ave-high':float(summary[3]),
            }
        except (KeyError, IndexError, ValueError, TypeError):
            print(f"Failed to parse historical data for slot {slot_index}.")
            return None

        self.historical[slot_index] = slot
        print(f"Historical baseline for {slot['date']} (3-day window, {self.history_years}-year PRISM):")
        print("           |  Low | High")
        print("-----------|------|------")
        print(f"Record     | {slot['low']:4.0f} | {slot['high']:4.0f}")
        print(f"Average    | {slot['ave-low']:4.0f} | {slot['ave-high']:4.0f}")
        return slot

    def _fetch_temp_range(self, sdate, edate):
        """POST one ACIS GridData request for the given date range and return
        ``(temp_min, temp_max)`` as integers on success, or ``None`` on any
        failure (network error, bad JSON, -999 sentinel, non-physical values,
        or a span too narrow to be useful).  Does not mutate ``self``."""

        querydata = {
            "loc":    f"{self.lon},{self.lat}",
            "grid":   "21",
            "sdate":  sdate,
            "edate":  edate,
            "elems":  [
                {"name": "mint", "smry": [{"reduce": "min"}],
                 "smry_only": "1", "units": "degreeF"},
                {"name": "maxt", "smry": [{"reduce": "max"}],
                 "smry_only": "1", "units": "degreeF"},
            ],
            "output": "json",
        }

        print(f"Fetching all-time temperature range (PRISM {sdate} – {edate})...")
        json_data = network.request("POST", self.historical_api, querydata,
                                    min_budget_s=ACIS_TEMP_RANGE_MIN_BUDGET_SECONDS)

        if not json_data:
            return None

        try:
            summary = json_data['smry']
            temp_min = int(round(float(summary[0])))
            temp_max = int(round(float(summary[1])))
        except (KeyError, IndexError, ValueError, TypeError):
            print("Failed to parse temperature range response.")
            return None

        # Sanity check: PRISM uses -999 as a missing-data sentinel; other
        # clearly non-physical values indicate a bad response. All-time US
        # extremes are roughly -80°F (Rogers Pass MT) and 134°F (Death Valley).
        # Bounds of -150/+160 give generous headroom while catching sentinels.
        # Also reject spans < 32°F — one degree per pixel (the display is
        # 32px tall), the minimum useful scale — same floor the portal enforces.
        if not (-150 <= temp_min <= 160 and -150 <= temp_max <= 160):
            print(f"Temperature range sanity check failed "
                  f"({temp_min}°F – {temp_max}°F) — possible missing-data sentinel.")
            return None
        if temp_max - temp_min < 32:
            print(f"Temperature range too narrow ({temp_min}°F – {temp_max}°F) — skipping.")
            return None

        return (temp_min, temp_max)

    def get_temp_range(self):
        """Fetch all-time temperature range for this location from ACIS PRISM data.

        Queries RCC ACIS GridData (PRISM grid 21, 4 km resolution) for the
        all-time record low (mint) and record high (maxt) over the full PRISM
        record from 1981 to the most recent reliable date.

        Three end-dates are tried in order, stopping at the first success:

        1. Today − 3 days — freshest possible data; the 3-day buffer avoids
           PRISM "early" near-real-time sentinels (-999) for unprocessed dates.
        2. December 31 of last year — a fully stable, well-processed PRISM year;
           used when PRISM hasn't been updated recently (e.g. due to agency
           disruptions).
        3. 2025-12-31 — hardcoded last known-good year as a final ACIS attempt
           before giving up entirely.

        Duplicate end-dates (e.g. both #2 and #3 resolve to the same string
        in the year the hardcoded date was current) are skipped.

        On success, stores the results as integer °F in ``self.temp_min`` and
        ``self.temp_max``, sets ``self.temp_range_is_fallback = False``, and
        returns ``(temp_min, temp_max)``.  On total failure, leaves both
        attributes unchanged and returns ``None`` so the caller can apply a
        computed fallback scale."""

        if not self.lat or not self.lon:
            print("Need latitude and longitude to get temperature range!")
            return None

        now = localtime()
        year = now.tm_year
        today = f"{year}-{now.tm_mon:02d}-{now.tm_mday:02d}"

        candidate_edates = [
            _add_days(today, -3),       # preferred: freshest PRISM data
            f"{year - 1}-12-31",        # stable: end of last full year
            "2025-12-31",               # hardcoded: last known-good year
        ]

        # Skip duplicate end-dates (common when year == 2026 and both #2/#3
        # resolve to "2025-12-31") to avoid redundant network requests.
        seen = set()
        for edate in candidate_edates:
            if edate in seen:
                continue
            seen.add(edate)

            result = self._fetch_temp_range("1981-01-01", edate)
            if result is not None:
                self.temp_min, self.temp_max = result
                self.temp_range_is_fallback = False
                print(f"Auto-scale: {self.temp_min}°F – {self.temp_max}°F "
                      f"(ACIS PRISM 1981-01-01 – {edate})")
                return result

        print("All ACIS date-range attempts failed — caller will apply fallback scale.")
        return None

    def compute_fallback_range(self):
        """Return the hard-default temperature scale as a fallback when ACIS is unreachable."""
        from appconfig import DEFAULTS
        return (DEFAULTS['TEMP_MIN'], DEFAULTS['TEMP_MAX'])

    @property
    def hourly(self):
        """Dynamically merge hourly and griddata stores into a combined OrderedDict of Hours.

        Temperature is taken from griddata when griddata_model_updated is more
        recent than hourly_model_updated (ISO-8601 UTC strings compare
        lexicographically). QPF and snow fraction always come from griddata when
        available. The snow hint (_apply_snow_hint) is applied here where both
        h.forecast (hourly) and h.snow_fraction (griddata) are present.

        Returns a fresh OrderedDict on every call — no result is cached.
        """
        griddata_fresher = (
            self.griddata_model_updated is not None
            and self.hourly_model_updated is not None
            and self.griddata_model_updated > self.hourly_model_updated
        )
        result = OrderedDict()
        for utc_key, hr in self._hourly_store.items():
            h = Hour()
            h.start         = hr.start
            h.end           = hr.end
            h.is_daytime    = hr.is_daytime
            h.precipitation = hr.precipitation
            h.forecast      = hr.forecast
            gd = self._griddata_store.get(utc_key)
            h.temperature = (
                gd.temperature
                if (griddata_fresher and gd is not None and gd.temperature is not None)
                else hr.temperature
            )
            if gd is not None:
                h.qpf_mm       = gd.qpf_mm
                h.snow_fraction = gd.snow_fraction
            _apply_snow_hint(h)
            result[utc_key] = h
        return result

    @property
    def hourly_update_age(self):
        """Seconds since NOAA last updated the hourly forecast model, or None if unknown.

        Parses the UTC ISO-8601 ``updateTime`` field stored in
        ``hourly_model_updated`` (e.g. ``"2026-05-12T10:00:00+00:00"``) and
        subtracts it from the current epoch time.  CircuitPython has no timezone
        support — the RTC runs in UTC, so ``mktime()`` and ``time()`` both
        produce UTC epochs and the subtraction is exact.  In the CPython sim,
        ``sim_stubs`` patches ``time.mktime`` to ``calendar.timegm`` for the
        same behavior.  Returns ``None`` when no forecast has been fetched yet.
        """
        if not self.hourly_model_updated:
            return None
        t = self.hourly_model_updated
        update_epoch = mktime(struct_time((
            int(t[0:4]), int(t[5:7]), int(t[8:10]),
            int(t[11:13]), int(t[14:16]), int(t[17:19]),
            0, -1, -1,
        )))
        return _time() - update_epoch

    @property
    def griddata_update_age(self):
        """Seconds since NOAA last updated the griddata model, or None if unknown.

        Identical in structure to ``hourly_update_age`` but reads from
        ``griddata_model_updated``.  Returns ``None`` when no griddata has been
        fetched yet.
        """
        if not self.griddata_model_updated:
            return None
        t = self.griddata_model_updated
        update_epoch = mktime(struct_time((
            int(t[0:4]), int(t[5:7]), int(t[8:10]),
            int(t[11:13]), int(t[14:16]), int(t[17:19]),
            0, -1, -1,
        )))
        return _time() - update_epoch

    def get_hourly_forecast(self, hours=FORECAST_HOURS):
        """Fetch hourly forecast from NOAA, storing results in self._hourly_store.

        Uses adafruit_json_stream for streaming parse so only the first
        `hours` non-expired periods are read from the socket — the remaining
        ~60% of the response body is never fetched.

        Early exit: if NOAA's updateTime is unchanged and _hourly_store was
        parsed less than HOURLY_FORCED_RELOAD_HOURS ago, the parse is skipped
        entirely and the existing store stays intact. Once the store is
        HOURLY_FORCED_RELOAD_HOURS old, a full re-parse is forced regardless
        of updateTime to keep the time window current.

        QPF and snow fraction live in self._griddata_store and are not touched
        here; they are merged into the combined view via the hourly property.
        """
        print("Getting hourly forecast...")

        update_time = None
        i = 0

        stream_ctx = network.get_stream(self.hourly_url, min_budget_s=HOURLY_MIN_BUDGET_SECONDS)
        with stream_ctx as stream:
            if stream is None:
                return None

            # Cache-Control: max-age tells us when NOAA will have fresh data.
            # Store the expiry epoch so the scheduler can skip unnecessary fetches.
            # HTTP header names are case-insensitive; adafruit_requests stores
            # them lowercase as received from the wire.
            raw_headers = stream_ctx.headers
            cc = raw_headers.get('cache-control', raw_headers.get('Cache-Control', ''))
            max_age = _parse_max_age(cc)
            if max_age is not None:
                max_age = max(max_age, FORECAST_MIN_CACHE_MINUTES * 60)
                self.hourly_expires = _time() + max_age
                _exp = localtime(int(self.hourly_expires))
                print(f"Hourly cache: next fetch after "
                      f"{_exp.tm_year}-{_exp.tm_mon:02}-{_exp.tm_mday:02}"
                      f"T{_exp.tm_hour:02}:{_exp.tm_min:02} local ({max_age}s)")

            try:
                props = stream['properties']
                update_time = props['updateTime']
            except (KeyError, TypeError):
                print("Hourly response missing properties.")
                return None

            store_age_h = (
                (_time() - self._hourly_store_parsed_at) / 3600
                if self._hourly_store_parsed_at is not None else None
            )
            if (update_time == self.hourly_model_updated
                    and store_age_h is not None
                    and store_age_h < HOURLY_FORCED_RELOAD_HOURS):
                print(f"Hourly model unchanged — skipping (store {store_age_h:.1f}h old)")
                return None   # exits with block; stream closes, socket discarded

            try:
                periods = props['periods']
            except (KeyError, TypeError):
                print("Hourly response missing periods.")
                return None

            new_store = OrderedDict()

            for period in periods:
                try:
                    h = Hour()

                    number = period['number'] - 1
                    if number != i:
                        print(f"Warning: hour {number} when {i} expected")

                    h.start = period['startTime']
                    h.end = period['endTime']
                    h.temperature = period['temperature']
                    if period['temperatureUnit'] != "F":
                        print("Warning: temperature not in Fahrenheit?")
                    if period['probabilityOfPrecipitation']['unitCode'] != "wmoUnit:percent":
                        print("Warning: probability of precipitation not in percent?")
                    h.precipitation = period['probabilityOfPrecipitation']['value'] or 0
                    h.forecast = period['shortForecast']

                    utc_key = _parse_utc_key(h.start)
                    print(f"  {h.start[11:16]}  {h.temperature:3}°  {h.precipitation:3}%  {h.forecast}")
                    new_store[utc_key] = h
                except (KeyError, TypeError, ValueError) as e:
                    print(f"Warning: skipping malformed period {i}: {e}")
                    continue

                i += 1
                if i >= hours:
                    break
            # Socket closes here; unread periods are discarded.
            # Note: NOAA always clips the response to start at the current hour,
            # so no expired periods are expected here. The display's own
            # hour.end < current_time guard handles any rare exceptions.

        self._hourly_store = new_store
        self._hourly_store_parsed_at = _time()
        prev_hourly_model_updated = self.hourly_model_updated
        self.hourly_model_updated = update_time
        age_s = self.hourly_update_age
        age_str = f"{int(age_s // 60)}m old" if age_s is not None else "age unknown"
        print(f"Hourly forecast model: {self.hourly_model_updated} ({age_str})")
        if update_time != prev_hourly_model_updated and age_s is not None:
            print(f"  New model — fetched {int(age_s // 60)}m after publish")

        if age_s is not None and age_s > STALE_THRESHOLD_MINUTES * 60:
            stale_cap = _time() + STALE_MAX_CACHE_MINUTES * 60
            if self.hourly_expires is None or self.hourly_expires > stale_cap:
                self.hourly_expires = stale_cap
                print(f"Hourly model is {int(age_s // 60)}m old — "
                      f"capping cache to {STALE_MAX_CACHE_MINUTES}m")

        # DEBUG: show hours where griddata temperature differs from hourly.
        # Remove once temperature fallback is confirmed working in the field.
        if self._griddata_store:
            griddata_fresher = (
                self.griddata_model_updated is not None
                and self.griddata_model_updated > self.hourly_model_updated
            )
            label = "griddata FRESHER — would use gd" if griddata_fresher else "hourly fresher — using hourly"
            diffs = 0
            for utc_key, hr in self._hourly_store.items():
                gd = self._griddata_store.get(utc_key)
                if gd is not None and gd.temperature is not None and gd.temperature != hr.temperature:
                    print(f"  Temp diff {utc_key[11:13]}:00  hourly={hr.temperature}°  gd={gd.temperature}°  ({label})")
                    diffs += 1
            if diffs:
                print(f"  {diffs} hours with differing temperatures")

        mem_before = gc.mem_free()
        gc.collect()
        print(f"  GC freed {network.fmt_bytes(gc.mem_free() - mem_before)}  ({network.fmt_bytes(gc.mem_free())} free)")
        return i

    def get_griddata(self):
        """Fetch QPF, snowfall, and temperature from NOAA griddata.

        Parses the forward-only griddata JSON stream, storing results in a
        fresh self._griddata_store (keyed by UTC hour key → GriddataRecord).
        Replacing the store on each call prunes keys outside the current window.

        Temperature is converted from °C to °F. Snow fraction is computed from
        QPF and snowfall using a 10:1 snow-to-liquid ratio. The snow hint
        (_apply_snow_hint) is applied in the hourly property where both
        h.forecast and h.snow_fraction are available.

        Uses adafruit_json_stream so only the four target properties
        (updateTime, temperature, quantitativePrecipitation, snowfallAmount)
        are materialized. The remaining ~60 properties are skipped byte-by-byte
        without Python object allocation. get_hourly_forecast() must be called
        first; the fetch is skipped when self._hourly_store is empty."""

        if not self.griddata_url:
            print("No griddata URL available")
            return

        if not self._hourly_store:
            print("No hourly forecast to populate with griddata")
            return

        print("Getting grid data...")

        stream_ctx = network.get_stream(self.griddata_url, min_budget_s=GRIDDATA_MIN_BUDGET_SECONDS)
        with stream_ctx as stream:
            if stream is None:
                return

            raw_headers = stream_ctx.headers
            cc = raw_headers.get('cache-control', raw_headers.get('Cache-Control', ''))
            max_age = _parse_max_age(cc)
            if max_age is not None:
                max_age = max(max_age, FORECAST_MIN_CACHE_MINUTES * 60)
                self.griddata_expires = _time() + max_age
                _exp = localtime(int(self.griddata_expires))
                print(f"Griddata cache: next fetch after "
                      f"{_exp.tm_year}-{_exp.tm_mon:02}-{_exp.tm_mday:02}"
                      f"T{_exp.tm_hour:02}:{_exp.tm_min:02} local ({max_age}s)")

            try:
                props = stream['properties']
            except (KeyError, TypeError):
                print("Griddata response missing properties.")
                return

            update_time = None
            _found = set()
            _meta_elev = _meta_office = _meta_grid_id = _meta_gx = _meta_gy = None

            # Iterate all properties in stream order. NOAA's production response
            # has a fixed ordering: updateTime at position 2, temperature at ~4,
            # QPF at ~26, snowfall at ~28. Unrecognized keys are skipped
            # byte-by-byte without Python object allocation. The break at 4 found
            # discards the remaining ~60 properties without reading them.
            #
            # A fresh new_store is built during the parse and atomically replaces
            # self._griddata_store only on success, so a failed fetch leaves the
            # previous store intact.
            #
            # The uom check from the previous implementation is omitted: accessing
            # ['uom'] when absent exhausts the sub-object (forward-only stream),
            # making a safe try/except around both uom and values impossible without
            # a second level of iteration. The check was diagnostic-only.
            new_store = {}

            first_key = next(iter(self._hourly_store))
            last_key = first_key
            for k in self._hourly_store:
                last_key = k

            for key in props:
                if key == 'updateTime':
                    update_time = props[key]
                    if update_time == self.griddata_model_updated:
                        print("Griddata model unchanged — skipping")
                        break   # exits loop; stream closes after the with block
                    _found.add(key)
                elif key == 'validTimes':
                    print(f"  Grid product validity: {props[key]}")
                elif key == 'elevation':
                    _meta_elev = props[key]['value']
                elif key == 'forecastOffice':
                    _meta_office = props[key]  # noqa: F841 — consumed to advance stream
                elif key == 'gridId':
                    _meta_grid_id = props[key]
                elif key == 'gridX':
                    _meta_gx = props[key]
                elif key == 'gridY':
                    _meta_gy = props[key]
                    _elev = f"{_meta_elev:.1f} m" if _meta_elev is not None else '?'
                    print(f"  Grid: {_meta_grid_id} ({_meta_gx},{_meta_gy})  Elevation: {_elev}")
                elif key == 'temperature':
                    try:
                        _parse_griddata_temperature(props[key]['values'], new_store, first_key, last_key)
                    except KeyError:
                        pass
                    _found.add(key)
                elif key == 'quantitativePrecipitation':
                    try:
                        _parse_griddata_qpf(props[key]['values'], new_store, first_key, last_key)
                    except KeyError:
                        pass
                    _found.add(key)
                elif key == 'snowfallAmount':
                    try:
                        _parse_griddata_snowfall(props[key]['values'], new_store, first_key, last_key)
                    except KeyError:
                        pass
                    _found.add(key)
                elif not key.startswith("@"):
                    print(f"  Skipping {key} (not visualized)")
                if len(_found) == 4:
                    break  # remaining properties discarded on socket close

        # Stream is closed here; all remaining bytes are discarded.

        if update_time is None:
            print("Griddata response missing updateTime.")
            return

        if update_time == self.griddata_model_updated:
            return   # early break was taken; existing store stays intact

        self._griddata_store = new_store
        prev_griddata_model_updated = self.griddata_model_updated
        self.griddata_model_updated = update_time
        age_s = self.griddata_update_age
        age_str = f"{int(age_s // 60)}m old" if age_s is not None else "age unknown"
        print(f"Populated griddata for {len(self._griddata_store)} hours")
        print(f"Grid data last updated at {self.griddata_model_updated} ({age_str})")
        if update_time != prev_griddata_model_updated and age_s is not None:
            print(f"  New model — fetched {int(age_s // 60)}m after publish")

        if age_s is not None and age_s > STALE_THRESHOLD_MINUTES * 60:
            stale_cap = _time() + STALE_MAX_CACHE_MINUTES * 60
            if self.griddata_expires is None or self.griddata_expires > stale_cap:
                self.griddata_expires = stale_cap
                print(f"Griddata model is {int(age_s // 60)}m old — "
                      f"capping cache to {STALE_MAX_CACHE_MINUTES}m")

        mem_before = gc.mem_free()
        gc.collect()
        print(f"  GC freed {network.fmt_bytes(gc.mem_free() - mem_before)}  ({network.fmt_bytes(gc.mem_free())} free)")

    def _get_point_info(self):
        """Query NOAA points endpoint to discover forecast URLs for this location."""

        print("Finding weather office...")
        json_data = network.request("GET", f"{self.gridpoint_api}/{self.lat},{self.lon}",
                                    min_budget_s=NOAA_METADATA_MIN_BUDGET_SECONDS)
        if not json_data:
            return

        properties = json_data.get('properties')
        if not properties:
            print("Warning: NOAA points response missing 'properties' — unexpected format")
            return

        if not self.hourly_url:
            self.hourly_url = properties.get('forecastHourly')
            if not self.hourly_url:
                print("Warning: NOAA points response missing 'forecastHourly'")

        if not self.griddata_url:
            self.griddata_url = properties.get('forecastGridData')
            if not self.griddata_url:
                print("Warning: NOAA points response missing 'forecastGridData'")

        if not self.station_list_url:
            self.station_list_url = properties.get('observationStations')
            if not self.station_list_url:
                print("Warning: NOAA points response missing 'observationStations'")

        if not self.city or not self.state:
            rel = properties.get('relativeLocation', {}).get('properties', {})
            self.city = rel.get('city')
            self.state = rel.get('state')
            if not self.city or not self.state:
                print("Warning: NOAA points response missing relativeLocation city/state")

        station_tz = properties.get('timeZone')
        if station_tz:
            if self.tz and self.tz != station_tz:
                print(f"Warning: GeoIP timezone ({self.tz}) differs from station timezone ({station_tz})")
            if not self.tz:
                self.tz = station_tz
                print(f"Station timezone is {self.tz}")
        else:
            print("Warning: NOAA points response missing 'timeZone'")

        print(f"Location: {self.city}, {self.state}")
        print(f"Observation stations: {self.station_list_url}")
        print(f"Hourly forecast: {self.hourly_url}")
        print(f"Grid data: {self.griddata_url}")
        return True

    def _get_station_url(self):
        """Get first observation station from NOAA station list for this location.

        Leaves self.station_url / self.station_id unset on any failure so that
        the caller loop in get_station() retries, and _ensure_station() retries
        each scheduler loop until a station is found."""

        print("Getting local station...")
        json_data = network.request("GET", self.station_list_url + "?limit=1",
                                    min_budget_s=NOAA_METADATA_MIN_BUDGET_SECONDS)
        if not json_data:
            return

        try:
            self.station_url = json_data['features'][0]['id']
        except (KeyError, IndexError):
            print("Couldn't get station information from station list features.")

        if not self.station_url:
            try:
                self.station_url = json_data['observationStations'][0]
            except (KeyError, IndexError):
                print("Couldn't get station information from observationStations, either.")

        if self.station_url:
            self.station_id = self.station_url.split('/')[-1]
            print(f"Station: {self.station_url}")
