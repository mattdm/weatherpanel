"""Weather data retrieval from NOAA Weather API and RCC ACIS historical grids.

Fetches hourly forecasts, gridpoint QPF/snowfall data, and n-year historical
temperature baselines for display color-coding.

Historical baselines are stored in a 3-slot circular buffer — one slot per
forecast day (today, tomorrow, day-after-tomorrow). Each slot is fetched with
a single ACIS call and rotated at midnight so only the new day-after slot
needs a fresh fetch.
"""
import gc
from time import sleep

import network

MAX_RETRIES = 7
RETRY_DELAY_S = 5
FORECAST_HOURS = 65
HISTORY_YEARS_DEFAULT = 10

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
    """Add days to a date string 'YYYY-MM-DD', handling month/year rollovers.

    Simple implementation for forecast windows (max ±400 days)."""
    year, month, day = map(int, date_str.split('-'))

    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
        days_in_month[1] = 29

    day += days

    iterations = 0
    while day > days_in_month[month - 1]:
        day -= days_in_month[month - 1]
        month += 1
        if month > 12:
            month = 1
            year += 1
            days_in_month[1] = 29 if ((year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)) else 28
        iterations += 1
        if iterations > 400:
            raise ValueError(f"_add_days: too many iterations (day={day})")

    while day < 1:
        month -= 1
        if month < 1:
            month = 12
            year -= 1
            days_in_month[1] = 29 if ((year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)) else 28
        day += days_in_month[month - 1]
        iterations += 1
        if iterations > 400:
            raise ValueError(f"_add_days: too many iterations (day={day})")

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


def _expand_time_series(values):
    """Expand NOAA griddata multi-hour time series into per-hour dict.

    Each entry has a validTime like "2026-04-20T06:00:00+00:00/PT6H" and a
    value. Distributes the value evenly across the duration's hours, keyed
    by UTC hour string like "2026-04-20T06".

    When windows overlap (can occur during NOAA forecast updates), the
    earlier entry's value is kept."""
    by_hour = {}
    for entry in values:
        valid_time = entry['validTime']
        dt_part, duration = valid_time.split('/')
        key = dt_part[:13]
        n_hours = _parse_iso_duration_hours(duration)
        if n_hours == 0:
            continue
        val = entry['value'] or 0.0

        year = int(key[:4])
        month = int(key[5:7])
        day = int(key[8:10])
        base_hour = int(key[11:13])

        for i in range(n_hours):
            h = base_hour + i
            y, m, d = year, month, day
            while h >= 24:
                h -= 24
                d += 1
                if d > _days_in_month(y, m):
                    d = 1
                    m += 1
                    if m > 12:
                        m = 1
                        y += 1
            hour_key = f"{y:04}-{m:02}-{d:02}T{h:02}"
            by_hour.setdefault(hour_key, val / n_hours)
    return by_hour


def _print_historical_slot(slot, history_years=HISTORY_YEARS_DEFAULT):
    """Print a formatted table of one historical baseline slot."""
    print(f"Historical baseline for {slot['date']} (3-day window, {history_years}-year PRISM):")
    print("           |  Low | High")
    print("-----------|------|------")
    print(f"Record     | {slot['low']:4.0f} | {slot['high']:4.0f}")
    print(f"Average    | {slot['ave-low']:4.0f} | {slot['ave-high']:4.0f}")


class Hour():
    """One hour of forecast data: temperature, precipitation, snow fraction."""
    def __init__(self):
        self.start= None
        self.end = None
        self.is_daytime = None
        self.temperature = None
        self.precipitation = None
        self.snow_fraction = None
        self.forecast = None

class Station():
    """Weather station metadata and forecast data for a location."""

    def __init__(self,config):
        """Initialize station with API endpoints from config."""

        self.geolocation_api = config['GEOLOCATION_API']
        self.gridpoint_api = config['GRIDPOINT_API']
        self.historical_api = config['HISTORICAL_API']
        self.configured_lat = config.get('LATITUDE')
        self.configured_lon = config.get('LONGITUDE')
        self.history_years = int(config.get('HISTORY_YEARS', HISTORY_YEARS_DEFAULT))

        self.tz=None
        self.city=None
        self.state=None
        self.lat=None
        self.lon=None
        self.location=None

        self.station_id=None
        self.unsupported=False

        self.station_list_url=None
        self.station_url=None
        self.forecast_url=None
        self.hourly_url=None
        self.griddata_url=None

        # observations at the station are less useful than gridpoint data, even if there are just forecasts
        self.observations=None
        self.observations_updated=None
        self.hourly=[]
        self.hourly_updated=None
        self.griddata_updated=None
        self.forecast=None
        self.forecast_updated=None
        # 4-slot circular buffer: [today, tomorrow, day-after, three-days-ahead]
        # None = not yet fetched
        self.historical=[None, None, None, None]

    def geolocate(self):
        """Determine location via configured lat/lon or IP geolocation API.

        If geolocation fails after MAX_RETRIES, location remains None and
        the scheduler will keep retrying on subsequent loop iterations."""
        if self.configured_lat and self.configured_lon:
            print(f"Using configured location: {self.configured_lat}, {self.configured_lon}")
            self.lat = self.configured_lat
            self.lon = self.configured_lon
            self.location = f"{self.lat},{self.lon}"
            return
        i = 0
        while not self.lat or not self.lon:
            print("Getting location...")
            json_data = network.get(self.geolocation_api)
            if not json_data:
                print(f"Warning: didn't get location from {self.geolocation_api}")
                if i >= MAX_RETRIES:
                    print("Geolocation failed; will retry next loop")
                    return
            else:
                if 'timezone' in json_data:
                    self.tz = json_data['timezone']
                    print(f"GeoIP timezone is {self.tz}")
                if 'lat' in json_data and 'lon' in json_data:
                    self.lat = f"{json_data['lat']:.4f}"
                    self.lon = f"{json_data['lon']:.4f}"
                    print(f"Latitude: {self.lat} Longitude {self.lon}")
                    break
            i += 1
            sleep(RETRY_DELAY_S)

        self.location = f"{self.lat},{self.lon}"

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
            i=0
            while not self.griddata_url or not self.forecast_url or not self.hourly_url:

                if self._get_point_info():
                    break
                i+=1

                if i >= MAX_RETRIES:
                    print(f"Can't get information for {self.lat},{self.lon}")
                    return
                sleep(RETRY_DELAY_S)

            i=0
            while self.station_list_url and not self.station_url:

                if self._get_station_url():
                    break
                i+=1
                if i >= MAX_RETRIES:
                    print(f"Can't get station from {self.station_list_url}")
                    break
                sleep(RETRY_DELAY_S)

        except RuntimeError as err:
            print("Error fetching station info!")
            print(err)

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
            print("Need latitude and longitude to get historical data!")
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
        json_data = network.post(self.historical_api, querydata)

        if not json_data:
            print(f"Failed to fetch historical data for slot {slot_index}.")
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
        _print_historical_slot(slot, self.history_years)
        return slot

    def get_hourly_forecast(self, hours=FORECAST_HOURS):
        """Fetch hourly forecast from NOAA, preserving existing snow_fraction data.

        Snow fractions are populated separately by get_griddata() and refreshed
        less often, so we preserve them across hourly forecast updates."""


        print("Getting hourly forecast...")
        json_data = network.get(self.hourly_url)
        if not json_data:
            print("Request failed.")
            return None

        # Validate structure before touching self.hourly so a bad response
        # doesn't destroy the last-known-good forecast.
        try:
            periods = json_data['properties']['periods']
        except (KeyError, TypeError):
            print("Hourly response missing properties/periods.")
            return None

        snow_fractions = {}
        for h in self.hourly:
            if h.snow_fraction is not None:
                snow_fractions[h.start] = h.snow_fraction

        self.hourly=[]
        i=0
        for period in periods:
            try:
                h = Hour()

                number = period['number'] - 1
                if number != i:
                    print(f"Warning: hour {number} when {i} expected!")

                h.start = period['startTime']
                h.end = period['endTime']
                h.temperature = period['temperature']
                if period['temperatureUnit'] != "F":
                    print("Warning: temperature not in Fahrenheit?")
                if period['probabilityOfPrecipitation']['unitCode'] != "wmoUnit:percent":
                    print("Warning: probability of precipitation not in percent?")
                h.precipitation = period['probabilityOfPrecipitation']['value'] or 0
                h.forecast = period['shortForecast']

                if h.start in snow_fractions:
                    h.snow_fraction = snow_fractions[h.start]

                print(f"Hour {number:02}: {h.start[:13]}–{h.end[:13]} {h.temperature:3}° {h.precipitation:3}% rain | {h.forecast}")
                self.hourly.append(h)
            except (KeyError, TypeError, ValueError) as e:
                print(f"Warning: skipping malformed period {i}: {e}")
                continue

            i += 1
            if i >= hours:
                break

        self.hourly_updated = json_data['properties']['updateTime']
        print(f"Hourly forecast last updated at {self.hourly_updated}")

        mem_before = gc.mem_free()
        gc.collect()
        print(f"  gc freed {gc.mem_free() - mem_before} bytes (mem free: {gc.mem_free()})")
        return i

    def get_griddata(self):
        """Fetch QPF and snowfall from NOAA griddata, compute snow_fraction for each hour.

        Uses 10:1 snow-to-liquid ratio to convert snowfall (mm) to liquid equivalent,
        then calculates what fraction of total precipitation will be snow vs rain."""

        if not self.griddata_url:
            print("No griddata URL available")
            return

        if not self.hourly:
            print("No hourly forecast to populate with QPF data")
            return

        print("Getting griddata QPF and snowfall...")
        json_data = network.get(self.griddata_url)
        if not json_data:
            print("Request failed.")
            return

        properties = json_data['properties']

        qpf_series  = properties.get('quantitativePrecipitation', {'values': []})
        snow_series = properties.get('snowfallAmount',            {'values': []})

        if qpf_series.get('uom', 'wmoUnit:mm') != 'wmoUnit:mm':
            print(f"Warning: QPF unit is {qpf_series['uom']}, expected wmoUnit:mm")
        if snow_series.get('uom', 'wmoUnit:mm') != 'wmoUnit:mm':
            print(f"Warning: snowfall unit is {snow_series['uom']}, expected wmoUnit:mm")

        qpf_by_hour  = _expand_time_series(qpf_series['values'])
        snow_by_hour = _expand_time_series(snow_series['values'])

        for h in self.hourly:
            utc_key = _parse_utc_key(h.start)
            qpf_mm = qpf_by_hour.get(utc_key, 0.0)
            snow_mm = snow_by_hour.get(utc_key, 0.0)

            if snow_mm > 0:
                snow_liquid_mm = snow_mm / 10.0  # 10:1 snow-to-liquid ratio
                if qpf_mm > 0:
                    h.snow_fraction = min(1.0, snow_liquid_mm / qpf_mm)
                else:
                    h.snow_fraction = 1.0
            else:
                h.snow_fraction = 0.0

        # Text-hint fallback: when griddata shows zero snowfall but the hourly
        # text forecast mentions frozen precipitation, apply a type-appropriate
        # minimum snow_fraction. The griddata snowfall series uses 6-hour windows
        # and can lag the hourly text forecast by several hours at transition
        # boundaries (e.g. "Rain And Snow Likely" hours before the first non-zero
        # snowfallAmount window). Uses max() so compound phrases like "Snow/Sleet"
        # pick the more-frozen tier rather than the first match.
        for h in self.hourly:
            if h.snow_fraction == 0.0:
                hints = [v for kw, v in SNOW_HINT_MINIMUMS.items() if kw in (h.forecast or "")]
                if hints:
                    h.snow_fraction = max(hints)

        self.griddata_updated = json_data['properties']['updateTime']
        print(f"Populated snow_fraction for {len(self.hourly)} hours")
        print(f"Griddata last updated at {self.griddata_updated}")
        mem_before = gc.mem_free()
        gc.collect()
        print(f"  gc freed {gc.mem_free() - mem_before} bytes (mem free: {gc.mem_free()})")

    def _get_point_info(self):
        """Query NOAA points endpoint to discover forecast URLs for this location."""

        print("Finding weather office...")
        json_data = network.get(f"{self.gridpoint_api}/{self.lat},{self.lon}")
        if not json_data:
            return

        properties = json_data['properties']

        if not self.forecast_url:
            try:
                self.forecast_url = properties['forecast']
            except KeyError:
                pass

        if not self.hourly_url:
            try:
                self.hourly_url = properties['forecastHourly']
            except KeyError:
                pass

        if not self.griddata_url:
            try:
                self.griddata_url = properties['forecastGridData']
            except KeyError:
                pass

        if not self.station_list_url:
            try:
                self.station_list_url = properties['observationStations']
            except KeyError:
                pass

        if not self.city or not self.state:
            try:
                loc = properties['relativeLocation']['properties']

                self.city = loc['city']
                self.state = loc['state']

            except KeyError:
                pass

        try:
            station_tz = properties['timeZone']
            if self.tz and self.tz != station_tz:
                print(f"Warning: GeoIP timezone ({self.tz}) differs from station timezone ({station_tz})")
            if not self.tz:
                self.tz = station_tz
                print(f"Station timezone is {self.tz}")
        except (KeyError, ValueError):
            pass

        print(f"Location: {self.city}, {self.state}")
        print(f"observationStations: {self.station_list_url}")
        print(f"forecast: {self.forecast_url}")
        print(f"forecastHourly: {self.hourly_url}")
        print(f"forecastGridData: {self.griddata_url}")
        return True

    def _get_station_url(self):
        """Get first observation station from NOAA station list for this location.

        Leaves self.station_url / self.station_id unset on any failure so that
        the caller loop in get_station() retries, and _ensure_station() retries
        each scheduler loop until a station is found."""

        print("Getting local station...")
        json_data = network.get(self.station_list_url + "?limit=1")
        if not json_data:
            return

        try:
            for feature in json_data['features']:
                self.station_url = feature['id']
                break
        except KeyError:
            print("Couldn't get station information from station list features.")

        if not self.station_url:
            try:
                stationlist = json_data['observationStations']
                self.station_url = stationlist[0]
            except (KeyError, IndexError):
                print("Couldn't get station information from observationStations, either.")

        if self.station_url:
            self.station_id = self.station_url.split('/')[-1]
            print(f"local Station: {self.station_url}")
