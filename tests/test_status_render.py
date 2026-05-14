"""Render-to-PNG tests for the status screen coordinate layout.

Exercises set_location() across every distinct layout branch:
- 2-digit longitude (abs < 100): single label with space separator
- 3-digit longitude (abs >= 100): split label + 2px drawn dash
- Plain text: error and in-progress states

First run (no reference yet): each test saves the rendered PNG and passes.
Subsequent runs: pixel-for-pixel comparison against the saved reference.
Intentional update (rendering changed on purpose): pytest --update-refs
"""

from render_helpers import compare_or_save


class TestStatusCoordinateRender:
    """Render the status screen with set_location() for each coordinate layout case."""

    def test_east_coast_2digit_lon(self, sim_display, request):
        """Boston: 2-digit longitude, space-separated single label."""
        from display import SUCCESS_COLOR
        sim_display.set_location("42.39,-71.11", SUCCESS_COLOR)
        sim_display.show_status()
        compare_or_save(request, sim_display._display.render_to_image(scale=8), "status_coords_east_coast")

    def test_west_coast_3digit_lon(self, sim_display, request):
        """Seattle: 3-digit longitude, split label with drawn dash."""
        from display import SUCCESS_COLOR
        sim_display.set_location("47.61,-122.33", SUCCESS_COLOR)
        sim_display.show_status()
        compare_or_save(request, sim_display._display.render_to_image(scale=8), "status_coords_west_coast")

    def test_alaska_extreme_3digit_lon(self, sim_display, request):
        """Utqiagvik (Barrow): widest realistic US coordinates."""
        from display import SUCCESS_COLOR
        sim_display.set_location("71.29,-156.79", SUCCESS_COLOR)
        sim_display.show_status()
        compare_or_save(request, sim_display._display.render_to_image(scale=8), "status_coords_alaska")

    def test_puerto_rico_2digit_lon(self, sim_display, request):
        """Puerto Rico: 2-digit longitude, southernmost US location."""
        from display import SUCCESS_COLOR
        sim_display.set_location("18.47,-66.12", SUCCESS_COLOR)
        sim_display.show_status()
        compare_or_save(request, sim_display._display.render_to_image(scale=8), "status_coords_puerto_rico")

    def test_plain_text_locating(self, sim_display, request):
        """Plain text pass-through: boot progress message."""
        from display import QUERY_COLOR
        sim_display.set_location("Locating...", QUERY_COLOR)
        sim_display.show_status()
        compare_or_save(request, sim_display._display.render_to_image(scale=8), "status_coords_plain_text")

    def test_plain_text_error(self, sim_display, request):
        """Plain text pass-through: location error state."""
        from display import FAILURE_COLOR
        sim_display.set_location("Location?", FAILURE_COLOR)
        sim_display.show_status()
        compare_or_save(request, sim_display._display.render_to_image(scale=8), "status_coords_plain_error")
