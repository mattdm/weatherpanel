"""Verify the simulate network shim exposes every attribute any src/ module needs.

bin/simulate builds a fake ``network`` module and registers it in
sys.modules["network"].  Any attribute that src/ code accesses on ``network``
must be explicitly set on that shim, or the simulator will crash at runtime
with AttributeError.

This test catches that class of omission by static analysis: it finds every
``network.ATTR`` access across all src/ modules and asserts that
``_network.ATTR`` is assigned somewhere in the shim block of bin/simulate.
"""
import re
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
