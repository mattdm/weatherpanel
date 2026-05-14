"""Verify the simulate network layer and argument parser.

bin/simulate uses the real src/network.py backed by adafruit_requests in
CPython mode — there is no longer a shadow _network shim module.  These tests
verify that the shim has been retired and that the CLI exposes the expected
flags with the right defaults.

Argument-parser tests run ``bin/simulate --help`` in a subprocess to avoid the
hardware-stub side effects that make importing the script directly impractical.
"""
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SIMULATE = _ROOT / "bin" / "simulate"


def test_no_network_shim():
    """bin/simulate must NOT build a fake network module.

    The real src/network.py is used directly, backed by adafruit_requests in
    CPython mode.  A shadow _network module would bypass network.py's error
    handling and _parse_json — the whole point of the refactor was to retire it.
    """
    text = _SIMULATE.read_text()
    assert 'types.ModuleType("network")' not in text, (
        "bin/simulate still creates a _network shim — retire it and use src/network.py directly"
    )


# ---------------------------------------------------------------------------
# Argument-parser tests — run via --help to avoid import side effects
# ---------------------------------------------------------------------------

def _help_text():
    """Return the --help output of bin/simulate."""
    result = subprocess.run(
        [sys.executable, str(_SIMULATE), "--help"],
        capture_output=True, text=True,
    )
    # argparse writes --help to stdout and exits 0.
    return result.stdout


class TestSimulateArgParser:
    """Verify --wifi-delay and --broken-wifi appear in the CLI with correct defaults."""

    def test_wifi_delay_appears_in_help(self):
        assert "--wifi-delay" in _help_text()

    def test_broken_wifi_appears_in_help(self):
        assert "--broken-wifi" in _help_text()

    def test_wifi_delay_default_shown_in_help(self):
        """The help text must advertise the 2.0-second default."""
        assert "2.0" in _help_text()
