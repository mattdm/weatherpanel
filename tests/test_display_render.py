"""Render-to-PNG tests: exercise the full sim compositor and compare against
committed reference images.

First run (no reference yet): each test saves the rendered PNG and passes.
Subsequent runs: pixel-for-pixel comparison against the saved reference.
Intentional update (rendering changed on purpose): pytest --update-refs
"""
import math
from pathlib import Path

import pytest
from PIL import Image

import adafruit_bitmap_font_sim
import adafruit_display_text_sim
import displayio_sim
import matrix_sim

# ---------------------------------------------------------------------------
# Paths and helpers
# ---------------------------------------------------------------------------

REFS_DIR = Path(__file__).parent / "reference-images"

_CONFIG = {
    'TEMP_SCALE_RANGE': 110,
    'TEMP_MIDPOINT': 50,
    'SWAP_GREEN_BLUE': False,
}

_CURRENT_TIME = "2026-05-07T09:00:00"
_NO_HISTORICAL = [None, None, None]
_HISTORICAL = [
    {'date': '2026-05-07', 'low': 20, 'ave-low': 35, 'ave-high': 55, 'high': 75},
]

_WIDTH = 64


def _make_hour(temperature, precipitation=0, snow_fraction=0.0,
               start="2026-05-07T10:00:00", end="2026-05-07T11:00:00"):
    from station import Hour
    h = Hour()
    h.start = start
    h.end = end
    h.temperature = temperature
    h.precipitation = precipitation
    h.snow_fraction = snow_fraction
    h.forecast = "Sim"
    return h


def _compare_or_save(request, display_obj, name):
    """Render display to a PIL Image and compare against the reference PNG.

    If the reference does not exist (first run) or --update-refs is passed,
    the current render is saved as the new reference and the test passes.
    Otherwise a pixel-exact comparison is performed.
    """
    img = display_obj._display.render_to_image(scale=8)
    ref_path = REFS_DIR / f"{name}.png"

    if request.config.getoption("--update-refs") or not ref_path.exists():
        REFS_DIR.mkdir(exist_ok=True)
        img.save(ref_path)
        return

    ref_img = Image.open(ref_path).convert("RGB")
    assert list(img.getdata()) == list(ref_img.getdata()), (
        f"Render mismatch for '{name}' — run pytest --update-refs to accept new output"
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sim_display(monkeypatch):
    """Display instance backed by the CPython sim layer."""
    import display as display_module
    import matrix as matrix_module

    monkeypatch.setattr(display_module, 'displayio', displayio_sim)
    monkeypatch.setattr(display_module, 'bitmap_font', adafruit_bitmap_font_sim.bitmap_font)
    monkeypatch.setattr(display_module, 'Label', adafruit_display_text_sim.Label)
    monkeypatch.setattr(matrix_module, 'display_set_root', matrix_sim.display_set_root)

    return display_module.Display(_CONFIG)


# ---------------------------------------------------------------------------
# Render scenarios
# ---------------------------------------------------------------------------

class TestRenderScenarios:
    def test_clear_midpoint(self, sim_display, request):
        """Flat 50°F line across all 64 columns, no precipitation."""
        hours = [_make_hour(50)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        _compare_or_save(request, sim_display, "clear_midpoint")

    def test_temperature_wave(self, sim_display, request):
        """Sinusoidal temperature curve spanning ~30–70°F across 64 columns."""
        hours = [
            _make_hour(int(50 + 20 * math.sin(2 * math.pi * i / _WIDTH)))
            for i in range(_WIDTH)
        ]
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        _compare_or_save(request, sim_display, "temperature_wave")

    def test_all_rain(self, sim_display, request):
        """Cold temperatures (40°F) with 60% rain precipitation throughout."""
        hours = [_make_hour(40, precipitation=60, snow_fraction=0.0)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        _compare_or_save(request, sim_display, "all_rain")

    def test_all_snow(self, sim_display, request):
        """Cold temperatures (25°F) with 60% snow precipitation throughout."""
        hours = [_make_hour(25, precipitation=60, snow_fraction=1.0)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        _compare_or_save(request, sim_display, "all_snow")

    def test_mixed_precip(self, sim_display, request):
        """Alternating rain-only and snow-only columns, 50% precipitation."""
        hours = [
            _make_hour(35, precipitation=50, snow_fraction=0.0 if i % 2 == 0 else 1.0)
            for i in range(_WIDTH)
        ]
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        _compare_or_save(request, sim_display, "mixed_precip")

    def test_heat_wave_with_history(self, sim_display, request):
        """72°F with historical ave-high=55 — temperature line should be orange."""
        hours = [_make_hour(72)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _HISTORICAL, _CURRENT_TIME)
        _compare_or_save(request, sim_display, "heat_wave_with_history")
