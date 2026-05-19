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
_ADAFRUIT_REQUESTS_MAX_RETRIES = 2  # retry_count < 2 in adafruit_requests.py
# Minimum budget required after headers arrive before attempting body download.
# Anything below this means the remaining loop work (display, clock.wait()) cannot
# complete before the watchdog fires, so we skip the body and let the caller retry.
BODY_MIN_BUDGET_S = 3

user_agent = None
_session = None
_iteration_deadline = None


def set_iteration_deadline(deadline):
    """Set a monotonic deadline for all requests this iteration.

    Called by the scheduler after each watchdog.feed(). request() and
    get_stream() divide the remaining time by _ADAFRUIT_REQUESTS_MAX_RETRIES
    to compute a per-attempt socket timeout, bounding worst-case total time
    (all retries firing) to the remaining budget.
    """
    global _iteration_deadline
    _iteration_deadline = deadline


def _budget_remaining():
    """Seconds of network budget remaining in this iteration.

    May be negative if the deadline has already passed. Call sites pass
    min_budget_s to request() / get_stream() to declare how much budget they
    need; the budget is divided by _ADAFRUIT_REQUESTS_MAX_RETRIES to compute
    the per-attempt socket timeout.
    """
    return _iteration_deadline - _monotonic()


def has_budget(*, min_budget_s):
    """Return True if the remaining iteration budget meets the given floor.

    Callers declare exactly how much budget they need — the same contract as
    request() and get_stream().
    """
    return _budget_remaining() >= min_budget_s


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


def fmt_bytes(n):
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
        while True:
            n = response._readinto(buf)
            if n == 0:
                break
            chunks.append(bytes(buf[:n]))
            print(".", end="")
        raw = b"".join(chunks)
        t1 = time.monotonic()
    except MemoryError:
        elapsed = time.monotonic() - t0
        print(f"\n  Out of memory after {elapsed:.1f} s  ({fmt_bytes(gc.mem_free())} free)")
        return None
    except OSError as e:
        elapsed = time.monotonic() - t0
        print(f"\n  Transport error reading body after {elapsed:.1f} s: {e}")
        return None
    try:
        data = _json.loads(raw)
        print(f"\n  {fmt_bytes(len(raw))} in {t1-t0:.1f} s  ({fmt_bytes(gc.mem_free())} free, {fmt_bytes(mem_before - gc.mem_free())} used for JSON)")
    except MemoryError:
        elapsed = time.monotonic() - t0
        print(f"\n  Out of memory parsing JSON after {elapsed:.1f} s  ({fmt_bytes(gc.mem_free())} free)")
        return None
    del raw
    del chunks
    return data


def request(verb, url, body=None, headers=None, out_headers=None, *, min_budget_s):
    """HTTP request (GET or POST), returning parsed JSON response.

    Args:
        verb:         HTTP method — "GET" or "POST"
        url:          URL to request
        body:         JSON-serializable body for POST requests; None for GET
        headers:      Additional headers merged with defaults (GET only)
        out_headers:  Optional dict that is populated with response headers on
                      a successful (200) response. Untouched on error or non-20.
        min_budget_s: Required. Minimum seconds of budget needed to attempt
                      this request. budget / _ADAFRUIT_REQUESTS_MAX_RETRIES
                      becomes the per-attempt socket timeout.
    """
    budget = _budget_remaining()
    if budget < min_budget_s:
        print(f"Skipping {verb} {url} — only {budget:.1f}s remaining, need {min_budget_s}s")
        return None
    timeout = budget / _ADAFRUIT_REQUESTS_MAX_RETRIES

    session = _get_session()
    json_data = None
    try:
        print(f"{verb} {url} ", end="")
        t0 = time.monotonic()
        if verb == "POST":
            response_ctx = session.post(url, headers=_headers(), json=body,
                                        timeout=timeout)
        else:
            response_ctx = session.get(url, headers=_headers(headers),
                                       timeout=timeout)
        with response_ctx as response:
            if response.status_code != 200:
                print(f"HTTP {response.status_code} ({time.monotonic()-t0:.1f} s) [{_budget_remaining():.0f}s left, {timeout:.0f}s/attempt]")
            else:
                budget_after = _budget_remaining()
                print(f"OK ({time.monotonic()-t0:.1f} s to headers) [{budget_after:.0f}s left, {timeout:.0f}s/attempt]")
                if budget_after < BODY_MIN_BUDGET_S:
                    print("  Budget exhausted after headers — skipping body")
                else:
                    if out_headers is not None:
                        out_headers.update(response.headers)
                    json_data = _parse_json(response)
    except (TimeoutError, OutOfRetries, ConnectionError, OSError, RuntimeError) as error:
        print(f"Transport error: {type(error).__name__}: {error} [{_budget_remaining():.0f}s left, {timeout:.0f}s/attempt]")
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

    def __init__(self, url, headers, min_budget_s):
        self._url = url
        self._headers = headers
        self._min_budget_s = min_budget_s
        self._response = None

    def __enter__(self):
        import adafruit_json_stream as _json_stream
        budget = _budget_remaining()
        if budget < self._min_budget_s:
            print(f"Skipping GET {self._url} — only {budget:.1f}s remaining, need {self._min_budget_s}s")
            return None
        timeout = budget / _ADAFRUIT_REQUESTS_MAX_RETRIES
        requests_session = _get_session()
        t0 = time.monotonic()
        print(f"GET {self._url} ", end="")
        try:
            self._response = requests_session.get(
                self._url, headers=_headers(self._headers),
                timeout=timeout
            )
        except (TimeoutError, OutOfRetries, ConnectionError, OSError, RuntimeError) as error:
            print(f"Transport error: {type(error).__name__}: {error} [{_budget_remaining():.0f}s left, {timeout:.0f}s/attempt]")
            _reset_session()
            return None

        if self._response.status_code != 200:
            print(f"HTTP {self._response.status_code} ({time.monotonic()-t0:.1f} s) [{_budget_remaining():.0f}s left, {timeout:.0f}s/attempt]")
            return None

        print(f"OK ({time.monotonic()-t0:.1f} s to headers) [{_budget_remaining():.0f}s left, {timeout:.0f}s/attempt]")
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
            print(f"  Stream closed [{_budget_remaining():.0f}s left]")
        return False


def get_stream(url, headers=None, *, min_budget_s):
    """HTTP GET returning a context manager that yields an adafruit_json_stream.

    Opens the HTTP connection, logs timing, and yields a streaming JSON parser
    fed directly from the socket. The caller iterates the stream and may break
    early; unread body bytes are discarded when the context exits and the
    socket closes.

    Progress dots are printed as data arrives so hangs are immediately visible
    on the serial console.

    min_budget_s: Required. Minimum seconds of budget needed to attempt this
    request. budget / _ADAFRUIT_REQUESTS_MAX_RETRIES becomes the per-attempt
    socket timeout.

    Yields None on connection failure, budget skip, or non-200 status.
    """
    return _GetStream(url, headers, min_budget_s)
