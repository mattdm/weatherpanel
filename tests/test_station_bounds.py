"""Tests for Station.check_bounds(), geolocate(), and get_station() with NOAA 404s.

The bounding box (lat 17–72, lon -180–-64) is intentionally generous — it
covers all 50 US states but also includes most of Mexico, southern Canada,
Cuba, the Pacific Ocean between the US and Hawaii, and significant Atlantic
waters.  check_bounds() flags only coordinates definitively outside that box;
everything else falls through to the NOAA points API, which returns 404 for
non-US locations and handles the case via the retry loop in get_station().
"""
import network
from station import MAX_RETRIES, NOAA_METADATA_MIN_BUDGET_S, Station


def _make_station(lat, lon):
    """Create a Station with minimal config and set lat/lon."""
    config = {
        'GRIDPOINT_API': '',
        'HISTORICAL_API': '',
    }
    s = Station(config)
    s.lat = str(lat) if lat is not None else None
    s.lon = str(lon) if lon is not None else None
    return s


class TestCheckBounds:
    def test_continental_us_not_flagged(self):
        s = _make_station(42.39, -71.13)  # Boston
        s.check_bounds()
        assert not s.unsupported

    def test_foreign_city_flagged(self):
        s = _make_station(51.5, -0.12)  # London — east of -64
        s.check_bounds()
        assert s.unsupported

    # ------------------------------------------------------------------
    # Bounding box edge cases
    # ------------------------------------------------------------------

    def test_exactly_on_lat_min_inside(self):
        s = _make_station(17.0, -80.0)
        s.check_bounds()
        assert not s.unsupported

    def test_just_below_lat_min_outside(self):
        s = _make_station(16.9, -80.0)
        s.check_bounds()
        assert s.unsupported

    def test_exactly_on_lat_max_inside(self):
        s = _make_station(72.0, -155.0)
        s.check_bounds()
        assert not s.unsupported

    def test_just_above_lat_max_outside(self):
        s = _make_station(72.1, -155.0)
        s.check_bounds()
        assert s.unsupported

    def test_exactly_on_lon_max_inside(self):
        s = _make_station(32.0, -64.0)
        s.check_bounds()
        assert not s.unsupported

    def test_just_east_of_lon_max_outside(self):
        s = _make_station(32.3, -63.9)
        s.check_bounds()
        assert s.unsupported

    def test_none_lat_lon_no_crash_and_not_flagged(self):
        s = _make_station(None, None)
        s.check_bounds()
        assert not s.unsupported


# ---------------------------------------------------------------------------
# Locations inside the bounding box but outside NOAA coverage — the retry
# loop in get_station() handles these (network.request returns None → retry).
# ---------------------------------------------------------------------------

def _make_station_with_api(lat, lon):
    config = {
        'GRIDPOINT_API':  'https://api.weather.gov/points',
        'HISTORICAL_API': 'https://data.rcc-acis.org/GridData',
    }
    s = Station(config)
    s.lat = str(lat)
    s.lon = str(lon)
    s.location = f"{lat},{lon}"
    return s


class TestGetStationOutsideNoaaRange:
    """get_station() handles NOAA 404 (network.request → None) without crashing."""

    def test_outside_coverage_exhausts_retries_gracefully(self, monkeypatch):
        """Vancouver BC: inside box, outside NOAA coverage → MAX_RETRIES attempts, no crash."""
        calls = []

        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            calls.append(url)

        monkeypatch.setattr(network, 'request', fake_request)
        s = _make_station_with_api(49.3, -123.1)
        s.get_station()  # must not raise

        assert s.station_id is None
        assert s.hourly_url is None
        assert len(calls) == MAX_RETRIES

    def test_correct_url_includes_coordinates(self, monkeypatch):
        calls = []

        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            calls.append(url)

        monkeypatch.setattr(network, 'request', fake_request)
        s = _make_station_with_api(49.3, -123.1)
        s.get_station()

        assert all("49.3" in url and "-123.1" in url for url in calls)


class TestGetStationBudgetBailout:
    """get_station() skips retries and returns immediately when budget is exhausted."""

    def test_returns_after_one_attempt_when_budget_exhausted(self, monkeypatch):
        calls = []

        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            calls.append(url)

        monkeypatch.setattr(network, 'request', fake_request)
        monkeypatch.setattr(network, '_budget_remaining', lambda: NOAA_METADATA_MIN_BUDGET_S - 1)

        s = _make_station_with_api(42.39, -71.10)
        s.get_station()

        assert len(calls) == 1
        assert s.station_id is None

    def test_ample_budget_exhausts_max_retries(self, monkeypatch):
        calls = []

        def fake_request(verb, url, body=None, headers=None, out_headers=None, min_budget_s=None):
            calls.append(url)

        monkeypatch.setattr(network, 'request', fake_request)
        monkeypatch.setattr(network, '_budget_remaining', lambda: NOAA_METADATA_MIN_BUDGET_S + 10)

        s = _make_station_with_api(42.39, -71.10)
        s.get_station()

        assert len(calls) == MAX_RETRIES


# ---------------------------------------------------------------------------
# TestGeolocate
# ---------------------------------------------------------------------------

def _make_fresh_station():
    return Station({'GRIDPOINT_API': '', 'HISTORICAL_API': ''})


class TestGeolocate:
    def test_configured_lat_lon_makes_no_network_call(self, monkeypatch):
        config = {
            'GRIDPOINT_API': '',
            'HISTORICAL_API': '',
            'LATITUDE': '42.39',
            'LONGITUDE': '-71.13',
        }
        s = Station(config)
        calls = []
        monkeypatch.setattr(network, "request", lambda *a, **kw: calls.append(1) or {})
        s.geolocate()
        assert calls == []

    def test_missing_lat_leaves_location_none(self):
        s = _make_fresh_station()
        s.geolocate()
        assert s.location is None

    def test_missing_lon_leaves_location_none(self):
        config = {'GRIDPOINT_API': '', 'HISTORICAL_API': '', 'LATITUDE': '42.39'}
        s = Station(config)
        s.geolocate()
        assert s.location is None
