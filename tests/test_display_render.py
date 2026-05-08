"""Render-to-PNG tests: exercise the full sim compositor and compare against
committed reference images.

First run (no reference yet): each test saves the rendered PNG and passes.
Subsequent runs: pixel-for-pixel comparison against the saved reference.
Intentional update (rendering changed on purpose): pytest --update-refs
"""
import math

from render_helpers import compare_or_save

_CURRENT_TIME = "2026-05-07T09:00:00"
_NO_HISTORICAL = [None, None, None, None]
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


# ---------------------------------------------------------------------------
# Render scenarios
# ---------------------------------------------------------------------------

class TestRenderScenarios:
    def test_clear_midpoint(self, sim_display, request):
        """Flat 50°F line across all 64 columns, no precipitation."""
        hours = [_make_hour(50)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        compare_or_save(request, sim_display, "clear_midpoint")

    def test_temperature_wave(self, sim_display, request):
        """Sinusoidal temperature curve spanning ~30–70°F across 64 columns."""
        hours = [
            _make_hour(int(50 + 20 * math.sin(2 * math.pi * i / _WIDTH)))
            for i in range(_WIDTH)
        ]
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        compare_or_save(request, sim_display, "temperature_wave")

    def test_all_rain(self, sim_display, request):
        """Cold temperatures (40°F) with 60% rain precipitation throughout."""
        hours = [_make_hour(40, precipitation=60, snow_fraction=0.0)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        compare_or_save(request, sim_display, "all_rain")

    def test_all_snow(self, sim_display, request):
        """Cold temperatures (25°F) with 60% snow precipitation throughout."""
        hours = [_make_hour(25, precipitation=60, snow_fraction=1.0)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        compare_or_save(request, sim_display, "all_snow")

    def test_mixed_precip(self, sim_display, request):
        """Alternating rain-only and snow-only columns, 50% precipitation."""
        hours = [
            _make_hour(35, precipitation=50, snow_fraction=0.0 if i % 2 == 0 else 1.0)
            for i in range(_WIDTH)
        ]
        sim_display.update_hourly_forecast(hours, _NO_HISTORICAL, _CURRENT_TIME)
        compare_or_save(request, sim_display, "mixed_precip")

    def test_heat_wave_with_history(self, sim_display, request):
        """72°F with historical ave-high=55 — temperature line should be orange."""
        hours = [_make_hour(72)] * _WIDTH
        sim_display.update_hourly_forecast(hours, _HISTORICAL, _CURRENT_TIME)
        compare_or_save(request, sim_display, "heat_wave_with_history")
