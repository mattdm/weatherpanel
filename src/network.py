"""Wi-Fi and HTTP networking for CircuitPython.

Wraps adafruit_requests with error handling for weather API access,
and provides access-point helpers for the configuration portal.
"""
import gc
import time

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
    """Discard the cached session and force-close all tracked sockets.

    Sets _session to None so the next request creates a fresh one, then calls
    connection_manager_close_all() to clear adafruit_connection_manager's global
    socket registry. Without the latter, a socket left "in use" by an interrupted
    request (e.g. a WatchDogTimeout mid-TLS-handshake) stays registered and causes
    the next get_socket() call for the same host to raise RuntimeError. The call is
    safe at cold boot — if no pools are managed yet, the loop inside
    connection_manager_close_all() does nothing.
    """
    global _session
    _session = None
    adafruit_connection_manager.connection_manager_close_all()

def wifi_configured(config):
    """Return True if Wi-Fi credentials have been set."""
    return bool(config.get('CIRCUITPY_WIFI_SSID'))


def start_ap(ssid, password=None):
    """Start a Wi-Fi access point."""
    if password:
        wifi.radio.start_ap(ssid=ssid, password=password)
    else:
        wifi.radio.start_ap(ssid=ssid)
    print(f"AP started: {ssid} ({wifi.radio.ipv4_address_ap})")


def stop_ap():
    """Stop the Wi-Fi access point."""
    wifi.radio.stop_ap()
    print("AP stopped")


def ap_ip():
    """Return the access point's IP address as a string."""
    return str(wifi.radio.ipv4_address_ap)


def scan_networks():
    """Scan for visible Wi-Fi networks.

    Returns a list of (ssid, rssi) tuples sorted by signal strength
    (strongest first), with duplicate SSIDs removed (keeping the
    strongest reading).
    """
    seen = {}
    for entry in wifi.radio.start_scanning_networks():
        ssid = entry.ssid
        rssi = entry.rssi
        if ssid and (ssid not in seen or rssi > seen[ssid]):
            seen[ssid] = rssi
    wifi.radio.stop_scanning_networks()
    return sorted(seen.items(), key=lambda x: x[1], reverse=True)


def check():
    """Check Wi-Fi connection status, return SSID if connected."""
    if wifi.radio.connected:
        print(f"Connected to {wifi.radio.ap_info.ssid}. ({wifi.radio.ipv4_address})")
        return wifi.radio.ap_info.ssid
    else:
        print("Waiting for network.")
        return None

def connect(config):
    """Attempt to connect to configured Wi-Fi network."""
    print(f"Trying to connect to {config['CIRCUITPY_WIFI_SSID']}")
    try:
        wifi.radio.connect(config['CIRCUITPY_WIFI_SSID'],config['CIRCUITPY_WIFI_PASSWORD'])
    except (ConnectionError, OSError) as e:
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


_READ_CHUNK = 4096


def _parse_json(response):
    """Parse JSON from response with timing and memory diagnostics.

    Runs gc.collect() first to maximise available heap, then logs free memory
    before and after parsing, and elapsed wall-clock time. Returns the parsed
    object, or None on MemoryError.

    Splits the parse into two timed phases so we can distinguish slow network
    reads from slow JSON decoding:
      Phase 1 — _readinto() loop: reads the full body from the socket into bytes
                using _READ_CHUNK-byte chunks rather than the 32-byte default
                used by response.content, reducing ~5,100 recv calls to ~40
      Phase 2 — json.loads():     decodes those bytes into a Python object

    After decode, raw bytes and chunk list are explicitly deleted so the
    ~163 KB buffer is freed before the caller processes the parsed dict.
    """
    import json as _json
    gc.collect()
    mem_before = gc.mem_free()
    t0 = time.monotonic()
    print(f"  Reading body (mem free: {mem_before})...")
    try:
        buf = bytearray(_READ_CHUNK)
        chunks = []
        offset = 0
        chunk_count = 0
        while True:
            n = response._readinto(buf)
            if n == 0:
                break
            chunks.append(bytes(buf[:n]))
            offset += n
            chunk_count += 1
            print(f"    chunk {chunk_count}: {n} bytes, {offset} total")
        raw = b"".join(chunks)
        t1 = time.monotonic()
        print(f"  Body read: {t1-t0:.1f}s  ({len(raw)} bytes, mem free: {gc.mem_free()})")
    except MemoryError:
        elapsed = time.monotonic() - t0
        print(f"  MemoryError reading body after {elapsed:.1f}s (mem free: {gc.mem_free()})")
        return None
    try:
        data = _json.loads(raw)
        t2 = time.monotonic()
        print(f"  JSON decode: {t2-t1:.1f}s  (mem free: {gc.mem_free()}, consumed: {mem_before - gc.mem_free()})")
    except MemoryError:
        elapsed = time.monotonic() - t0
        print(f"  MemoryError decoding JSON after {elapsed:.1f}s (mem free: {gc.mem_free()})")
        return None
    del raw
    del chunks
    return data


def post(url, querydata):
    """HTTP POST with JSON payload, return parsed JSON response."""
    requests = _get_session()

    json_data = None
    try:
        print(f"POST {url} ", end="")
        t0 = time.monotonic()
        with requests.post(url, headers=_headers(), json=querydata) as response:
            if response.status_code != 200:
                print(f"HTTP {response.status_code} ({time.monotonic()-t0:.1f}s)")
            else:
                print(f"OK ({time.monotonic()-t0:.1f}s to headers)")
                json_data = _parse_json(response)
    except (TimeoutError, OutOfRetries, ConnectionError, OSError, RuntimeError) as error:
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
        t0 = time.monotonic()
        with requests.get(url, headers=_headers(headers)) as response:
            if response.status_code != 200:
                print(f"HTTP {response.status_code} ({time.monotonic()-t0:.1f}s)")
            else:
                print(f"OK ({time.monotonic()-t0:.1f}s to headers)")
                json_data = _parse_json(response)
    except (TimeoutError, OutOfRetries, ConnectionError, OSError, RuntimeError) as error:
        print(f"Transport error: {type(error).__name__}: {error}")
        _reset_session()
    except ValueError as error:
        print(f"Parse error: {error}")

    return json_data
