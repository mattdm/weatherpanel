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

    def _fake_get_stream(url, req_headers=None, min_budget_s=None):
        return _FakeStream()

    return _fake_get_stream


def make_griddata_stream(fixture_name, response_headers=None):
    """Return a get_stream-compatible context manager backed by a griddata fixture file.

    Identical in structure to make_hourly_stream — both serve adafruit_json_stream
    objects — but named separately for clarity in tests that patch get_stream for
    griddata responses.

    Usage:
        monkeypatch.setattr(network, "get_stream",
                            make_griddata_stream("boston_griddata.json"))

        monkeypatch.setattr(network, "get_stream",
                            make_griddata_stream("boston_griddata.json",
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

    def _fake_get_stream(url, req_headers=None, min_budget_s=None):
        return _FakeStream()

    return _fake_get_stream


def dict_to_stream(data, response_headers=None):
    """Return a get_stream mock that streams the given dict as JSON bytes.

    Used for tests that build a custom payload (e.g. modified null PoP, minimal
    griddata dicts).  ``response_headers`` is exposed via ``.headers`` so
    Cache-Control tests can inject header values.
    """
    raw = _json.dumps(data).encode()
    headers = response_headers if response_headers is not None else {}

    class _FakeStream:
        def __init__(self):
            self.headers = headers

        def __enter__(self):
            return adafruit_json_stream.load(iter([raw]))

        def __exit__(self, *args):
            return False

    def _fake_get_stream(url, req_headers=None, min_budget_s=None):
        return _FakeStream()

    return _fake_get_stream


def make_stream_with_transport_error(fixture_name, truncate_at_bytes):
    """Return a get_stream mock that raises OSError mid-body.

    Yields the first ``truncate_at_bytes`` of the fixture as a single chunk,
    then raises ``OSError(116, "ETIMEDOUT")`` on the next read.

    Because adafruit_json_stream does not catch OSError (confirmed by the real
    crash traceback: OSError propagated from _readinto through __getitem__
    without interception), the error fires exactly as it does in production when
    a socket recv() times out mid-stream.

    Choose ``truncate_at_bytes`` so that the fixture includes the fields you
    want parsed before the error, but is short enough that adafruit_json_stream
    will need to read past the end to complete parsing.

    Usage:
        monkeypatch.setattr(network, "get_stream",
                            make_stream_with_transport_error(
                                "soda_springs_hourly.json", 400))
    """
    raw = _load_bytes(fixture_name)
    partial = raw[:truncate_at_bytes]

    class _FakeStream:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            def _chunks():
                yield partial
                raise OSError(116, "ETIMEDOUT")
            return adafruit_json_stream.load(_chunks())

        def __exit__(self, *args):
            return False

    def _fake_get_stream(url, req_headers=None, min_budget_s=None):
        return _FakeStream()

    return _fake_get_stream


def make_stream_router(hourly_fn, griddata_fn):
    """Return a get_stream mock that routes by URL substring.

    Dispatches URLs containing ``griddata`` or ``grid`` (case-insensitive) to
    ``griddata_fn``, and all other URLs to ``hourly_fn``.  Used when both
    get_hourly_forecast() and get_griddata() are called within a single test
    and each needs a different fixture.

    Usage:
        monkeypatch.setattr(network, "get_stream", make_stream_router(
            make_hourly_stream("boston_hourly.json"),
            make_griddata_stream("boston_griddata.json"),
        ))
    """
    def _fake(url, req_headers=None, min_budget_s=None):
        if 'griddata' in url or 'grid' in url.lower():
            return griddata_fn(url, req_headers)
        return hourly_fn(url, req_headers)

    return _fake
