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


# ---------------------------------------------------------------------------
# Break DNS / Break ISP button structural tests
# ---------------------------------------------------------------------------

class TestSimulateNetworkBreakButtons:
    """Verify Break DNS and Break ISP button wiring and socket-level interception."""

    def test_dns_command_handled_in_worker(self):
        """Worker stdin listener must handle 'toggle-dns'."""
        assert "toggle-dns" in _SIMULATE.read_text(), (
            "bin/simulate worker stdin listener is missing 'toggle-dns' — "
            "Break DNS button click will be silently ignored by the worker"
        )

    def test_isp_command_handled_in_worker(self):
        """Worker stdin listener must handle 'toggle-isp'."""
        assert "toggle-isp" in _SIMULATE.read_text(), (
            "bin/simulate worker stdin listener is missing 'toggle-isp' — "
            "Break ISP button click will be silently ignored by the worker"
        )

    def test_break_dns_button_label_present(self):
        """Live draw panel must render a 'Break DNS' button label."""
        text = _SIMULATE.read_text()
        assert '"Break DNS"' in text or "'Break DNS'" in text, (
            "bin/simulate does not render a 'Break DNS' button in _live_draw_panel()"
        )

    def test_break_isp_button_label_present(self):
        """Live draw panel must render a 'Break ISP' button label."""
        text = _SIMULATE.read_text()
        assert '"Break ISP"' in text or "'Break ISP'" in text, (
            "bin/simulate does not render a 'Break ISP' button in _live_draw_panel()"
        )

    def test_dns_break_uses_socket_getaddrinfo(self):
        """DNS break must intercept socket.getaddrinfo, not network.request.

        Patching at the socket layer means the real adafruit_requests error path
        is exercised and the error message comes from the OS, not a fake string.
        """
        text = _SIMULATE.read_text()
        assert "getaddrinfo" in text, (
            "bin/simulate is missing a getaddrinfo patch — "
            "Break DNS must intercept at the socket layer"
        )
        assert "_dns_broken" in text, (
            "bin/simulate is missing _dns_broken — "
            "Break DNS has no flag to check in the getaddrinfo patch"
        )

    def test_isp_break_uses_socket_subclass(self):
        """ISP break must subclass socket.socket and override connect().

        Subclassing ensures new sockets respect the flag without touching
        any adafruit library internals.
        """
        text = _SIMULATE.read_text()
        assert "_isp_broken" in text, (
            "bin/simulate is missing _isp_broken — "
            "Break ISP has no flag to check in the socket subclass"
        )
        assert "_OrigSocket" in text or "_SimSocket" in text, (
            "bin/simulate is missing the socket.socket subclass for ISP break"
        )


# ---------------------------------------------------------------------------
# Watchdog restart-loop structural tests
# ---------------------------------------------------------------------------

class TestSimulateWatchdogRestart:
    """Verify that bin/simulate handles both watchdog modes with an auto-restart loop.

    Structural tests — they read the source text rather than executing the
    simulator, which would require hardware stubs and a real scheduler run.
    """

    def test_sim_watchdog_reset_imported(self):
        """bin/simulate must import SimWatchdogReset from sim_stubs."""
        text = _SIMULATE.read_text()
        assert "SimWatchdogReset" in text, (
            "bin/simulate does not import or reference SimWatchdogReset — "
            "watchdog RESET events will not be caught and the worker will crash "
            "instead of rebooting"
        )

    def test_restart_loop_catches_sim_watchdog_reset(self):
        """The restart loop must explicitly catch SimWatchdogReset."""
        text = _SIMULATE.read_text()
        assert "SimWatchdogReset" in text and "rebooting" in text, (
            "bin/simulate restart loop is missing SimWatchdogReset handling — "
            "watchdog RESET will not trigger an auto-reboot"
        )

    def test_restart_loop_catches_watchdog_timeout(self):
        """The restart loop must also catch an escaped WatchDogTimeout (RAISE mode)."""
        text = _SIMULATE.read_text()
        assert "_WatchDogTimeout" in text or "WatchDogTimeout" in text, (
            "bin/simulate restart loop does not catch WatchDogTimeout — "
            "an uncaught RAISE-mode watchdog will crash the worker instead of rebooting"
        )

    def test_worker_exits_zero_on_watchdog(self):
        """Worker mode must call sys.exit(0) on watchdog so the launcher restarts it."""
        text = _SIMULATE.read_text()
        assert "sys.exit(0)" in text, (
            "bin/simulate does not call sys.exit(0) after a watchdog event — "
            "the launcher will see a non-zero exit and show the crash message "
            "instead of auto-restarting"
        )
