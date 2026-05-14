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


# ---------------------------------------------------------------------------
# Worker-command and WiFi-fix structural tests
# ---------------------------------------------------------------------------

class TestSimulateWorkerCommands:
    """Verify the live-window reboot button and corrected WiFi toggle are present."""

    def test_reboot_kills_worker_from_launcher(self):
        """The launcher must kill the worker directly on reboot — not via a stdin command.

        The reboot button must use _reboot_requested (a threading.Event set in
        the launcher's event loop) so the worker is killed immediately with
        proc.kill(), matching a hardware RESET button with no clean-shutdown
        handshake.
        """
        text = _SIMULATE.read_text()
        assert "_reboot_requested" in text, (
            "bin/simulate is missing _reboot_requested — reboot button must kill "
            "the worker from the launcher, not send a 'reboot' stdin command"
        )

    def test_reboot_button_label_present(self):
        """The live-window draw function must render a 'Reboot' button label."""
        text = _SIMULATE.read_text()
        assert '"Reboot"' in text or "'Reboot'" in text, (
            "bin/simulate does not render a Reboot button in _live_draw_panel()"
        )

    def test_wifi_enabled_flag_gates_reconnection(self):
        """_sim_wifi_connect must check a _wifi_enabled flag.

        Without this flag, toggling WiFi off has no effect — the scheduler's
        next connect() call immediately reconnects and network calls continue.
        """
        text = _SIMULATE.read_text()
        assert "_wifi_enabled" in text, (
            "bin/simulate is missing the _wifi_enabled flag — "
            "WiFi toggle does not actually prevent reconnection"
        )
