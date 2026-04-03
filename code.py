import gc
gc.collect()
print(f"Free memory: {gc.mem_free()} (at start)")
from os import getenv

import supervisor

import scheduler

# defaults
config = {
          'CIRCUITPY_WIFI_SSID' : 'change me in settings.toml',
          'CIRCUITPY_WIFI_PASSWORD' : 'change me in settings.toml',
          'GEOLOCATION_API': "http://ip-api.com/json/",
          'GRIDPOINT_API': "https://api.weather.gov/points/",
          'HISTORICAL_API': "https://data.rcc-acis.org/GridData",
          'LATITUDE': None,
          'LONGITUDE': None,
          'SWAP_GREEN_BLUE': False,
          'RELOAD_ON_ERROR': False,
          'TEMP_SCALE_RANGE': 110,
          'TEMP_MIDPOINT': 50
         }

for conf in config.keys():
    if getenv(conf):
        config[conf] = getenv(conf)
        print(f"{conf} = \'{config[conf]}\'")
    else:
        print(f"{conf} = \'{config[conf]}\' (default)")
        
print(f"Setting reload on error to {config['RELOAD_ON_ERROR']}")
supervisor.set_next_code_file(None,reload_on_error=config['RELOAD_ON_ERROR'],sticky_on_error=True)

scheduler.run(config)