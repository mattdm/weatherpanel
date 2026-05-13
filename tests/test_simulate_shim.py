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


class TestPortalReload:
    """Verify that saving settings in portal mode restarts the sim correctly."""

    def test_run_portal_returns_reload_flag(self):
        """_run_portal must return a bool indicating whether a reload was requested.

        After saving settings, portal.run() calls supervisor.reload() which raises
        _SimStop.  _run_portal must catch this, set reload_requested = True, and
        return it so callers can distinguish a save/reload from a user interrupt.
        Without the return value, _run_scheduler_main cannot loop back and the sim
        just exits after a save.
        """
        text = _SIMULATE.read_text()
        # Find _run_portal's body and check for the return.
        m = re.search(r'def _run_portal\(.*?\ndef [^\s]', text, re.DOTALL)
        assert m is not None, "_run_portal function not found in bin/simulate"
        body = m.group(0)
        assert "reload_requested = True" in body, (
            "_run_portal does not set reload_requested = True in its _SimStop handler"
        )
        assert "return reload_requested" in body, (
            "_run_portal does not return reload_requested — callers cannot distinguish "
            "a save/reload from an interrupt"
        )

    def test_run_scheduler_loops_after_portal_save(self):
        """_run_scheduler_main must loop back after a portal save, not just return.

        On the real device supervisor.reload() restarts the firmware.  In the sim,
        the equivalent is re-entering the portal-vs-scheduler decision with the
        freshly saved config.  A bare ``if`` that returns after one portal run
        silently exits the simulator instead of restarting.
        """
        text = _SIMULATE.read_text()
        # The portal-entry block must be a while loop, not a bare if.
        assert re.search(
            r'while\s+\(getattr\(args,\s*"portal"',
            text,
        ), (
            "_run_scheduler_main uses 'if' for the portal-entry block — "
            "it must use 'while' so the sim restarts after a settings save"
        )
        # _load_config must be called inside that loop to pick up the new settings.
        m = re.search(
            r'while\s+\(getattr\(args,\s*"portal".*?_load_config\(args\.settings\)',
            text, re.DOTALL,
        )
        assert m is not None, (
            "_run_scheduler_main does not call _load_config inside the portal while-loop — "
            "the sim would restart with the old config after a save"
        )

    def test_live_event_loop_restarts_on_clean_exit(self):
        """_live_event_loop must auto-restart the worker when it exits cleanly (code 0).

        When settings are saved the worker process exits with code 0.  If the
        event loop treats all exits as crashes the user sees 'worker crashed'
        and has to edit source to restart — when all that happened was a successful
        save.  Exit code 0 must trigger the same restart path as a file-change event.
        """
        text = _SIMULATE.read_text()
        # The event loop must check for a zero exit code in the not-worker-alive branch.
        assert re.search(
            r'exit_code\s*=\s*proc_ref\[0\]\.poll\(\)',
            text,
        ), (
            "_live_event_loop does not capture the worker exit code — "
            "it cannot distinguish a clean exit (save) from a crash"
        )
        # And must call _start_worker_proc when exit_code == 0.
        m = re.search(
            r'exit_code\s*==\s*0.*?_start_worker_proc\(\)',
            text, re.DOTALL,
        )
        assert m is not None, (
            "_live_event_loop does not call _start_worker_proc on exit_code == 0 — "
            "saving settings will show 'worker crashed' instead of restarting"
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
