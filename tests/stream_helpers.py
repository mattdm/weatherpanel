"""Helpers for mocking network.get_stream() in tests.

Kept in a separate module (not conftest.py) so importing it does not
trigger setup_hardware() or any other conftest-level side effects.
"""
from contextlib import contextmanager
from pathlib import Path

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"


def _load_bytes(name):
    return (SAMPLE_DIR / name).read_bytes()


def make_hourly_stream(fixture_name):
    """Return a get_stream-compatible mock context manager for a fixture file.

    Usage in tests:
        monkeypatch.setattr(network, "get_stream",
                            make_hourly_stream("boston_hourly.json"))
    """
    import adafruit_json_stream

    raw = _load_bytes(fixture_name)

    @contextmanager
    def _fake_stream(url, headers=None):
        yield adafruit_json_stream.load(iter([raw]))

    return _fake_stream


def dict_to_stream(data):
    """Return a get_stream mock that streams the given dict as JSON bytes.

    Used for tests that build a custom hourly payload (e.g. modified null PoP).
    """
    import json as _json
    import adafruit_json_stream

    raw = _json.dumps(data).encode()

    @contextmanager
    def _fake_stream(url, headers=None):
        yield adafruit_json_stream.load(iter([raw]))

    return _fake_stream
