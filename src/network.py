"""Wi-Fi and HTTP networking for CircuitPython.

Wraps adafruit_requests with error handling for weather API access.
"""
import wifi

import adafruit_connection_manager
import adafruit_ntp
import adafruit_requests
from adafruit_requests import OutOfRetries

NTP_CACHE_TIME = 3600

user_agent = None
_session = None


def _get_session():
    """Return a cached requests session, creating one if needed."""
    global _session
    if _session is None:
        pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
        ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
        _session = adafruit_requests.Session(pool, ssl_context)
    return _session


def _reset_session():
    """Discard the cached session so the next request creates a fresh one."""
    global _session
    _session = None

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


def _headers(extra=None):
    """Build request headers, including User-Agent if configured."""
    h = {'accept': 'application/json'}
    if user_agent:
        h['User-Agent'] = user_agent
    if extra:
        h.update(extra)
    return h


def post(url, querydata):
    """HTTP POST with JSON payload, return parsed JSON response."""
    requests = _get_session()

    json_data = None
    try:
        print(f"POST {url} ", end="")
        with requests.post(url, headers=_headers(), json=querydata) as response:
            if response.status_code != 200:
                print(f"HTTP {response.status_code}")
            else:
                print(f"OK")
                json_data = response.json()
    except (TimeoutError, OutOfRetries, ConnectionError, OSError) as error:
        print(f"Transport error: {type(error).__name__}: {error}")
        _reset_session()
    except ValueError as error:
        print(f"Parse error: {error}")

    return json_data


def get(url, headers=None):
    """HTTP GET returning parsed JSON response."""
    requests = _get_session()

    json_data = None
    try:
        print(f"GET {url} ", end="")
        with requests.get(url, headers=_headers(headers)) as response:
            if response.status_code != 200:
                print(f"HTTP {response.status_code}")
            else:
                print(f"OK")
                json_data = response.json()
    except (TimeoutError, OutOfRetries, ConnectionError, OSError) as error:
        print(f"Transport error: {type(error).__name__}: {error}")
        _reset_session()
    except ValueError as error:
        print(f"Parse error: {error}")

    return json_data
