"""Integration test for the Wi-Fi configuration portal setup flow.

Runs portal.run() in a daemon thread with a real stdlib HTTP server bound to
a local port (replacing adafruit_httpserver, which is a MagicMock in the test
environment).  The test makes actual TCP/HTTP GET and POST requests against
that local server to exercise the complete portal cycle:

    portal.run()
      → network.start_ap / scan / ap_ip   (mocked)
      → display setup + QR generation     (real, via displayio_sim)
      → _make_server                      (replaced: real stdlib HTTPServer)
      ↓
    GET /       → verifies HTML form is served with SSID dropdown
    POST /      → verifies settings merge + save + success response
      ↓
    supervisor.reload() called → portal thread exits cleanly
      ↓
    assert settings file content is correct
"""
import itertools
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import network
import portal

_FONTS_DIR = Path(__file__).parent.parent / "fonts"


# ---------------------------------------------------------------------------
# Pre-built stdlib HTTP server shim
# ---------------------------------------------------------------------------

def _build_test_server(settings_file):
    """Create and bind an HTTPServer on a free local port for the portal test.

    Returns ``(shim, state, port)``.

    The HTTP server runs in its own dedicated daemon thread so it is always
    ready to accept connections — decoupled from the portal's event loop.
    The shim's ``poll()`` method is a no-op; the portal loop checks
    ``state['reload_pending']`` after each call and calls supervisor.reload()
    when the POST handler sets it.

    The POST handler calls portal.merge_settings() and writes to
    ``settings_file``, mirroring what the device does without touching
    any hardware or the real /settings.toml path.
    """
    _networks = [("TestNet-5G", -42), ("TestNet", -60)]
    state = {"last_request_t": 0.0}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass  # suppress output during tests

        def _send(self, body, content_type="text/html", code=200):
            enc = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(enc)))
            self.end_headers()
            self.wfile.write(enc)

        def do_GET(self):
            state["last_request_t"] = time.monotonic()
            if self.path == "/scan":
                fresh = network.scan_networks()
                self._send(portal._ssid_options(fresh))
            else:
                self._send(portal._form_html(_networks))

        def do_POST(self):
            state["last_request_t"] = time.monotonic()
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            fields = urllib.parse.parse_qs(raw, keep_blank_values=True)
            form_data = {k: v[0] for k, v in fields.items()}

            old_content = settings_file.read_text() if settings_file.exists() else ""
            new_content = portal.merge_settings(form_data, old_content)
            settings_file.write_text(new_content)

            state["reload_pending"] = True
            self._send(portal._success_html(new_content))

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.timeout = 1.0   # block up to 1s per handle_request in the server thread

    port = srv.server_address[1]

    # The server runs in its own daemon thread, always ready to accept
    # connections.  This decouples request handling from the portal's event
    # loop so there is no race between portal initialization and urlopen.
    def _serve():
        for _ in itertools.count():
            srv.handle_request()

    _srv_thread = threading.Thread(target=_serve, daemon=True,
                                   name="portal-test-httpserver")
    _srv_thread.start()

    class _Shim:
        """No-op shim — HTTP handling happens in _srv_thread.

        The portal's event loop calls poll() on every iteration; the actual
        work is done by the server thread.  After returning from poll(), the
        portal checks state['reload_pending'] and calls supervisor.reload().
        """
        def poll(self):
            time.sleep(0.01)  # yield to other threads; prevent busy-spin

    return _Shim(), state, port


# ---------------------------------------------------------------------------
# Portal setup flow test
# ---------------------------------------------------------------------------

class _PortalDone(Exception):
    """Raised by the mocked supervisor.reload() to exit portal.run()."""


class TestPortalSetupFlow:
    def test_get_form_and_post_settings(self, tmp_path, monkeypatch):
        """Full portal cycle: GET form → POST settings → supervisor.reload().

        Verifies:
        - GET / returns a valid HTML configuration form with all expected fields
        - POST / with SSID + password + lat/lon returns the success page
        - The settings file is written with the correct KEY = "value" lines
        - supervisor.reload() is called after a successful save
        """
        import adafruit_bitmap_font.bitmap_font as _bmp_font
        import matrix as _matrix_mod
        import matrix_sim
        import supervisor as _supervisor_mod
        import wifi as _wifi

        settings_file = tmp_path / "settings.toml"
        settings_file.write_text("")

        # Pre-create and bind the HTTP server so the port is known before
        # portal.run() is called, avoiding any race between port discovery
        # and server startup.
        shim, server_state, port = _build_test_server(settings_file)

        # _portal_ready fires when portal.run() calls _make_server(), i.e.
        # after font load + QR generation — just before the event loop starts.
        _portal_ready = threading.Event()

        def _make_server_fn(ip, nets, current_values=None, config_errors=None):
            _portal_ready.set()
            return (shim, server_state)

        monkeypatch.setattr(portal, "_make_server", _make_server_fn)

        # Font path redirect — fonts/ is in the repo, not at /fonts/ on disk.
        _orig_load = _bmp_font.load_font
        monkeypatch.setattr(
            _bmp_font, "load_font",
            lambda path: _orig_load(str(_FONTS_DIR / Path(path).name)),
        )

        # Display sim — no hardware init.
        monkeypatch.setattr(_matrix_mod, "display_set_root",
                            matrix_sim.display_set_root)

        # Network mocks.
        monkeypatch.setattr(network, "start_ap",        lambda ssid, password=None: None)
        monkeypatch.setattr(network, "stop_ap",         lambda: None)
        monkeypatch.setattr(network, "ap_ip",           lambda: f"127.0.0.1:{port}")
        monkeypatch.setattr(network, "scan_networks",
                            lambda: [("TestNet-5G", -42), ("TestNet", -60)])
        # Unconfigured device → wifi_configured returns False so the portal
        # does not auto-reload on AP_CYCLE_S expiry.
        monkeypatch.setattr(network, "wifi_configured", lambda config: False)

        # wifi.radio.stations_ap: truthy → portal treats a client as connected.
        # This prevents the portal from blocking in the WiFi-QR display state
        # waiting for a phone to connect.
        monkeypatch.setattr(_wifi.radio, "stations_ap", [object()])

        # Freeze monotonic at 0.0.
        #   • prevents AP_CYCLE_S auto-reload (0 < 1800)
        #   • prevents CLIENT_CHECK_INTERVAL_S check from firing (Δ = 0)
        monkeypatch.setattr(portal, "monotonic", lambda: 0.0)

        # Mock sleep to eliminate the 1.5s interstitial delays.
        monkeypatch.setattr(portal, "sleep", lambda t: None)

        # supervisor.reload: signal the main thread and exit portal.run().
        _reloaded = threading.Event()

        def _fake_reload():
            _reloaded.set()
            raise _PortalDone()

        monkeypatch.setattr(_supervisor_mod, "reload", _fake_reload)

        # Config: freshly unboxed device with no SSID configured.
        # AP_SSID must be short: WiFi QR uses Version 2 / EC-L (max 26 bytes).
        # "WIFI:T:nopass;S:WP;;" = 20 bytes — safely within the limit.
        config = {
            "CIRCUITPY_WIFI_SSID": "",
            "AP_SSID":             "WP",
            "AP_PASSWORD":         None,
            "SWAP_GREEN_BLUE":     False,
            "USER_AGENT":          None,
        }

        # Run portal in a daemon thread so the main thread can make requests.
        _thread_exc = [None]

        def _run():
            try:
                portal.run(config)
            except _PortalDone:
                pass
            except Exception as e:
                _thread_exc[0] = e

        t = threading.Thread(target=_run, daemon=True, name="portal-integration")
        t.start()

        # Wait for portal to complete initialization (font load, QR gen) and
        # enter its event loop — _portal_ready fires just before the loop.
        assert _portal_ready.wait(timeout=15), \
            "portal._make_server() was not called within 15s — initialization hung"

        url = f"http://127.0.0.1:{port}/"

        # --- GET / -------------------------------------------------------
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                assert resp.status == 200
                get_body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise AssertionError(
                f"GET {url} failed — portal did not respond within 10s: {exc}"
            )

        assert "WeatherPanel Setup" in get_body, \
            "GET / did not return the WeatherPanel Setup form"
        assert 'name="ssid"'            in get_body
        assert 'name="password"'        in get_body
        assert 'name="lat"'             in get_body
        assert 'name="lon"'             in get_body
        assert 'name="temp_min"' in get_body
        assert 'name="temp_max"' in get_body
        assert 'method="POST"'          in get_body

        # --- POST / ------------------------------------------------------
        form_payload = urllib.parse.urlencode({
            "ssid":     "MyHomeNetwork",
            "password": "s3cr3t",
            "lat":      "42.3601",
            "lon":      "-71.0589",
        }).encode("utf-8")
        post_req = urllib.request.Request(url, data=form_payload, method="POST")
        try:
            with urllib.request.urlopen(post_req, timeout=10) as resp:
                assert resp.status == 200
                post_body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise AssertionError(
                f"POST {url} failed — portal did not respond within 10s: {exc}"
            )

        assert "Settings saved" in post_body, \
            f"POST / did not return the success page; got: {post_body[:200]!r}"

        # --- supervisor.reload() called ----------------------------------
        assert _reloaded.wait(timeout=5.0), \
            "supervisor.reload() was not called within 5s of the POST"
        t.join(timeout=2.0)

        if _thread_exc[0] is not None:
            raise _thread_exc[0]

        # --- Settings file content ---------------------------------------
        content = settings_file.read_text()
        assert 'CIRCUITPY_WIFI_SSID = "MyHomeNetwork"'   in content
        assert 'CIRCUITPY_WIFI_PASSWORD = "s3cr3t"'      in content
        assert 'LATITUDE = "42.3601"'                    in content
        assert 'LONGITUDE = "-71.0589"'                  in content

    def test_scan_endpoint_returns_options(self, tmp_path, monkeypatch):
        """GET /scan returns fresh SSID option elements as HTML fragments."""
        import adafruit_bitmap_font.bitmap_font as _bmp_font
        import matrix as _matrix_mod
        import matrix_sim
        import supervisor as _supervisor_mod
        import wifi as _wifi

        settings_file = tmp_path / "settings.toml"
        settings_file.write_text("")

        shim, server_state, port = _build_test_server(settings_file)

        _portal_ready = threading.Event()

        def _make_server_fn(ip, nets, current_values=None, config_errors=None):
            _portal_ready.set()
            return (shim, server_state)

        monkeypatch.setattr(portal, "_make_server", _make_server_fn)

        _orig_load = _bmp_font.load_font
        monkeypatch.setattr(
            _bmp_font, "load_font",
            lambda path: _orig_load(str(_FONTS_DIR / Path(path).name)),
        )
        monkeypatch.setattr(_matrix_mod, "display_set_root",
                            matrix_sim.display_set_root)
        monkeypatch.setattr(network, "start_ap",        lambda ssid, password=None: None)
        monkeypatch.setattr(network, "stop_ap",         lambda: None)
        monkeypatch.setattr(network, "ap_ip",           lambda: f"127.0.0.1:{port}")
        monkeypatch.setattr(network, "scan_networks",
                            lambda: [("FreshScan-5G", -38)])
        monkeypatch.setattr(network, "wifi_configured", lambda config: False)
        monkeypatch.setattr(_wifi.radio, "stations_ap", [object()])
        monkeypatch.setattr(portal, "monotonic",        lambda: 0.0)
        monkeypatch.setattr(portal, "sleep",            lambda t: None)

        _reloaded = threading.Event()

        def _fake_reload():
            _reloaded.set()
            raise _PortalDone()

        monkeypatch.setattr(_supervisor_mod, "reload", _fake_reload)

        config = {
            "CIRCUITPY_WIFI_SSID": "",
            "AP_SSID":             "WP",
            "AP_PASSWORD":         None,
            "SWAP_GREEN_BLUE":     False,
            "USER_AGENT":          None,
        }

        _thread_exc = [None]

        def _run():
            try:
                portal.run(config)
            except _PortalDone:
                pass
            except Exception as e:
                _thread_exc[0] = e

        t = threading.Thread(target=_run, daemon=True, name="portal-scan-test")
        t.start()

        assert _portal_ready.wait(timeout=15), \
            "portal._make_server() was not called within 15s"

        scan_url = f"http://127.0.0.1:{port}/scan"
        try:
            with urllib.request.urlopen(scan_url, timeout=10) as resp:
                assert resp.status == 200
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise AssertionError(f"GET /scan failed: {exc}")

        assert "FreshScan-5G" in body
        assert "-38 dBm" in body
        assert "<option" in body

        if _thread_exc[0] is not None:
            raise _thread_exc[0]
