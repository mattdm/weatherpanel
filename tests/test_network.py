"""Tests for src/network.py transport-error handling and session reset.

These tests verify that:
  - _reset_session() clears the session and calls connection_manager_close_all()
  - network.request() catches RuntimeError from the transport layer
    (the "existing socket already connected" error raised by adafruit_connection_manager
    when a previous request left a socket registered but not released)

Note: adafruit_connection_manager is stubbed as MagicMock() in the test
environment, so these tests confirm the recovery code is correctly wired up.
They cannot simulate the actual hardware stale-socket state; that requires
the device smoke test described in the plan.
"""
from unittest.mock import MagicMock, patch

import adafruit_connection_manager
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
# network.request() GET — RuntimeError from connection layer
# ---------------------------------------------------------------------------

class TestGetCatchesRuntimeError:
    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    def test_returns_none_on_runtime_error(self):
        err = RuntimeError("An existing socket is already connected to https://api.weather.gov:443")
        mock_session = _make_session_raising(err)
        with patch.object(network, '_get_session', return_value=mock_session):
            result = network.request("GET", "https://api.weather.gov/test")
        assert result is None

    def test_calls_reset_session_on_runtime_error(self):
        err = RuntimeError("An existing socket is already connected to https://api.weather.gov:443")
        mock_session = _make_session_raising(err)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_reset_session') as mock_reset:
            network.request("GET", "https://api.weather.gov/test")
        mock_reset.assert_called_once_with()

    def test_does_not_raise_on_runtime_error(self):
        """RuntimeError must not propagate to the caller."""
        err = RuntimeError("An existing socket is already connected to https://api.weather.gov:443")
        mock_session = _make_session_raising(err)
        with patch.object(network, '_get_session', return_value=mock_session):
            # Should complete without raising.
            network.request("GET", "https://api.weather.gov/test")


# ---------------------------------------------------------------------------
# network.request() POST — RuntimeError from connection layer
# ---------------------------------------------------------------------------

class TestPostCatchesRuntimeError:
    def setup_method(self):
        network._session = None
        adafruit_connection_manager.connection_manager_close_all.reset_mock()

    def test_returns_none_on_runtime_error(self):
        err = RuntimeError("An existing socket is already connected to https://api.weather.gov:443")
        mock_session = _make_session_raising(err)
        with patch.object(network, '_get_session', return_value=mock_session):
            result = network.request("POST", "https://api.weather.gov/test", {"key": "value"})
        assert result is None

    def test_calls_reset_session_on_runtime_error(self):
        err = RuntimeError("An existing socket is already connected to https://api.weather.gov:443")
        mock_session = _make_session_raising(err)
        with patch.object(network, '_get_session', return_value=mock_session), \
             patch.object(network, '_reset_session') as mock_reset:
            network.request("POST", "https://api.weather.gov/test", {"key": "value"})
        mock_reset.assert_called_once_with()

    def test_does_not_raise_on_runtime_error(self):
        """RuntimeError must not propagate to the caller."""
        err = RuntimeError("An existing socket is already connected to https://api.weather.gov:443")
        mock_session = _make_session_raising(err)
        with patch.object(network, '_get_session', return_value=mock_session):
            network.request("POST", "https://api.weather.gov/test", {"key": "value"})
