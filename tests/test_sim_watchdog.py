"""Unit tests for SimWatchdog mode behavior.

Tests call SimWatchdog._handle() directly — no SIGALRM timer is involved —
so they are safe to run in any thread under pytest without affecting timing.
"""
import sys

import pytest

from sim_stubs import SimWatchdog, SimWatchdogReset, WatchDogMode, setup_hardware


@pytest.fixture(autouse=True)
def _hardware():
    """Ensure watchdog module stubs are installed before each test."""
    setup_hardware()


def _make_dog():
    wdt_exc = sys.modules["watchdog"].WatchDogTimeout
    return SimWatchdog(wdt_exc)


# ---------------------------------------------------------------------------
# SimWatchdogReset exception hierarchy
# ---------------------------------------------------------------------------

def test_sim_watchdog_reset_is_base_exception_not_exception():
    """Must bypass except-Exception handlers, matching hardware RESET behavior."""
    assert issubclass(SimWatchdogReset, BaseException)
    assert not issubclass(SimWatchdogReset, Exception)


# ---------------------------------------------------------------------------
# SimWatchdog._handle() mode dispatch
# ---------------------------------------------------------------------------

def test_raise_mode_raises_watchdog_timeout():
    """RAISE mode: must raise WatchDogTimeout — catchable by Python code."""
    dog = _make_dog()
    dog._mode = WatchDogMode.RAISE
    wdt_exc = sys.modules["watchdog"].WatchDogTimeout
    with pytest.raises(wdt_exc):
        dog._handle(None, None)


def test_reset_mode_raises_sim_watchdog_reset():
    """RESET mode: must raise SimWatchdogReset — bypasses except Exception."""
    dog = _make_dog()
    dog._mode = WatchDogMode.RESET
    with pytest.raises(SimWatchdogReset):
        dog._handle(None, None)


