"""Hardware stub setup shared by the test suite and bin/simulate.

Call setup_hardware() before importing any src/ module. It registers
MagicMock (or minimal real stubs) for every CircuitPython-only module so
that CPython can import the production code without hardware present.

Kept separate from conftest.py so bin/simulate can call the same function
and both stay in sync automatically.
"""
import calendar
import collections
import gc
import signal
import sys
import time
import types
from unittest.mock import MagicMock


def setup_hardware():
    """Install sys.modules stubs for all CircuitPython hardware dependencies."""

    # CircuitPython's time.mktime() treats its input as UTC (the RTC has no
    # timezone concept). CPython's time.mktime() treats input as local time,
    # which produces a wrong epoch for UTC timestamps. Replace it with
    # calendar.timegm, which always treats input as UTC — matching the device.
    time.mktime = calendar.timegm

    # displayio: callers install displayio_sim after this call, but we leave
    # the slot empty here rather than MagicMocking it, so an accidental
    # import before the real sim is installed raises ImportError rather than
    # silently returning a mock.

    # fontio: CircuitPython built-in; provides Glyph namedtuple used by the
    # adafruit_bitmap_font PCF/BDF parsers.
    _fontio = types.ModuleType("fontio")
    _fontio.Glyph = collections.namedtuple(
        "Glyph",
        ["bitmap", "tile_index", "width", "height", "dx", "dy", "shift_x", "shift_y"],
    )
    _fontio.FontProtocol = object
    sys.modules["fontio"] = _fontio

    # micropython: CircuitPython built-in; const() is a no-op on CPython.
    _micropython = types.ModuleType("micropython")
    _micropython.const = lambda x: x
    sys.modules["micropython"] = _micropython

    # Network-layer CircuitPython modules — replaced wholesale by the network
    # shim in bin/simulate, but must exist at import time.
    _wifi = MagicMock()
    _wifi.radio.connected = False
    _wifi.radio.ap_info.ssid = "sim"
    _wifi.radio.ipv4_address = "127.0.0.1"
    _wifi.radio.ipv4_address_ap = "192.168.4.1"
    _wifi.radio.stations_ap = 0
    sys.modules["wifi"] = _wifi

    sys.modules["adafruit_connection_manager"] = MagicMock()
    sys.modules["adafruit_ntp"] = MagicMock()

    _adafruit_requests = types.ModuleType("adafruit_requests")
    _adafruit_requests.OutOfRetries = type("OutOfRetries", (Exception,), {})
    _adafruit_requests.Session = MagicMock
    sys.modules["adafruit_requests"] = _adafruit_requests

    sys.modules["socketpool"] = MagicMock()
    sys.modules["adafruit_httpserver"] = MagicMock()
    # adafruit_miniqr is pure Python and pip-installable; use the real library
    # in the sim so QR bitmaps are actually generated.  Tests that exercise
    # make_qr_bitmap directly also benefit from the real implementation.
    sys.modules["storage"] = MagicMock()

    # Hardware-only modules.
    sys.modules["microcontroller"] = MagicMock()

    _watchdog = types.ModuleType("watchdog")
    _watchdog.WatchDogMode = MagicMock()
    _watchdog.WatchDogTimeout = type("WatchDogTimeout", (Exception,), {})
    sys.modules["watchdog"] = _watchdog

    _supervisor = MagicMock()
    _supervisor.runtime.usb_connected = False
    sys.modules["supervisor"] = _supervisor
    sys.modules["board"] = MagicMock()
    sys.modules["rgbmatrix"] = MagicMock()
    sys.modules["framebufferio"] = MagicMock()
    sys.modules["neopixel"] = MagicMock()

    # rtc: real minimal stub so clock.py can call time.mktime() on .datetime.
    # MagicMock would return another MagicMock from the property, which
    # time.mktime() cannot accept.
    _rtc = types.ModuleType("rtc")

    class _RTC:
        @property
        def datetime(self):
            return time.localtime()

        @datetime.setter
        def datetime(self, v):
            pass  # no hardware to set

    _rtc.RTC = _RTC
    sys.modules["rtc"] = _rtc

    # gc.mem_free() does not exist in CPython.
    gc.mem_free = lambda: 0


class SimWatchdog:
    """SIGALRM-based watchdog that mirrors the real hardware timer.

    Each call to feed() (or arming via mode=) resets a SIGALRM countdown.
    If feed() is not called within timeout seconds, SIGALRM fires and the
    handler raises WatchDogTimeout into the main thread — exactly matching
    the behaviour the scheduler expects from the real CircuitPython watchdog.
    """

    def __init__(self, wdt_exception_class):
        self._exc = wdt_exception_class
        self._timeout = 60
        self._mode = None

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        self._mode = value
        if value is not None:
            self._arm()

    def feed(self):
        if self._mode is not None:
            self._arm()

    def _arm(self):
        signal.signal(signal.SIGALRM, self._handle)
        signal.setitimer(signal.ITIMER_REAL, self._timeout)

    def _handle(self, signum, frame):
        raise self._exc(f"[sim] watchdog timeout ({self._timeout}s)")


def install_sim_watchdog():
    """Replace the MagicMock watchdog with a real SIGALRM-based SimWatchdog.

    Call after setup_hardware(). Only safe in the main thread on Linux/macOS.
    Not called during pytest runs so test timing is unaffected.
    """
    wdt_exc = sys.modules["watchdog"].WatchDogTimeout
    sys.modules["microcontroller"].watchdog = SimWatchdog(wdt_exc)
