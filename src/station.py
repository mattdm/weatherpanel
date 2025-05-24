from time import sleep

import adafruit_json_stream as json_stream

import network



class Hour():
    def __init__(self):
        self.start= None
        self.end = None
        self.is_daytime = None
        self.temperature = None
        self.precipitation = None
        self.forecast = None
    
    def copy(self):
        h = Hour()
        h.start = self.start
        h.end = self.end
        h.temperature = self.temperature
        h.precipitation = self.precipitation
        h.forecast = self.forecast
        return h
    
    def blend(self,other):
        h = Hour()
        h.start = other.start
        h.end =  self.end
        h.temperature = round((self.temperature+other.temperature)/2)
        h.precipitation = round((self.precipitation+other.precipitation)/2)
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
        # TODO (big) -- this endpoint has the same data as hourly_url, but formatted with valid time in time-duration
        # format. we should switch to that, because it has actual rain and snow amounts. A project for this winter!
        self.griddata_url=None

        # observations at the station are less useful than gridpoint data, even if there are just forecasts
        self.observations=None
        self.observations_updated=None
        self.hourly=[]
        self.hourly_updated=None
        self.forecast=None
        self.forecast_updated=None
        self.historical={}


    def geolocate(self):
        i = 0 
        while not self.lat or not self.lon:
            print("Getting location...")
            json_data = network.get(self.geolocation_api)
            if not json_data:
                print(f"Warning: didn't get location from {self.geolocation_api}")
                i += 1
                if i>6:
                    print("Using Somerville, MA as location")
                    self.lat="42.39"
                    self.lon="-71.13"                
                    break
            else:
                if 'lat' in json_data.keys() and 'lon' in json_data.keys():
                    self.lat=f"{json_data['lat']:.4}"
                    self.lon=f"{json_data['lon']:.4}"
                    print(f"Latitude: {self.lat} Longitude {self.lon}")
                if 'timezone' in json_data.keys():
                    self.tz=json_data['timezone']
                    print(f"GeoIP timezone is {self.tz}")

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

                    
    def get_observations(self):

        print("Getting latest observations...")
        json_data = network.get(self.griddata_url)

        if not json_data:
            return

        properties = json_data['properties']
        
        self.observations={}
        for prop in ('timestamp',
                    'textDescription',
                    'presentWeather',
                    ):

            self.observations[prop] = properties[prop]

        for prop in ('temperature',
                    'relativeHumidity',
                    'windChill',
                    'heatIndex',
                    ):

            p = properties[prop]            
            unit = p['unitCode']
            value = p['value']
            if value and unit and unit == "wmoUnit:degC":
                self.observations[prop] = round(value * 9/5 + 32)
            else:
                self.observations[prop] = value
            

        print(f"Observation time: {self.observations['timestamp']}")
        print(f"Current: {self.observations['textDescription']} ({self.observations['temperature']}°F)")


    def get_hourly_forecast(self,hours=65):


        print("Getting hourly forecast...")
        json_data = network.get(self.hourly_url)
        if not json_data:
            print("Request failed.")
            return None        
        
        periods = json_data['properties']['periods']
        i=0

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
            
            print(f"Hour {number:02}: {h.start[:13]}–{h.end[:13]} {h.temperature:3}° {h.precipitation:3}% rain | {h.forecast}")
            self.hourly.append(h)

            i += 1
            if i >= hours:
                break

        self.hourly_updated = json_data['properties']['updateTime']
        print(f"Hourly forecast last updated at {self.hourly_updated}")   

        return i


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
