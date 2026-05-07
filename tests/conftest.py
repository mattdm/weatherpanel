"""Stub CircuitPython-only modules so src/ code can be imported on CPython.

These stubs satisfy import-time attribute access only.  Test-time behaviour
(e.g. network.get returning fixture data) is handled by monkeypatching in
individual tests.
"""
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
# ---------------------------------------------------------------------------

_wifi = MagicMock()
_wifi.radio.connected = False
_wifi.radio.ap_info.ssid = "test-ssid"
_wifi.radio.ipv4_address = "192.168.1.99"
sys.modules["wifi"] = _wifi

_acm = MagicMock()
sys.modules["adafruit_connection_manager"] = _acm

sys.modules["adafruit_ntp"] = MagicMock()

_adafruit_requests = types.ModuleType("adafruit_requests")
_adafruit_requests.OutOfRetries = type("OutOfRetries", (Exception,), {})
_adafruit_requests.Session = MagicMock
sys.modules["adafruit_requests"] = _adafruit_requests

# Hardware modules only used by other src/ files (not station.py directly),
# but stub them so any transitive import is safe.
sys.modules["microcontroller"] = MagicMock()
sys.modules["watchdog"] = types.ModuleType("watchdog")
sys.modules["watchdog"].WatchDogMode = MagicMock()
sys.modules["watchdog"].WatchDogTimeout = type("WatchDogTimeout", (Exception,), {})
sys.modules["displayio"] = MagicMock()
sys.modules["adafruit_bitmap_font"] = MagicMock()
sys.modules["adafruit_bitmap_font.bitmap_font"] = MagicMock()
sys.modules["adafruit_display_text"] = MagicMock()
sys.modules["adafruit_display_text.label"] = MagicMock()
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

import adafruit_bitmap_font_sim  # noqa: E402  (tests/ module, after stubs)
import adafruit_display_text_sim  # noqa: E402
import displayio_sim  # noqa: E402
import matrix_sim  # noqa: E402

_DISPLAY_SIM_CONFIG = {
    'TEMP_SCALE_RANGE': 110,
    'TEMP_MIDPOINT': 50,
    'SWAP_GREEN_BLUE': False,
}


@pytest.fixture
def sim_display(monkeypatch):
    """Display instance backed by the CPython sim layer.

    Patches the module-level names in display.py so that all displayio/font/
    label calls go through real sim objects instead of MagicMock stubs.
    matrix.display_set_root is replaced so no hardware init is attempted.
    """
    import display as display_module
    import matrix as matrix_module

    monkeypatch.setattr(display_module, 'displayio', displayio_sim)
    monkeypatch.setattr(display_module, 'bitmap_font', adafruit_bitmap_font_sim.bitmap_font)
    monkeypatch.setattr(display_module, 'Label', adafruit_display_text_sim.Label)
    monkeypatch.setattr(matrix_module, 'display_set_root', matrix_sim.display_set_root)

    return display_module.Display(_DISPLAY_SIM_CONFIG)
