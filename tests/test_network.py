"""Tests for src/network.py transport-error handling and session reset.

Covers:
  - _reset_session(): clears session, always calls connection_manager_close_all()
  - request(): returns None and resets session on transport errors; returns
    None without resetting on parse errors (ValueError); returns None on non-200
    responses without resetting; correctly routes GET vs POST body; populates
    out_headers on 200, leaves it untouched otherwise
  - _GetStream: returns None and resets session on transport errors; returns
    None on non-200 responses
  - _fmt_bytes(): pure formatting function

Note: adafruit_connection_manager is stubbed as MagicMock() in the test
environment, so these tests confirm the recovery code is correctly wired up.
They cannot simulate the actual hardware stale-socket state — that requires
the device smoke test described in the plan.
"""
import socket
import pytest
from unittest.mock import MagicMock, patch

import adafruit_connection_manager
from adafruit_requests import OutOfRetries
import network


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_raising(exc):
    """Return a mock session whose .get() and .post() raise exc immediately.

    The exception is raised when the method is called (before the with-block
    body), matching what adafruit_connection_manager raises when get_socket()
    sees a stale registered socket.
    """
    mock_session = MagicMock()
    mock_session.get.side_effect = exc
    mock_session.post.side_effect = exc
    return mock_session


def _make_session_returning(status_code):
    """Return a mock session whose .get() and .post() yield a response with the
    given HTTP status code.  The response is used as a context manager (via
    ``with session.get(...) as response`` in request()).
    """
    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status_code = status_code

    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    mock_session.post.return_value = mock_response
    return mock_session


# ---------------------------------------------------------------------------
# _reset_session
# ---------------------------------------------------------------------------

class TestResetSession:
    def setup_method(self):
        """Start each test with a clean session state."""
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    def test_clears_session(self):
        network._session = MagicMock()
        network._reset_session()
        assert network._session is None

    def test_calls_connection_manager_close_all(self):
        network._reset_session()
        adafruit_connection_manager.connection_manager_close_all.assert_called_once_with()

    def test_close_all_called_even_when_session_was_none(self):
        """Cold-boot path: _session is already None, but close_all still runs."""
        assert network._session is None
        network._reset_session()
        adafruit_connection_manager.connection_manager_close_all.assert_called_once_with()


# ---------------------------------------------------------------------------
# network.request() — transport errors reset the session
# ---------------------------------------------------------------------------

_TRANSPORT_ERRORS = [
    RuntimeError("An existing socket is already connected to https://api.weather.gov:443"),
    TimeoutError("timed out"),
    OutOfRetries(),
    ConnectionError("connection refused"),
    OSError("network down"),
    # The exact exception bin/simulate's Break DNS button raises: socket.gaierror
    # is an OSError subclass, so this confirms it lands in the right except clause.
    socket.gaierror(-2, "Name or service not known"),
    # The exact exception bin/simulate's Break ISP button raises: ETIMEDOUT from
    # the socket.socket subclass's connect() override.
    OSError(110, "Connection timed out"),
]


class TestRequestTransportErrors:
    """request() must return None and reset the session for all transport errors."""

    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    @pytest.mark.parametrize("err", _TRANSPORT_ERRORS)
    def test_get_returns_none(self, err):
        with patch.object(network, '_get_session', return_value=_make_session_raising(err)):
            assert network.request("GET", "https://api.weather.gov/test") is None

    @pytest.mark.parametrize("err", _TRANSPORT_ERRORS)
    def test_get_resets_session(self, err):
        with patch.object(network, '_get_session', return_value=_make_session_raising(err)), \
             patch.object(network, '_reset_session') as mock_reset:
            network.request("GET", "https://api.weather.gov/test")
        mock_reset.assert_called_once_with()

    @pytest.mark.parametrize("err", _TRANSPORT_ERRORS)
    def test_post_returns_none(self, err):
        with patch.object(network, '_get_session', return_value=_make_session_raising(err)):
            assert network.request("POST", "https://api.weather.gov/test", {}) is None

    @pytest.mark.parametrize("err", _TRANSPORT_ERRORS)
    def test_post_resets_session(self, err):
        with patch.object(network, '_get_session', return_value=_make_session_raising(err)), \
             patch.object(network, '_reset_session') as mock_reset:
            network.request("POST", "https://api.weather.gov/test", {})
        mock_reset.assert_called_once_with()


# ---------------------------------------------------------------------------
# network.request() — non-200 HTTP status
# ---------------------------------------------------------------------------

class TestRequestNonOkStatus:
    """Non-200 responses return None without resetting the session.

    A 4xx/5xx response means the socket completed successfully — there is no
    stale-socket condition to recover from.  Resetting the session here would
    discard a healthy connection unnecessarily.
    """

    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    @pytest.mark.parametrize("status", [400, 404, 500, 503])
    def test_get_returns_none_on_non_200(self, status):
        mock_session = _make_session_returning(status)
        with patch.object(network, '_get_session', return_value=mock_session):
            assert network.request("GET", "https://api.weather.gov/test") is None

    @pytest.mark.parametrize("status", [400, 404, 500, 503])
    def test_get_does_not_reset_session_on_non_200(self, status):
        mock_session = _make_session_returning(status)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_reset_session') as mock_reset:
            network.request("GET", "https://api.weather.gov/test")
        mock_reset.assert_not_called()


# ---------------------------------------------------------------------------
# network.request() — ValueError (JSON parse error)
# ---------------------------------------------------------------------------

class TestRequestValueError:
    """ValueError from _parse_json returns None but does NOT reset the session.

    A parse error means the response was received correctly — the socket is
    fine.  Only transport errors warrant session teardown.
    """

    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    def test_returns_none_on_value_error(self):
        mock_session = _make_session_returning(200)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_parse_json', side_effect=ValueError("invalid JSON")):
            assert network.request("GET", "https://api.weather.gov/test") is None

    def test_does_not_reset_session_on_value_error(self):
        mock_session = _make_session_returning(200)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_parse_json', side_effect=ValueError("invalid JSON")), \
             patch.object(network, '_reset_session') as mock_reset:
            network.request("GET", "https://api.weather.gov/test")
        mock_reset.assert_not_called()


# ---------------------------------------------------------------------------
# network.request() — out_headers parameter
# ---------------------------------------------------------------------------

class TestRequestOutHeaders:
    """out_headers dict is populated with response headers on 200; untouched otherwise."""

    def setup_method(self):
        network._session = None

    def test_populated_on_200(self):
        mock_session = _make_session_returning(200)
        mock_session.get.return_value.__enter__.return_value.headers = {
            'cache-control': 'public, max-age=3600',
            'content-type': 'application/geo+json',
        }
        out = {}
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_parse_json', return_value={}):
            network.request("GET", "https://api.weather.gov/test", out_headers=out)
        assert out.get('cache-control') == 'public, max-age=3600'
        assert out.get('content-type') == 'application/geo+json'

    def test_not_populated_on_non_200(self):
        mock_session = _make_session_returning(503)
        out = {}
        with patch.object(network, '_get_session', return_value=mock_session):
            network.request("GET", "https://api.weather.gov/test", out_headers=out)
        assert out == {}

    def test_not_populated_on_transport_error(self):
        out = {}
        with patch.object(network, '_get_session',
                          return_value=_make_session_raising(TimeoutError("timed out"))):
            network.request("GET", "https://api.weather.gov/test", out_headers=out)
        assert out == {}

    def test_none_out_headers_does_not_raise(self):
        """Default None value must not cause an AttributeError inside request()."""
        mock_session = _make_session_returning(200)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_parse_json', return_value={}):
            result = network.request("GET", "https://api.weather.gov/test")
        assert result == {}


# ---------------------------------------------------------------------------
# get_stream() (_GetStream context manager)
# ---------------------------------------------------------------------------

class TestGetStream:
    """_GetStream.__enter__ returns None on transport errors and non-200 status."""

    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    @pytest.mark.parametrize("err", _TRANSPORT_ERRORS)
    def test_yields_none_on_transport_error(self, err):
        mock_session = MagicMock()
        mock_session.get.side_effect = err
        with patch.object(network, '_get_session', return_value=mock_session):
            with network.get_stream("https://api.weather.gov/test") as stream:
                assert stream is None

    @pytest.mark.parametrize("err", _TRANSPORT_ERRORS)
    def test_resets_session_on_transport_error(self, err):
        mock_session = MagicMock()
        mock_session.get.side_effect = err
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_reset_session') as mock_reset:
            with network.get_stream("https://api.weather.gov/test"):
                pass
        mock_reset.assert_called_once_with()

    @pytest.mark.parametrize("status", [400, 404, 500, 503])
    def test_yields_none_on_non_200(self, status):
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        with patch.object(network, '_get_session', return_value=mock_session):
            with network.get_stream("https://api.weather.gov/test") as stream:
                assert stream is None

    @pytest.mark.parametrize("status", [400, 404, 500, 503])
    def test_does_not_reset_session_on_non_200(self, status):
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_reset_session') as mock_reset:
            with network.get_stream("https://api.weather.gov/test"):
                pass
        mock_reset.assert_not_called()


# ---------------------------------------------------------------------------
# _fmt_bytes
# ---------------------------------------------------------------------------

class TestFmtBytes:
    """_fmt_bytes formats byte counts as KB (>=1024) or B (< 1024)."""

    def test_zero_bytes(self):
        assert network._fmt_bytes(0) == "0 B"

    def test_small_bytes(self):
        assert network._fmt_bytes(512) == "512 B"

    def test_exactly_one_kb(self):
        assert network._fmt_bytes(1024) == "1.0 KB"

    def test_fractional_kb(self):
        assert network._fmt_bytes(1536) == "1.5 KB"

    def test_large_value(self):
        assert network._fmt_bytes(163840) == "160.0 KB"
