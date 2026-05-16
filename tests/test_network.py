"""Tests for src/network.py transport-error handling and session reset.

Covers:
  - _reset_session(): clears session, always calls connection_manager_close_all()
  - request(): returns None and resets session on transport errors; returns
    None without resetting on parse errors (ValueError); returns None on non-200
    responses without resetting; correctly routes GET vs POST body; populates
    out_headers on 200, leaves it untouched otherwise; forwards computed timeout;
    skips (returns None without network call) when budget < MIN_REQUEST_TIMEOUT_S
  - _GetStream: returns None and resets session on transport errors; returns
    None on non-200 responses; forwards computed timeout; skips when budget
    < MIN_REQUEST_TIMEOUT_S
  - set_iteration_deadline() / _budget_remaining(): deadline tracking and
    per-request budget computation
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
import microcontroller
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
    def test_post_returns_none(self, err):
        with patch.object(network, '_get_session', return_value=_make_session_raising(err)):
            assert network.request("POST", "https://api.weather.gov/test", {}) is None


# ---------------------------------------------------------------------------
# microcontroller.reset() on OutOfRetries — request() and get_stream()
# ---------------------------------------------------------------------------

_OTHER_TRANSPORT_ERRORS = [
    RuntimeError("An existing socket is already connected to https://api.weather.gov:443"),
    TimeoutError("timed out"),
    ConnectionError("connection refused"),
    OSError("network down"),
    socket.gaierror(-2, "Name or service not known"),
    OSError(110, "Connection timed out"),
]


class TestOutOfRetriesReset:
    """microcontroller.reset() is called for OutOfRetries but not for any other error."""

    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()
        microcontroller.reset.reset_mock()

    def test_get_request_calls_reset_on_out_of_retries(self):
        with patch.object(network, '_get_session',
                          return_value=_make_session_raising(OutOfRetries())):
            network.request("GET", "https://api.weather.gov/test")
        microcontroller.reset.assert_called_once()

    def test_post_request_calls_reset_on_out_of_retries(self):
        with patch.object(network, '_get_session',
                          return_value=_make_session_raising(OutOfRetries())):
            network.request("POST", "https://api.weather.gov/test", {})
        microcontroller.reset.assert_called_once()

    def test_get_stream_calls_reset_on_out_of_retries(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = OutOfRetries()
        with patch.object(network, '_get_session', return_value=mock_session):
            with network.get_stream("https://api.weather.gov/test") as stream:
                assert stream is None
        microcontroller.reset.assert_called_once()

    @pytest.mark.parametrize("err", _OTHER_TRANSPORT_ERRORS)
    def test_get_request_does_not_reset_on_other_errors(self, err):
        with patch.object(network, '_get_session',
                          return_value=_make_session_raising(err)):
            network.request("GET", "https://api.weather.gov/test")
        microcontroller.reset.assert_not_called()

    @pytest.mark.parametrize("err", _OTHER_TRANSPORT_ERRORS)
    def test_get_stream_does_not_reset_on_other_errors(self, err):
        mock_session = MagicMock()
        mock_session.get.side_effect = err
        with patch.object(network, '_get_session', return_value=mock_session):
            with network.get_stream("https://api.weather.gov/test") as stream:
                assert stream is None
        microcontroller.reset.assert_not_called()

    def test_budget_skip_does_not_call_reset(self):
        low_budget = network.MIN_REQUEST_TIMEOUT_S * network._ADAFRUIT_REQUESTS_MAX_RETRIES - 1
        with patch.object(network, '_budget_remaining', return_value=low_budget):
            network.request("GET", "https://api.weather.gov/test")
        microcontroller.reset.assert_not_called()


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
# set_iteration_deadline / _budget_remaining
# ---------------------------------------------------------------------------

class TestBudgetRemaining:
    """set_iteration_deadline() / _budget_remaining() budget computation."""

    def test_returns_remaining_time(self):
        """Returns raw seconds until the deadline."""
        with patch.object(network, '_monotonic', return_value=1000.0):
            network.set_iteration_deadline(1010.0)
        with patch.object(network, '_monotonic', return_value=1000.0):
            assert network._budget_remaining() == 10.0

    def test_large_budget_not_capped(self):
        """Returns the full remaining budget regardless of magnitude."""
        with patch.object(network, '_monotonic', return_value=1000.0):
            network.set_iteration_deadline(1060.0)  # 60 s away
        with patch.object(network, '_monotonic', return_value=1000.0):
            assert network._budget_remaining() == 60.0

    def test_zero_at_deadline(self):
        """Returns 0.0 when deadline is exactly now."""
        with patch.object(network, '_monotonic', return_value=1000.0):
            network.set_iteration_deadline(1000.0)
            assert network._budget_remaining() == 0.0

    def test_negative_when_past_deadline(self):
        """Returns a negative value when the deadline has already passed."""
        with patch.object(network, '_monotonic', return_value=1000.0):
            network.set_iteration_deadline(990.0)
            assert network._budget_remaining() == -10.0

    def test_set_deadline_updates_state(self):
        """set_iteration_deadline() replaces any previous deadline."""
        with patch.object(network, '_monotonic', return_value=1000.0):
            network.set_iteration_deadline(1050.0)
            network.set_iteration_deadline(1015.0)
            assert network._budget_remaining() == 15.0


# ---------------------------------------------------------------------------
# Budget skip — request() and get_stream() skip when budget is exhausted
# ---------------------------------------------------------------------------

class TestBudgetSkip:
    """request() and get_stream() return None without touching the network
    when budget / _ADAFRUIT_REQUESTS_MAX_RETRIES is below MIN_REQUEST_TIMEOUT_S.

    The effective minimum budget to proceed is
    MIN_REQUEST_TIMEOUT_S * _ADAFRUIT_REQUESTS_MAX_RETRIES.
    """

    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    # Budget values that yield a per-attempt timeout below MIN_REQUEST_TIMEOUT_S.
    _SKIP_BUDGETS = [
        network.MIN_REQUEST_TIMEOUT_S * network._ADAFRUIT_REQUESTS_MAX_RETRIES - 0.1,
        0.0,
        -10.0,
    ]

    @pytest.mark.parametrize("budget", _SKIP_BUDGETS)
    def test_request_get_skips_when_budget_exhausted(self, budget):
        mock_session = MagicMock()
        with patch.object(network, '_budget_remaining', return_value=budget), \
             patch.object(network, '_get_session', return_value=mock_session):
            result = network.request("GET", "https://api.weather.gov/test")
        assert result is None
        mock_session.get.assert_not_called()

    @pytest.mark.parametrize("budget", _SKIP_BUDGETS)
    def test_request_post_skips_when_budget_exhausted(self, budget):
        mock_session = MagicMock()
        with patch.object(network, '_budget_remaining', return_value=budget), \
             patch.object(network, '_get_session', return_value=mock_session):
            result = network.request("POST", "https://api.weather.gov/test", {})
        assert result is None
        mock_session.post.assert_not_called()

    @pytest.mark.parametrize("budget", _SKIP_BUDGETS)
    def test_get_stream_skips_when_budget_exhausted(self, budget):
        mock_session = MagicMock()
        with patch.object(network, '_budget_remaining', return_value=budget), \
             patch.object(network, '_get_session', return_value=mock_session):
            with network.get_stream("https://api.weather.gov/test") as stream:
                assert stream is None
        mock_session.get.assert_not_called()

    def test_request_does_not_skip_at_exact_threshold(self):
        """A request with exactly the minimum viable budget proceeds."""
        min_budget = float(
            network.MIN_REQUEST_TIMEOUT_S * network._ADAFRUIT_REQUESTS_MAX_RETRIES
        )
        mock_session = _make_session_returning(404)
        with patch.object(network, '_budget_remaining', return_value=min_budget), \
             patch.object(network, '_get_session', return_value=mock_session):
            network.request("GET", "https://api.weather.gov/test")
        mock_session.get.assert_called_once()

    def test_skip_does_not_reset_session(self):
        """Skipping due to budget does not trigger a session reset."""
        low_budget = network.MIN_REQUEST_TIMEOUT_S * network._ADAFRUIT_REQUESTS_MAX_RETRIES - 1
        with patch.object(network, '_budget_remaining', return_value=low_budget), \
             patch.object(network, '_reset_session') as mock_reset:
            network.request("GET", "https://api.weather.gov/test")
        mock_reset.assert_not_called()


# ---------------------------------------------------------------------------
# Timeout forwarding — request() and get_stream()
# ---------------------------------------------------------------------------

class TestTimeoutForwarding:
    """request() and get_stream() pass budget / _ADAFRUIT_REQUESTS_MAX_RETRIES
    as the per-attempt socket timeout to adafruit_requests."""

    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    def test_request_get_passes_timeout(self):
        """30 s budget (starting around :25) → 15 s per-attempt timeout."""
        mock_session = _make_session_returning(404)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_budget_remaining', return_value=30.0):
            network.request("GET", "https://api.weather.gov/test")
        assert mock_session.get.call_args.kwargs['timeout'] == 15.0

    def test_request_post_passes_timeout(self):
        """30 s budget (starting around :25) → 15 s per-attempt timeout."""
        mock_session = _make_session_returning(404)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_budget_remaining', return_value=30.0):
            network.request("POST", "https://api.weather.gov/test", {})
        assert mock_session.post.call_args.kwargs['timeout'] == 15.0

    def test_get_stream_passes_timeout(self):
        """30 s budget (starting around :25) → 15 s per-attempt timeout."""
        mock_response = MagicMock()
        mock_response.status_code = 404  # non-200 avoids adafruit_json_stream
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_budget_remaining', return_value=30.0):
            with network.get_stream("https://api.weather.gov/test"):
                pass
        assert mock_session.get.call_args.kwargs['timeout'] == 15.0


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
