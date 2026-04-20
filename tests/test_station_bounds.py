"""Tests for Station.check_bounds() US bounding box."""
from station import Station


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
