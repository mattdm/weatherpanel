"""Tests for Station.check_bounds() and Station.geolocate()."""
import network
from station import Station, MAX_RETRIES


def _make_station(lat, lon):
    """Create a Station with minimal config and set lat/lon."""
    config = {
        'GEOLOCATION_API': '',
        'GRIDPOINT_API': '',
        'HISTORICAL_API': '',
    }
    s = Station(config)
    s.lat = str(lat)
    s.lon = str(lon)
    return s


class TestCheckBounds:
    def test_continental_us(self):
        s = _make_station(42.39, -71.13)  # Boston
        s.check_bounds()
        assert not s.unsupported

    def test_alaska(self):
        s = _make_station(64.2, -152.5)  # Fairbanks
        s.check_bounds()
        assert not s.unsupported

    def test_hawaii(self):
        s = _make_station(21.3, -157.8)  # Honolulu
        s.check_bounds()
        assert not s.unsupported

    def test_london(self):
        s = _make_station(51.5, -0.12)
        s.check_bounds()
        assert s.unsupported

    def test_tokyo(self):
        s = _make_station(35.7, 139.7)
        s.check_bounds()
        assert s.unsupported

    def test_sydney(self):
        s = _make_station(-33.9, 151.2)
        s.check_bounds()
        assert s.unsupported

    def test_toronto_passes_box(self):
        """Toronto is inside the bounding box -- caught by NOAA 404 later."""
        s = _make_station(43.7, -79.4)
        s.check_bounds()
        assert not s.unsupported

    def test_none_lat_lon(self):
        """None coordinates should not crash or set unsupported."""
        s = _make_station(None, None)
        s.lat = None
        s.lon = None
        s.check_bounds()
        assert not s.unsupported

    def test_unsupported_only_from_bounds(self):
        """unsupported flag should only be set by check_bounds, not by default."""
        s = _make_station(42.39, -71.13)
        assert not s.unsupported
        s.check_bounds()
        assert not s.unsupported


# ---------------------------------------------------------------------------
# TestGeolocate
# ---------------------------------------------------------------------------

def _make_fresh_station():
    config = {
        'GEOLOCATION_API': 'http://test/geo',
        'GRIDPOINT_API': '',
        'HISTORICAL_API': '',
    }
    return Station(config)


class TestGeolocate:
    def test_sets_location_on_success(self, monkeypatch):
        s = _make_fresh_station()
        monkeypatch.setattr(network, "get", lambda url, **kw: {"lat": 42.39, "lon": -71.13})
        s.geolocate()
        assert s.location == "42.3900,-71.1300"

    def test_configured_lat_lon_skips_network(self, monkeypatch):
        config = {
            'GEOLOCATION_API': 'http://test/geo',
            'GRIDPOINT_API': '',
            'HISTORICAL_API': '',
            'LATITUDE': '42.39',
            'LONGITUDE': '-71.13',
        }
        s = Station(config)
        calls = []
        monkeypatch.setattr(network, "get", lambda url, **kw: calls.append(url) or {})
        s.geolocate()
        assert calls == []
        assert s.location == "42.39,-71.13"

    def test_returns_after_max_retries_on_null_response(self, monkeypatch):
        """geolocate() must exit after MAX_RETRIES null responses, not loop forever."""
        s = _make_fresh_station()
        calls = []
        monkeypatch.setattr(network, "get", lambda url, **kw: calls.append(url) or None)
        s.geolocate()
        assert s.location is None
        assert len(calls) == MAX_RETRIES + 1

    def test_returns_after_max_retries_on_malformed_response(self, monkeypatch):
        """geolocate() must exit after MAX_RETRIES when JSON has no lat/lon keys."""
        s = _make_fresh_station()
        calls = []
        monkeypatch.setattr(network, "get", lambda url, **kw: calls.append(url) or {"status": "error"})
        s.geolocate()
        assert s.location is None
        assert len(calls) == MAX_RETRIES + 1

    def test_extracts_timezone_from_response(self, monkeypatch):
        s = _make_fresh_station()
        monkeypatch.setattr(network, "get", lambda url, **kw: {
            "lat": 42.39, "lon": -71.13, "timezone": "America/New_York"
        })
        s.geolocate()
        assert s.tz == "America/New_York"
