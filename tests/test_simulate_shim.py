"""Verify the simulate network shim exposes every attribute any src/ module needs.

bin/simulate builds a fake ``network`` module and registers it in
sys.modules["network"].  Any attribute that src/ code accesses on ``network``
must be explicitly set on that shim, or the simulator will crash at runtime
with AttributeError.

This test catches that class of omission by static analysis: it finds every
``network.ATTR`` access across all src/ modules and asserts that
``_network.ATTR`` is assigned somewhere in the shim block of bin/simulate.

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


def _shim_attrs():
    """Return the set of attribute names assigned to the _network shim."""
    text = _SIMULATE.read_text()
    return {m.group(1) for m in re.finditer(r"_network\.(\w+)\s*=", text)}


def _src_network_attrs():
    """Return the set of network.ATTR names accessed across all src/ modules."""
    attrs = set()
    for path in _SRC_DIR.glob("*.py"):
        text = path.read_text()
        attrs.update(m.group(1) for m in re.finditer(r"\bnetwork\.(\w+)", text))
    return attrs


def test_shim_covers_all_src_network_attrs():
    """Every network.ATTR accessed in src/ must be set in the simulate shim.

    If this test fails, add the missing attribute to the ``_network`` shim
    block in bin/simulate (near the other ``_network.X = ...`` lines).
    """
    missing = _src_network_attrs() - _shim_attrs()
    assert not missing, (
        f"simulate network shim is missing attribute(s) used by src/ modules: "
        f"{sorted(missing)!r} — add them to the _network shim in bin/simulate"
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

    def test_normal_wifi_overrides_check(self):
        """_network.check must be overridden in the non-broken-wifi else branch.

        If only _network.connect is overridden, check would fall back to the
        module-level placeholder lambda and the SSID shown on screen would be
        wrong.
        """
        text = _SIMULATE.read_text()
        # Find the else branch of the wifi simulation block and confirm
        # _network.check is assigned there (not just _network.connect).
        m = re.search(
            r'else:\s.*?_network\.check\s*=',
            text, re.DOTALL,
        )
        assert m is not None, (
            "_network.check is not assigned in the non-broken-wifi else branch "
            "of bin/simulate"
        )

    def test_normal_wifi_check_returns_configured_ssid(self):
        """In normal mode, _network.check must return the configured SSID, not
        the hardcoded string 'simulated'.

        The check lambda must close over _ssid (derived from
        config['CIRCUITPY_WIFI_SSID']) so the display shows the real network
        name rather than a generic placeholder.
        """
        text = _SIMULATE.read_text()
        assert "lambda: _ssid" in text, (
            "bin/simulate's normal-wifi _network.check should be "
            "'lambda: _ssid', not a hardcoded string"
        )
