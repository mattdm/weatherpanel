"""Verify the simulate network layer and argument parser.

bin/simulate uses the real src/network.py backed by adafruit_requests in
CPython mode — there is no longer a shadow _network shim module.  These tests
verify that the shim has been retired and that the wifi simulation is wired
correctly through wifi.radio.

Also tests that bin/simulate's argument parser exposes the expected CLI flags
with the right defaults by running ``bin/simulate --help`` in a subprocess —
avoiding the hardware-stub side effects that make importing the script directly
impractical.
"""
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SIMULATE = _ROOT / "bin" / "simulate"
_SRC_DIR = _ROOT / "src"


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

    def test_wifi_delay_default_is_two_seconds(self):
        """Parse with no flags — wifi_delay must default to 2.0."""
        # Extract the default from the help text via static analysis of the
        # source rather than importing the script (import has hardware side
        # effects).  Look for the default= value on the --wifi-delay line.
        text = _SIMULATE.read_text()
        m = re.search(r'add_argument\([^)]*"--wifi-delay"[^)]*default=(\S+?)[,\)]', text)
        assert m is not None, "--wifi-delay argument not found in bin/simulate source"
        assert float(m.group(1)) == 2.0, (
            f"--wifi-delay default is {m.group(1)!r}, expected 2.0"
        )

    def test_broken_wifi_is_store_true(self):
        """--broken-wifi must be a flag (store_true), not a value argument."""
        text = _SIMULATE.read_text()
        m = re.search(r'add_argument\([^)]*"--broken-wifi"[^)]*\)', text, re.DOTALL)
        assert m is not None, "--broken-wifi argument not found in bin/simulate source"
        assert "store_true" in m.group(0), (
            "--broken-wifi should use action='store_true'"
        )

    def test_normal_wifi_starts_disconnected(self):
        """wifi.radio.connected must be set to False before the connect sequence.

        network.check() reads wifi.radio.connected directly.  If it is not set
        to False first, check() returns the SSID immediately on the first call
        and the scheduler never triggers connect() — the wifi-delay never fires.
        """
        text = _SIMULATE.read_text()
        assert "_wifi.radio.connected = False" in text, (
            "bin/simulate does not set _wifi.radio.connected = False before connect — "
            "the wifi-delay will not fire on startup"
        )

    def test_normal_wifi_overrides_radio_connect(self):
        """wifi.radio.connect must be wired to _sim_wifi_connect in the non-broken path.

        network.connect() calls wifi.radio.connect(ssid, password) directly.
        _sim_wifi_connect provides the simulated delay and sets connected=True.
        If omitted, wifi.radio.connect is a MagicMock that succeeds silently
        without setting connected=True, so check() never returns a SSID.
        """
        text = _SIMULATE.read_text()
        m = re.search(
            r'else:\s.*?_wifi\.radio\.connect\s*=\s*_sim_wifi_connect',
            text, re.DOTALL,
        )
        assert m is not None, (
            "_wifi.radio.connect is not assigned to _sim_wifi_connect in the non-broken-wifi "
            "else branch of bin/simulate — the wifi-delay will never fire"
        )


class TestSimLEDTracking:
    """Verify that the sim LED dot updates on every color change, not just on pixel changes."""

    def test_track_led_has_trigger_refresh(self):
        """_TrackLED must define _trigger_refresh so LED changes emit frames independently.

        Without this, the live window's NeoPixel dot only updates when display
        pixels change — LED transitions that happen mid-operation (e.g. PURPLE
        during a network fetch) are invisible until the next display frame.
        """
        text = _SIMULATE.read_text()
        assert "def _trigger_refresh" in text, (
            "bin/simulate _TrackLED is missing _trigger_refresh — "
            "LED color changes will not trigger frame updates"
        )

    def test_refresh_and_save_tracks_led_color(self):
        """_refresh_and_save must track the last LED color to emit frames on LED-only changes.

        Without _last_led_color, a frame is suppressed whenever display pixels are
        unchanged even if the LED changed — keeping the dot stale in the live window.
        """
        text = _SIMULATE.read_text()
        assert "_last_led_color" in text, (
            "bin/simulate _refresh_and_save does not track _last_led_color — "
            "LED-only state changes will not produce new frames"
        )
