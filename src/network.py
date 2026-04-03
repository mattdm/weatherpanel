"""Wi-Fi and HTTP networking for CircuitPython.

Wraps adafruit_requests with error handling for weather API access.
"""
import wifi

import adafruit_connection_manager
import adafruit_ntp
import adafruit_requests
from adafruit_requests import OutOfRetries

NTP_CACHE_TIME = 3600

def check():
    """Check Wi-Fi connection status, return SSID if connected."""
    
    if wifi.radio.connected:
        
        print(f"Connected to {wifi.radio.ap_info.ssid}. ({wifi.radio.ipv4_address})")

        return wifi.radio.ap_info.ssid
    
    else:   

        print(f"Waiting for network.")

        return None
    
def connect(config):
    """Attempt to connect to configured Wi-Fi network."""
     

    print(f"Trying to connect to {config['CIRCUITPY_WIFI_SSID']}")
    
    try:
        wifi.radio.connect(config['CIRCUITPY_WIFI_SSID'],config['CIRCUITPY_WIFI_PASSWORD'])
    except Exception as e:
        print(f"Nope! {e}")

def ntp():
    """Create NTP client for time sync."""

    pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    
    return adafruit_ntp.NTP(pool, tz_offset=0, cache_seconds=NTP_CACHE_TIME)


def post(url,querydata):
    """HTTP POST with JSON payload, return parsed JSON response."""

    pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)

    ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)

    requests = adafruit_requests.Session(pool, ssl_context)

    json_data = None

    try:
            print(f"Posting to {url} ",end="")
            with requests.post(url,headers={'accept':'application/json'},json=querydata) as response:
                if response.status_code != 200:
                    print(f"ERROR {response.status_code}")
                else:
                    print(f"OK ({response.status_code})")

                    json_data = response.json()
        
    except (ValueError,TimeoutError,OutOfRetries,ConnectionError,OSError) as error:
        print(f"Network Error: {error}")


    return(json_data)


def get(url,headers={'accept':'application/json'}):
    """HTTP GET returning parsed JSON response."""

    pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)

    ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)

    requests = adafruit_requests.Session(pool, ssl_context)

    json = None

    try:
            print(f"Getting from {url} ",end="")
            with requests.get(url,headers=headers) as response:
                if response.status_code != 200:
                    print(f"ERROR {response.status_code}")
                else:
                    print(f"OK ({response.status_code})")

                    json = response.json()
        
    except (ValueError,TimeoutError,OutOfRetries,ConnectionError,OSError) as error:
        print(f"Network Error: {error}")


    return(json)

