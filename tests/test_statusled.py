"""Tests for the StatusLED NeoPixel wrapper.

The neopixel module is a MagicMock in the test environment (see sim_stubs.py),
so NeoPixel() returns a MagicMock whose fill() calls we can inspect to verify
the LED shows the right color in each state.
"""
import pytest

from statusled import (
    BLUE, GREEN, ORANGE, OFF, PURPLE, RED, StatusLED,
)


@pytest.fixture
def led():
    """Fresh StatusLED for each test."""
    return StatusLED()


def _color(led):
    """Return the most recent color passed to pixel.fill()."""
    calls = led._pixel.fill.call_args_list
    assert calls, "fill() was never called"
    return calls[-1].args[0]


class TestWorking:
    def test_working_clears_sticky(self, led):
        led.failure()
        assert led._sticky
        led.working(BLUE)
        assert not led._sticky

    def test_working_overrides_sticky_color(self, led):
        led.failure()
        led.working(PURPLE)
        assert _color(led) == PURPLE


class TestSuccess:
    def test_success_does_not_override_sticky_failure(self, led):
        led.failure()
        led.success()
        assert _color(led) == ORANGE

    def test_success_does_not_override_sticky_wifi_down(self, led):
        led.wifi_down()
        led.success()
        assert _color(led) == RED


class TestFailure:
    def test_failure_overrides_green(self, led):
        led.success()
        led.failure()
        assert _color(led) == ORANGE


class TestIdle:
    def test_idle_leaves_sticky_failure_alone(self, led):
        led.failure()
        led.idle()
        assert _color(led) == ORANGE

    def test_idle_leaves_sticky_wifi_down_alone(self, led):
        led.wifi_down()
        led.idle()
        assert _color(led) == RED

    def test_idle_clears_green(self, led):
        led.success()
        led.idle()
        assert _color(led) == OFF


class TestClear:
    def test_clear_clears_sticky(self, led):
        led.failure()
        led.clear()
        assert not led._sticky

    def test_clear_overrides_sticky_failure(self, led):
        led.failure()
        led.clear()
        assert _color(led) == OFF

    def test_clear_overrides_sticky_wifi_down(self, led):
        led.wifi_down()
        led.clear()
        assert _color(led) == OFF

    def test_success_after_clear_sets_green(self, led):
        """After a hard clear, success() is no longer blocked by sticky."""
        led.failure()
        led.clear()
        led.success()
        assert _color(led) == GREEN
