"""Unit tests for SimWatchdog mode behaviour.

Tests call SimWatchdog._handle() directly — no SIGALRM timer is involved —
so they are safe to run in any thread under pytest without affecting timing.
"""
import sys
from pathlib import Path

import pytest

# sim_stubs lives in tests/simlib/, which conftest.py adds to sys.path.
from sim_stubs import (
    SimWatchdog,
    SimWatchdogReset,
    WatchDogMode,
    setup_hardware,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _hardware(monkeypatch):
    """Ensure watchdog module stubs are installed before each test."""
    setup_hardware()


def _make_dog():
    """Return a SimWatchdog using the stub WatchDogTimeout class."""
    wdt_exc = sys.modules["watchdog"].WatchDogTimeout
    return SimWatchdog(wdt_exc)


# ---------------------------------------------------------------------------
# WatchDogMode enum
# ---------------------------------------------------------------------------

class TestWatchDogMode:
    def test_raise_member_exists(self):
        assert hasattr(WatchDogMode, "RAISE")

    def test_reset_member_exists(self):
        assert hasattr(WatchDogMode, "RESET")

    def test_raise_and_reset_are_distinct(self):
        assert WatchDogMode.RAISE is not WatchDogMode.RESET

    def test_watchdog_module_exposes_enum(self):
        """from watchdog import WatchDogMode must yield the real enum."""
        assert sys.modules["watchdog"].WatchDogMode is WatchDogMode


# ---------------------------------------------------------------------------
# SimWatchdogReset exception type
# ---------------------------------------------------------------------------

class TestSimWatchdogReset:
    def test_is_base_exception(self):
        assert issubclass(SimWatchdogReset, BaseException)

    def test_is_not_exception(self):
        """Must bypass except-Exception handlers, matching hardware RESET."""
        assert not issubclass(SimWatchdogReset, Exception)

    def test_is_distinct_from_watchdog_timeout(self):
        wdt_exc = sys.modules["watchdog"].WatchDogTimeout
        assert not issubclass(SimWatchdogReset, wdt_exc)
        assert not issubclass(wdt_exc, SimWatchdogReset)


# ---------------------------------------------------------------------------
# SimWatchdog._handle() — mode dispatch
# ---------------------------------------------------------------------------

class TestSimWatchdogHandle:
    def test_raise_mode_raises_watchdog_timeout(self):
        """RAISE mode must raise WatchDogTimeout — catchable by Python code."""
        dog = _make_dog()
        dog._mode = WatchDogMode.RAISE
        wdt_exc = sys.modules["watchdog"].WatchDogTimeout
        with pytest.raises(wdt_exc):
            dog._handle(None, None)

    def test_reset_mode_raises_sim_watchdog_reset(self):
        """RESET mode must raise SimWatchdogReset — bypasses except Exception."""
        dog = _make_dog()
        dog._mode = WatchDogMode.RESET
        with pytest.raises(SimWatchdogReset):
            dog._handle(None, None)

    def test_reset_does_not_raise_watchdog_timeout(self):
        dog = _make_dog()
        dog._mode = WatchDogMode.RESET
        wdt_exc = sys.modules["watchdog"].WatchDogTimeout
        with pytest.raises(SimWatchdogReset):
            dog._handle(None, None)
        # If we got here without WatchDogTimeout, the assertion passes.

    def test_raise_does_not_raise_sim_watchdog_reset(self):
        dog = _make_dog()
        dog._mode = WatchDogMode.RAISE
        with pytest.raises(Exception):  # WatchDogTimeout is an Exception subclass
            dog._handle(None, None)
        # SimWatchdogReset would propagate past the except — absence is the test.

    def test_none_mode_raises_watchdog_timeout(self):
        """None mode (unarmed state) falls through to WatchDogTimeout."""
        dog = _make_dog()
        dog._mode = None
        wdt_exc = sys.modules["watchdog"].WatchDogTimeout
        with pytest.raises(wdt_exc):
            dog._handle(None, None)

    def test_raise_message_contains_timeout(self):
        dog = _make_dog()
        dog._mode = WatchDogMode.RAISE
        dog._timeout = 42
        wdt_exc = sys.modules["watchdog"].WatchDogTimeout
        with pytest.raises(wdt_exc, match="42"):
            dog._handle(None, None)

    def test_reset_message_contains_timeout(self):
        dog = _make_dog()
        dog._mode = WatchDogMode.RESET
        dog._timeout = 99
        with pytest.raises(SimWatchdogReset, match="99"):
            dog._handle(None, None)
