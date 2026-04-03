from time import sleep

import adafruit_json_stream as json_stream

import network


def _parse_utc_key(start_time):
    """Parse local time with offset to UTC hour key.
    
    Args:
        start_time: ISO 8601 string like "2026-03-22T19:00:00-04:00"
    
    Returns:
        UTC hour key like "2026-03-22T23"
    """
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
    elif utc_hour < 0:
        utc_hour += 24
        day -= 1
    
    return f"{year:04}-{month:02}-{day:02}T{utc_hour:02}"


class Hour():
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
    def __init__(self):
        self.name = None
        self.start = None
        self.end = None
        self.night = None
        self.temperature = None
        self.forecast = None


class Station():

    def __init__(self,config):
        

        self.geolocation_api = config['GEOLOCATION_API']
        self.gridpoint_api = config['GRIDPOINT_API']
        self.historical_api = config['HISTORICAL_API']
        self.configured_lat = config.get('LATITUDE')
        self.configured_lon = config.get('LONGITUDE')

        self.tz=None # todo: verify that it matches the geoip tz!
        self.city=None
        self.state=None
        self.lat=None
        self.lon=None
        self.location=None

        self.station_id=None

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


    def get_station(self):

        try:
            i=0
            while not self.griddata_url or not self.forecast_url or not self.hourly_url:

                if self._get_point_info():
                    break
                i+=1

                if i>6:
                    print(f"Can't get information for {self.lat},{self.lon}")
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

    def get_historical(self, date):

        if not self.lat or not self.lon:
            print("Need latitude and longitude to get historical data!")
            return None

        (year,month,day)=date.split("-")

        # TODO: maybe get this for the next few days, instead of just today? requires annoying date math.
        # Ultimately worth it for pedantic correctness, because we're currently coloring tomorrow and the 
        # next day with today's numbers.

        
        querydata = {"loc" : f"{self.lon},{self.lat}",  # yes, log,lat are in a strange order!
                     "grid": "21", # grid 21 is the PRISM dataset https://www.prism.oregonstate.edu/
                     "sdate": f"{int(year)-10}-{month}-{day}",
                     "edate": f"{int(year)-1}-{month}-{day}",
                     "elems": [ {"name":"mint","interval":[1,0,0],"duration":1,"smry":[{"reduce":"min"},{"reduce":"mean"}],"smry_only":"1","units":"degreeF"},
                                {"name":"maxt","interval":[1,0,0],"duration":1,"smry":[{"reduce":"max"},{"reduce":"mean"}],"smry_only":"1","units":"degreeF"}
                                ],
                     "output":"json"
                    }
        
        print(f"Requesting {month}-{day} temps from {int(year)-10}-{int(year)-1}...")
        json_data = network.post(self.historical_api,querydata)

        if not json_data:
            print("Failed to fetch historical data.")
            return None
    
        try:
            summary = json_data['smry']
        except KeyError:
            print("Did not get summary response from historical API.")
            return None
        
        self.historical['low']      = summary[0][0]
        self.historical['ave-low']  = summary[0][1]
        self.historical['high']     = summary[1][0]
        self.historical['ave-high'] = summary[1][1]
        self.historical['date']=date
            
        print(f"For {month}-{day} from {int(year)-10}-{int(year)-1}: low {self.historical['ave-low']} ({self.historical['low']}), high {self.historical['ave-high']} ({self.historical['high']})")        
        return self.historical            


    def get_hourly_forecast(self,hours=65):


        print("Getting hourly forecast...")
        json_data = network.get(self.hourly_url)
        if not json_data:
            print("Request failed.")
            return None        
        
        periods = json_data['properties']['periods']
        i=0

        # Preserve snow_fraction from existing hours
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
            
            # Restore snow_fraction if we had it before
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
        """Fetch griddata and populate snow_fraction for hourly forecast."""
        
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
                snow_liquid_mm = snow_mm / 10.0
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

        if not self.tz:
            try:
                self.tz = properties['timeZone']    
                print(f"Station timezone is {self.tz}")
            except (KeyError, ValueError):
                pass
        
        print(f"Location: {self.city}, {self.state}")
        print(f"observationStations: {self.station_list_url}")
        print(f"forecast: {self.forecast_url}")
        print(f"forecastHourly: {self.hourly_url}")
        print(f"forecastGridData: {self.griddata_url}")

    """
    Gets the first weather station from the list of stations for the lat,long
    """
    def _get_station_url(self):
        
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

        # look another place
        if not self.station_url:            
            try:
                stationlist = self.station_url=json_data['observationStations']
                self.station_url = stationlist[0]
            except KeyError:
                print(f"Couldn't get station information from observationStations, either.")
                pass
        
        if self.station_url:
            self.station_id = self.station_url.split('/')[-1]    
            print(f"local Station: {self.station_url}")
