"""Helpers for mocking network.get_stream() in tests.

Kept in a separate module (not conftest.py) so importing it does not
trigger setup_hardware() or any other conftest-level side effects.
"""
import json as _json
from pathlib import Path

import adafruit_json_stream

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"


def _load_bytes(name):
    return (SAMPLE_DIR / name).read_bytes()


def make_hourly_stream(fixture_name, response_headers=None):
    """Return a get_stream-compatible context manager backed by a fixture file.

    The returned context manager always exposes a .headers dict (empty by
    default) so production code can read Cache-Control without a defensive
    getattr.

    Usage:
        monkeypatch.setattr(network, "get_stream",
                            make_hourly_stream("boston_hourly.json"))

        monkeypatch.setattr(network, "get_stream",
                            make_hourly_stream("boston_hourly.json",
                                response_headers={"cache-control": "max-age=3600"}))
    """
    raw = _load_bytes(fixture_name)
    headers = response_headers if response_headers is not None else {}

    class _FakeStream:
        def __init__(self):
            self.headers = headers

        def __enter__(self):
            return adafruit_json_stream.load(iter([raw]))

        def __exit__(self, *args):
            return False

    def _fake_get_stream(url, req_headers=None):
        return _FakeStream()

    return _fake_get_stream


def dict_to_stream(data):
    """Return a get_stream mock that streams the given dict as JSON bytes.

    Used for tests that build a custom hourly payload (e.g. modified null PoP).
    """
    raw = _json.dumps(data).encode()

    class _FakeStream:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return adafruit_json_stream.load(iter([raw]))

        def __exit__(self, *args):
            return False

    def _fake_get_stream(url, req_headers=None):
        return _FakeStream()

    return _fake_get_stream
