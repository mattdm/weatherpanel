"""Stub CircuitPython-only modules so src/ code can be imported on CPython.

These stubs satisfy import-time attribute access only.  Test-time behaviour
(e.g. network.get returning fixture data) is handled by monkeypatching in
individual tests.
"""
import json
import sys
import time as _time
from pathlib import Path

import pytest

from sim_stubs import setup_hardware


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

# Hardware stubs shared with bin/simulate (single source of truth).
setup_hardware()

# displayio: use the real CPython sim instead of MagicMock.
import displayio_sim  # noqa: E402  (tests/ module, after setup_hardware)
sys.modules["displayio"] = displayio_sim

# ---------------------------------------------------------------------------
# Prevent retry-loop sleeps from slowing the suite
# ---------------------------------------------------------------------------

import station as _station_module  # noqa: E402  (must come after stubs)
_station_module.sleep = lambda _: None
_station_module.RETRY_DELAY_S = 0

# ---------------------------------------------------------------------------
# Isolate network._iteration_deadline between tests
#
# scheduler.run() writes network._iteration_deadline via set_iteration_deadline().
# Integration tests that mock scheduler.monotonic to 0 leave a tiny deadline
# that, when compared against the real monotonic clock, is hugely negative.
# Reset it before each test so that budget checks in production code see the
# clean state. Tests must not call _budget_remaining() without a deadline set.
# ---------------------------------------------------------------------------

import network as _network_module  # noqa: E402  (must come after stubs)


@pytest.fixture(autouse=True)
def _reset_network_deadline():
    _network_module.set_iteration_deadline(_time.monotonic() + 60)
    yield
    _network_module.set_iteration_deadline(_time.monotonic() + 60)

# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------

SAMPLE_FORECASTS_DIR = Path(__file__).parent / "sample-forecasts"


def _load_json(name):
    with open(SAMPLE_FORECASTS_DIR / name) as f:
        return json.load(f)


def _load_bytes(name):
    return (SAMPLE_FORECASTS_DIR / name).read_bytes()


@pytest.fixture
def minimal_config():
    """Bare-minimum config dict for Station.__init__."""
    return {
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
import matrix as _matrix_module                       # noqa: E402
import matrix_sim                                     # noqa: E402

_DISPLAY_SIM_CONFIG = {
    'TEMP_MIN': -5,
    'TEMP_MAX': 105,
    'SWAP_GREEN_BLUE': False,
}

_FONTS_DIR = Path(__file__).parent.parent / "fonts"

# ---------------------------------------------------------------------------
# Session-wide font cache — eliminates per-test PCF file reads and, critically,
# the explicit gc.collect() calls that adafruit_bitmap_font makes before each
# glyph load.  By returning the same PCFFont object every time, glyphs loaded
# by the first test stay in the object's internal glyph dict; all subsequent
# lookups are instant dict hits with no GC pressure.
# ---------------------------------------------------------------------------
_orig_load_font = _bmp_font.load_font
_font_cache: dict = {}


def _cached_font_loader(path: str):
    """Redirect device font paths to repo fonts/ and cache the loaded font object."""
    redir = str(_FONTS_DIR / Path(path).name)
    if redir not in _font_cache:
        _font_cache[redir] = _orig_load_font(redir)
    return _font_cache[redir]


_bmp_font.load_font = _cached_font_loader

# Permanently install the sim display backend so no per-test monkeypatching is
# needed.  Tests that intercept display_set_root (e.g. scheduler integration)
# use monkeypatch.setattr, which captures this value and restores it correctly.
_matrix_module.display_set_root = matrix_sim.display_set_root


@pytest.fixture(scope="class")
def sim_display():
    """Display instance backed by displayio_sim and the real Adafruit font libraries.

    Class-scoped so the same Display object is shared across all tests in a
    class.  The session-wide font cache (installed at conftest load time) means
    glyph bitmaps are loaded at most once per session — eliminating the
    gc.collect() calls that adafruit_bitmap_font makes on each cold glyph load.
    """
    import display as display_module
    return display_module.Display(_DISPLAY_SIM_CONFIG)
