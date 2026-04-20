"""Weather data retrieval from NOAA Weather API and RCC ACIS historical grids.

Fetches hourly forecasts, gridpoint QPF/snowfall data, and 10-year historical
temperature baselines for display color-coding.
"""
from time import sleep

import adafruit_json_stream as json_stream

import network


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
    
    Simple implementation for forecast windows (max ±10 days). Does not
    validate inputs -- out-of-range day values will cause an infinite loop."""
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
    
    def copy(self):
        h = Hour()
        h.start = self.start
        h.end = self.end
        h.temperature = self.temperature
        h.precipitation = self.precipitation
        h.snow_fraction = self.snow_fraction
        h.forecast = self.forecast
        return h
    
    def blend(self,other):
        """Average two hours for time-boundary overlaps in NOAA data."""
        h = Hour()
        h.start = other.start
        h.end =  self.end
        h.temperature = round((self.temperature+other.temperature)/2)
        h.precipitation = round((self.precipitation+other.precipitation)/2)
        if self.snow_fraction is not None and other.snow_fraction is not None:
            h.snow_fraction = (self.snow_fraction + other.snow_fraction) / 2
        else:
            h.snow_fraction = self.snow_fraction or other.snow_fraction
        h.forecast = None
        return h


class Forecast():
    """Multi-hour forecast period (day/night) from NOAA."""
    def __init__(self):
        self.name = None
        self.start = None
        self.end = None
        self.night = None
        self.temperature = None
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
        self.historical={}

    def geolocate(self):
        """Determine location via configured lat/lon or IP geolocation API.

        Falls back to Somerville, MA (42.39, -71.13) after 7 failed API
        retries -- intended for dev/test, but will also fire in production
        if the geolocation API is unreachable."""
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
                if i>6:
                    # Fallback to hardcoded dev/test location after retries
                    print("Using Somerville, MA as location")
                    self.lat="42.39"
                    self.lon="-71.13"                
                    break
            else:
                if 'timezone' in json_data.keys():
                    self.tz=json_data['timezone']
                    print(f"GeoIP timezone is {self.tz}")
                if 'lat' in json_data.keys() and 'lon' in json_data.keys():
                    self.lat=f"{json_data['lat']:.4}"
                    self.lon=f"{json_data['lon']:.4}"
                    print(f"Latitude: {self.lat} Longitude {self.lon}")
                    break
            i += 1
            sleep(5)

        self.location=f"{self.lat},{self.lon}"

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

                if i>6:
                    print(f"Can't get information for {self.lat},{self.lon}")
                    self.unsupported = True
                    return
                sleep(5)          

            i=0
            while self.station_list_url and not self.station_url:

                if self._get_station_url():
                    break
                i+=1
                if i>6:
                    print(f"Can't get information for {self.station_url}")
                    break
                sleep(5)     

        except RuntimeError as err:
            print(f"Error fetching station info!")
            print(err)

    def _fetch_day_historical(self, date):
        """Fetch 10-year historical temperature stats for a single date from PRISM.
        
        Returns (low, ave_low, high, ave_high) as floats, or None on failure.
        """
        (year, month, day) = date.split("-")
        
        # PRISM grid dataset (21 = 4km resolution climate data)
        querydata = {"loc": f"{self.lon},{self.lat}",  # lon,lat order per ACIS API
                     "grid": "21",
                     "sdate": f"{int(year)-10}-{month}-{day}",
                     "edate": f"{int(year)-1}-{month}-{day}",
                     "elems": [{"name":"mint","interval":[1,0,0],"duration":1,"smry":[{"reduce":"min"},{"reduce":"mean"}],"smry_only":"1","units":"degreeF"},
                               {"name":"maxt","interval":[1,0,0],"duration":1,"smry":[{"reduce":"max"},{"reduce":"mean"}],"smry_only":"1","units":"degreeF"}
                               ],
                     "output":"json"
                    }
        
        json_data = network.post(self.historical_api, querydata)
        
        if not json_data:
            return None
        
        try:
            summary = json_data['smry']
            return (float(summary[0][0]), float(summary[0][1]),
                    float(summary[1][0]), float(summary[1][1]))
        except (KeyError, ValueError, TypeError):
            return None

    def get_historical(self, date):
        """Fetch 5-day composite historical baseline for temperature color-coding.
        
        Fetches yesterday through 3 days out, aggregates into a single baseline
        that captures seasonal norms across the full forecast window rather than
        quirks of a single historical date."""

        if not self.lat or not self.lon:
            print("Need latitude and longitude to get historical data!")
            return None

        # Fetch historical data for a 5-day window: yesterday through 3 days out
        dates = [_add_days(date, offset) for offset in range(-1, 4)]
        print(f"Fetching historical window {dates[0]} to {dates[-1]}...")
        
        all_mins = []
        all_ave_lows = []
        all_maxs = []
        all_ave_highs = []
        
        for day_date in dates:
            result = self._fetch_day_historical(day_date)
            
            if result:
                all_mins.append(result[0])
                all_ave_lows.append(result[1])
                all_maxs.append(result[2])
                all_ave_highs.append(result[3])
            else:
                print(f"Warning: Failed to fetch historical data for {day_date}")
        
        if not all_mins:
            print("Failed to fetch any historical data.")
            return None
        
        self.historical['low'] = min(all_mins)
        self.historical['ave-low'] = sum(all_ave_lows) / len(all_ave_lows)
        self.historical['high'] = max(all_maxs)
        self.historical['ave-high'] = sum(all_ave_highs) / len(all_ave_highs)
        self.historical['date'] = date
            
        print(f"Historical composite ({len(all_mins)} days): low {self.historical['ave-low']:.0f} ({self.historical['low']}), high {self.historical['ave-high']:.0f} ({self.historical['high']})")        
        return self.historical

    def get_hourly_forecast(self,hours=65):
        """Fetch hourly forecast from NOAA, preserving existing snow_fraction data.
        
        Snow fractions are populated separately by get_griddata() and refreshed
        less often, so we preserve them across hourly forecast updates."""


        print("Getting hourly forecast...")
        json_data = network.get(self.hourly_url)
        if not json_data:
            print("Request failed.")
            return None        
        
        periods = json_data['properties']['periods']
        i=0

        snow_fractions = {}
        for h in self.hourly:
            if h.snow_fraction is not None:
                snow_fractions[h.start] = h.snow_fraction

        self.hourly=[]
        for period in periods:
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
            h.precipitation = period['probabilityOfPrecipitation']['value']
            h.forecast = period['shortForecast']
            
            if h.start in snow_fractions:
                h.snow_fraction = snow_fractions[h.start]
            
            print(f"Hour {number:02}: {h.start[:13]}–{h.end[:13]} {h.temperature:3}° {h.precipitation:3}% rain | {h.forecast}")
            self.hourly.append(h)

            i += 1
            if i >= hours:
                break

        self.hourly_updated = json_data['properties']['updateTime']
        print(f"Hourly forecast last updated at {self.hourly_updated}")   

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
        
        qpf_series = properties['quantitativePrecipitation']
        snow_series = properties['snowfallAmount']
        
        if qpf_series['uom'] != 'wmoUnit:mm':
            print(f"Warning: QPF unit is {qpf_series['uom']}, expected wmoUnit:mm")
        if snow_series['uom'] != 'wmoUnit:mm':
            print(f"Warning: snowfall unit is {snow_series['uom']}, expected wmoUnit:mm")
        
        qpf_by_hour = {}
        for entry in qpf_series['values']:
            valid_time = entry['validTime']
            dt_part, duration = valid_time.split('/')
            key = dt_part[:13]
            n_hours = int(duration[2:-1])
            val = entry['value'] or 0.0
            
            year = int(key[:4])
            month = int(key[5:7])
            day = int(key[8:10])
            base_hour = int(key[11:13])
            
            # NOAA griddata covers multi-hour windows (e.g. PT6H), distribute evenly
            for i in range(n_hours):
                h = base_hour + i
                d = day
                if h >= 24:
                    h -= 24
                    d += 1
                hour_key = f"{year:04}-{month:02}-{d:02}T{h:02}"
                qpf_by_hour[hour_key] = val / n_hours
        
        snow_by_hour = {}
        for entry in snow_series['values']:
            valid_time = entry['validTime']
            dt_part, duration = valid_time.split('/')
            key = dt_part[:13]
            n_hours = int(duration[2:-1])
            val = entry['value'] or 0.0
            
            year = int(key[:4])
            month = int(key[5:7])
            day = int(key[8:10])
            base_hour = int(key[11:13])
            
            # Distribute multi-hour total across individual hours
            for i in range(n_hours):
                h = base_hour + i
                d = day
                if h >= 24:
                    h -= 24
                    d += 1
                hour_key = f"{year:04}-{month:02}-{d:02}T{h:02}"
                snow_by_hour[hour_key] = val / n_hours
        
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
        
        self.griddata_updated = json_data['properties']['updateTime']
        print(f"Populated snow_fraction for {len(self.hourly)} hours")
        print(f"Griddata last updated at {self.griddata_updated}")

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

    def _get_station_url(self):
        """Get first observation station from NOAA station list for this location."""
        
        print("Getting local station...")
        json_data = network.get(self.station_list_url)
        if not json_data:
            return
        
        try:
            for feature in json_data['features']:
                self.station_url = feature['id']
                break
        except KeyError:
            print(f"Couldn't get station information from station list features.")
            pass

        if not self.station_url:
            try:
                stationlist = json_data['observationStations']
                self.station_url = stationlist[0]
            except KeyError:
                print(f"Couldn't get station information from observationStations, either.")
                pass
        
        if self.station_url:
            self.station_id = self.station_url.split('/')[-1]    
            print(f"local Station: {self.station_url}")
