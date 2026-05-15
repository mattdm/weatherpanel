"""Wi-Fi and HTTP networking for CircuitPython.

Wraps adafruit_requests with error handling for weather API access
and provides access-point helpers for the configuration portal.
"""
import gc
import time
from time import monotonic as _monotonic

import wifi

import adafruit_connection_manager
import adafruit_ntp
import adafruit_requests
from adafruit_requests import OutOfRetries

NTP_CACHE_TIME = 3600
MIN_REQUEST_TIMEOUT_S = 5   # skip any call with less than this much budget remaining
MAX_SOCKET_TIMEOUT_S = 20   # per-attempt cap; adafruit_requests may retry internally

user_agent = None
_session = None
_iteration_deadline = None


def set_iteration_deadline(deadline):
    """Set a monotonic deadline for all requests this iteration.

    Called by the scheduler after each watchdog.feed(). Each call to
    request() or get_stream() uses the remaining time as its socket timeout,
    so requests started later in the iteration automatically get a shorter
    window.
    """
    global _iteration_deadline
    _iteration_deadline = deadline


def _get_request_timeout():
    """Seconds remaining until the iteration deadline, capped at MAX_SOCKET_TIMEOUT_S.

    Falls back to MAX_SOCKET_TIMEOUT_S on boot before the first watchdog feed.
    May be negative if the deadline has passed — callers must check against
    MIN_REQUEST_TIMEOUT_S before proceeding. The cap prevents adafruit_requests
    from using an outsized timeout per socket attempt; with ~3 internal retries
    the worst-case total per call is 3 × MAX_SOCKET_TIMEOUT_S.
    """
    if _iteration_deadline is None:
        return MAX_SOCKET_TIMEOUT_S
    raw = _iteration_deadline - _monotonic()
    return min(raw, MAX_SOCKET_TIMEOUT_S)


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


def _fmt_bytes(n):
    """Format a byte count as KB (one decimal) or B."""
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


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
    print("  Downloading...", end="")
    try:
        buf = bytearray(_READ_CHUNK)
        chunks = []
        offset = 0
        while True:
            n = response._readinto(buf)
            if n == 0:
                break
            chunks.append(bytes(buf[:n]))
            offset += n
            print(".", end="")
        raw = b"".join(chunks)
        t1 = time.monotonic()
    except MemoryError:
        elapsed = time.monotonic() - t0
        print(f"\n  Out of memory after {elapsed:.1f} s  ({_fmt_bytes(gc.mem_free())} free)")
        return None
    try:
        data = _json.loads(raw)
        print(f"\n  {_fmt_bytes(len(raw))} in {t1-t0:.1f} s  ({_fmt_bytes(gc.mem_free())} free, {_fmt_bytes(mem_before - gc.mem_free())} used for JSON)")
    except MemoryError:
        elapsed = time.monotonic() - t0
        print(f"\n  Out of memory parsing JSON after {elapsed:.1f} s  ({_fmt_bytes(gc.mem_free())} free)")
        return None
    del raw
    del chunks
    return data


def request(verb, url, body=None, headers=None, out_headers=None):
    """HTTP request (GET or POST), returning parsed JSON response.

    Args:
        verb:        HTTP method — "GET" or "POST"
        url:         URL to request
        body:        JSON-serializable body for POST requests; None for GET
        headers:     Additional headers merged with defaults (GET only)
        out_headers: Optional dict that is populated with response headers on
                     a successful (200) response. Untouched on error or non-200.
    """
    timeout = _get_request_timeout()
    if timeout < MIN_REQUEST_TIMEOUT_S:
        print(f"Skipping {verb} {url} — only {timeout:.1f} s of budget remaining")
        return None

    session = _get_session()

    json_data = None
    try:
        print(f"[{timeout:.0f}s] {verb} {url} ", end="")
        t0 = time.monotonic()
        if verb == "POST":
            response_ctx = session.post(url, headers=_headers(), json=body,
                                        timeout=timeout)
        else:
            response_ctx = session.get(url, headers=_headers(headers),
                                       timeout=timeout)
        with response_ctx as response:
            if response.status_code != 200:
                print(f"HTTP {response.status_code} ({time.monotonic()-t0:.1f} s) [{_get_request_timeout():.0f}s left]")
            else:
                print(f"OK ({time.monotonic()-t0:.1f} s to headers) [{_get_request_timeout():.0f}s left]")
                if out_headers is not None:
                    out_headers.update(response.headers)
                json_data = _parse_json(response)
    except (TimeoutError, OutOfRetries, ConnectionError, OSError, RuntimeError) as error:
        print(f"Transport error: {type(error).__name__}: {error} [{_get_request_timeout():.0f}s left]")
        _reset_session()
    except ValueError as error:
        print(f"Parse error: {error}")

    return json_data


class _GetStream:
    """Context manager returned by get_stream().

    Opens an HTTP GET, logs timing, and exposes a streaming JSON parser fed
    directly from the socket. The caller iterates the stream and may break
    early; unread body bytes are discarded when the context exits and the
    socket closes. Progress dots are printed as data arrives.

    Yields None (via __enter__) on connection failure or non-200 status.
    """

    def __init__(self, url, headers):
        self._url = url
        self._headers = headers
        self._response = None

    def __enter__(self):
        import adafruit_json_stream as _json_stream
        timeout = _get_request_timeout()
        if timeout < MIN_REQUEST_TIMEOUT_S:
            print(f"Skipping GET {self._url} — only {timeout:.1f} s of budget remaining")
            return None
        requests_session = _get_session()
        t0 = time.monotonic()
        print(f"[{timeout:.0f}s] GET {self._url} ", end="")
        try:
            self._response = requests_session.get(
                self._url, headers=_headers(self._headers),
                timeout=timeout
            )
        except (TimeoutError, OutOfRetries, ConnectionError, OSError, RuntimeError) as error:
            print(f"Transport error: {type(error).__name__}: {error} [{_get_request_timeout():.0f}s left]")
            _reset_session()
            return None

        if self._response.status_code != 200:
            print(f"HTTP {self._response.status_code} ({time.monotonic()-t0:.1f} s) [{_get_request_timeout():.0f}s left]")
            return None

        print(f"OK ({time.monotonic()-t0:.1f} s to headers) [{_get_request_timeout():.0f}s left]")
        gc.collect()

        buf = bytearray(_READ_CHUNK)
        response = self._response

        def _chunks():
            while True:
                n = response._readinto(buf)
                if n == 0:
                    break
                yield bytes(buf[:n])

        return _json_stream.load(_chunks())

    @property
    def headers(self):
        """Response headers as Dict[str, str], or {} if not yet connected."""
        return self._response.headers if self._response is not None else {}

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._response is not None:
            self._response.close()
            self._response = None
            print(f"  Stream closed [{_get_request_timeout():.0f}s left]")
        return False


def get_stream(url, headers=None):
    """HTTP GET returning a context manager that yields an adafruit_json_stream.

    Opens the HTTP connection, logs timing, and yields a streaming JSON parser
    fed directly from the socket. The caller iterates the stream and may break
    early; unread body bytes are discarded when the context exits and the
    socket closes.

    Progress dots are printed as data arrives so hangs are immediately visible
    on the serial console.

    Yields None on connection failure or non-200 status.
    """
    return _GetStream(url, headers)
