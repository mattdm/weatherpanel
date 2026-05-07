"""Stub CircuitPython-only modules so src/ code can be imported on CPython.

These stubs satisfy import-time attribute access only.  Test-time behaviour
(e.g. network.get returning fixture data) is handled by monkeypatching in
individual tests.
"""
import collections
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--update-refs",
        action="store_true",
        default=False,
        help="Overwrite render reference images with current output.",
    )

# ---------------------------------------------------------------------------
# CircuitPython module stubs — must be registered *before* any src/ import
#
# Only hardware-specific or CP-built-in modules are stubbed here.
# adafruit_bitmap_font and adafruit_display_text are NOT stubbed — the real
# .py sources in lib/ are used so font rendering is accurate.
# displayio is also NOT stubbed — displayio_sim provides a real implementation.
# ---------------------------------------------------------------------------

# displayio: use the real CPython sim instead of MagicMock
import displayio_sim  # noqa: E402  (tests/ module)
sys.modules["displayio"] = displayio_sim

# fontio: CircuitPython built-in providing the Glyph namedtuple used by
# adafruit_bitmap_font's PCF/BDF parsers.
_fontio = types.ModuleType("fontio")
_fontio.Glyph = collections.namedtuple(
    "Glyph", ["bitmap", "tile_index", "width", "height", "dx", "dy", "shift_x", "shift_y"]
)
_fontio.FontProtocol = object
sys.modules["fontio"] = _fontio

# micropython: CircuitPython built-in; const() is a no-op on CPython.
_micropython = types.ModuleType("micropython")
_micropython.const = lambda x: x
sys.modules["micropython"] = _micropython

_wifi = MagicMock()
_wifi.radio.connected = False
_wifi.radio.ap_info.ssid = "test-ssid"
_wifi.radio.ipv4_address = "192.168.1.99"
_wifi.radio.ipv4_address_ap = "192.168.4.1"
_wifi.radio.stations_ap = 0
sys.modules["wifi"] = _wifi

_acm = MagicMock()
sys.modules["adafruit_connection_manager"] = _acm

sys.modules["adafruit_ntp"] = MagicMock()

_adafruit_requests = types.ModuleType("adafruit_requests")
_adafruit_requests.OutOfRetries = type("OutOfRetries", (Exception,), {})
_adafruit_requests.Session = MagicMock
sys.modules["adafruit_requests"] = _adafruit_requests

sys.modules["socketpool"] = MagicMock()

_adafruit_httpserver = MagicMock()
sys.modules["adafruit_httpserver"] = _adafruit_httpserver

sys.modules["adafruit_miniqr"] = MagicMock()

_storage = MagicMock()
sys.modules["storage"] = _storage

# Hardware modules only used by other src/ files (not station.py directly),
# but stub them so any transitive import is safe.
sys.modules["microcontroller"] = MagicMock()
sys.modules["watchdog"] = types.ModuleType("watchdog")
sys.modules["watchdog"].WatchDogMode = MagicMock()
sys.modules["watchdog"].WatchDogTimeout = type("WatchDogTimeout", (Exception,), {})
sys.modules["supervisor"] = MagicMock()
sys.modules["board"] = MagicMock()
sys.modules["rgbmatrix"] = MagicMock()
sys.modules["framebufferio"] = MagicMock()
sys.modules["rtc"] = MagicMock()
sys.modules["neopixel"] = MagicMock()

# ---------------------------------------------------------------------------
# Prevent retry-loop sleeps from slowing the suite
# ---------------------------------------------------------------------------

import station as _station_module  # noqa: E402  (must come after stubs)
_station_module.sleep = lambda _: None
_station_module.RETRY_DELAY_S = 0

# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------

SAMPLE_FORECASTS_DIR = Path(__file__).parent / "sample-forecasts"


def _load_json(name):
    with open(SAMPLE_FORECASTS_DIR / name) as f:
        return json.load(f)


@pytest.fixture
def minimal_config():
    """Bare-minimum config dict for Station.__init__."""
    return {
        "GEOLOCATION_API": "http://test/geolocation",
        "GRIDPOINT_API": "https://test/points",
        "HISTORICAL_API": "https://test/historical",
    }


@pytest.fixture
def make_station(minimal_config):
    """Factory that creates a Station with pre-set URLs (skips network discovery)."""
    def _make(hourly_url="https://test/hourly", griddata_url="https://test/griddata"):
        s = _station_module.Station(minimal_config)
        s.hourly_url = hourly_url
        s.griddata_url = griddata_url
        s.station_id = "TEST"
        s.lat = "39.317"
        s.lon = "-120.333"
        s.location = "39.317,-120.333"
        return s
    return _make


@pytest.fixture
def load_sample():
    """Load a sample forecast JSON file by name from tests/sample-forecasts/."""
    return _load_json


# ---------------------------------------------------------------------------
# Display simulation fixture (shared by test_display_sim, test_display_render,
# and test_display_forecast_render)
# ---------------------------------------------------------------------------

import adafruit_bitmap_font.bitmap_font as _bmp_font  # noqa: E402  (after stubs)
import matrix_sim  # noqa: E402

_DISPLAY_SIM_CONFIG = {
    'TEMP_SCALE_RANGE': 110,
    'TEMP_MIDPOINT': 50,
    'SWAP_GREEN_BLUE': False,
}

_FONTS_DIR = Path(__file__).parent.parent / "fonts"


@pytest.fixture
def sim_display(monkeypatch):
    """Display instance backed by displayio_sim and the real Adafruit font libraries.

    Redirects bitmap_font.load_font to the repo's fonts/ directory so the
    real dogica-pixel-8.pcf is loaded instead of the device-root path.
    matrix.display_set_root is replaced so no hardware init is attempted.
    """
    import display as display_module
    import matrix as matrix_module

    orig_load = _bmp_font.load_font
    monkeypatch.setattr(
        _bmp_font, 'load_font',
        lambda path: orig_load(str(_FONTS_DIR / Path(path).name))
    )
    monkeypatch.setattr(matrix_module, 'display_set_root', matrix_sim.display_set_root)

    return display_module.Display(_DISPLAY_SIM_CONFIG)
