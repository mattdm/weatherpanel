"""Tests for Station.check_bounds(), geolocate(), and get_station() with NOAA 404s.

The bounding box (lat 17–72, lon -180–-64) is intentionally generous — it
covers all 50 US states but also includes most of Mexico, southern Canada,
Cuba, the Pacific Ocean between the US and Hawaii, and significant Atlantic
waters.  check_bounds() flags only coordinates definitively outside that box;
everything else falls through to the NOAA points API, which returns 404 for
non-US locations and handles the case via the retry loop in get_station().
"""
import network
from station import MAX_RETRIES, Station


def _make_station(lat, lon):
    """Create a Station with minimal config and set lat/lon."""
    config = {
        'GRIDPOINT_API': '',
        'HISTORICAL_API': '',
    }
    s = Station(config)
    s.lat = str(lat)
    s.lon = str(lon)
    return s


class TestCheckBounds:
    # ------------------------------------------------------------------
    # US locations — must not be flagged
    # ------------------------------------------------------------------

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

    def test_puerto_rico(self):
        s = _make_station(18.47, -66.12)  # San Juan — near the southeast corner
        s.check_bounds()
        assert not s.unsupported

    def test_far_west_alaska(self):
        s = _make_station(71.3, -156.8)  # Utqiagvik — near the northwest corner
        s.check_bounds()
        assert not s.unsupported

    # ------------------------------------------------------------------
    # Clearly outside the bounding box
    # ------------------------------------------------------------------

    def test_london(self):
        """Eastern Atlantic — longitude east of -64."""
        s = _make_station(51.5, -0.12)
        s.check_bounds()
        assert s.unsupported

    def test_reykjavik(self):
        """Iceland — longitude east of -64 (lon=-22)."""
        s = _make_station(64.1, -21.9)
        s.check_bounds()
        assert s.unsupported

    def test_tokyo(self):
        """Japan — positive longitude, clearly not Western Hemisphere."""
        s = _make_station(35.7, 139.7)
        s.check_bounds()
        assert s.unsupported

    def test_sydney(self):
        """Australia — southern hemisphere and positive longitude."""
        s = _make_station(-33.9, 151.2)
        s.check_bounds()
        assert s.unsupported

    def test_bogota(self):
        """Colombia — latitude below the 17° minimum."""
        s = _make_station(4.7, -74.1)
        s.check_bounds()
        assert s.unsupported

    def test_central_america(self):
        """Honduras — latitude below 17°, longitude otherwise in range."""
        s = _make_station(14.1, -87.2)
        s.check_bounds()
        assert s.unsupported

    def test_mid_atlantic_east(self):
        """Mid-Atlantic Ocean east of -64 (Azores region)."""
        s = _make_station(39.0, -28.0)
        s.check_bounds()
        assert s.unsupported

    # ------------------------------------------------------------------
    # Inside the bounding box but outside US territory — NOT flagged by
    # check_bounds(); these fall through to the NOAA-404 path.
    # ------------------------------------------------------------------

    def test_toronto_passes_box(self):
        """Toronto is inside the bounding box — caught by NOAA 404 later."""
        s = _make_station(43.7, -79.4)
        s.check_bounds()
        assert not s.unsupported

    def test_vancouver_passes_box(self):
        """Vancouver, BC — lat/lon both inside the box."""
        s = _make_station(49.3, -123.1)
        s.check_bounds()
        assert not s.unsupported

    def test_mexico_city_passes_box(self):
        """Mexico City — lat 19.4 is above 17, lon -99.1 is west of -64."""
        s = _make_station(19.4, -99.1)
        s.check_bounds()
        assert not s.unsupported

    def test_cancun_passes_box(self):
        """Cancún — northeastern Mexico, inside the box."""
        s = _make_station(21.2, -86.8)
        s.check_bounds()
        assert not s.unsupported

    def test_havana_passes_box(self):
        """Havana, Cuba — inside the bounding box."""
        s = _make_station(23.1, -82.4)
        s.check_bounds()
        assert not s.unsupported

    def test_mid_pacific_passes_box(self):
        """Open Pacific between California and Hawaii — inside the box."""
        s = _make_station(30.0, -150.0)
        s.check_bounds()
        assert not s.unsupported

    def test_north_pacific_passes_box(self):
        """North Pacific between Alaska and Hawaii — inside the box."""
        s = _make_station(45.0, -170.0)
        s.check_bounds()
        assert not s.unsupported

    def test_stellwagen_bank_passes_box(self):
        """Stellwagen Bank (NMS, ~30 mi off Cape Cod) — inside the box.

        The NOAA points API likely covers this area; it falls through to the
        API call rather than being immediately rejected.
        """
        s = _make_station(42.4, -70.5)
        s.check_bounds()
        assert not s.unsupported

    def test_offshore_gulf_of_maine_passes_box(self):
        """Far offshore Gulf of Maine / Georges Bank — inside the box but no land.

        Whether NOAA's points API covers this is determined at runtime; the
        bounding box does not pre-reject it.
        """
        s = _make_station(41.5, -67.5)
        s.check_bounds()
        assert not s.unsupported

    # ------------------------------------------------------------------
    # Bounding box edge cases
    # ------------------------------------------------------------------

    def test_exactly_on_lat_min(self):
        """Latitude exactly at US_LAT_MIN (17°) is inside the box."""
        s = _make_station(17.0, -80.0)
        s.check_bounds()
        assert not s.unsupported

    def test_just_below_lat_min(self):
        """Just below US_LAT_MIN is outside."""
        s = _make_station(16.9, -80.0)
        s.check_bounds()
        assert s.unsupported

    def test_exactly_on_lat_max(self):
        """Latitude exactly at US_LAT_MAX (72°) is inside the box."""
        s = _make_station(72.0, -155.0)
        s.check_bounds()
        assert not s.unsupported

    def test_just_above_lat_max(self):
        """Just above US_LAT_MAX is outside."""
        s = _make_station(72.1, -155.0)
        s.check_bounds()
        assert s.unsupported

    def test_exactly_on_lon_max(self):
        """Longitude exactly at US_LON_MAX (-64°) is inside the box."""
        s = _make_station(32.0, -64.0)
        s.check_bounds()
        assert not s.unsupported

    def test_just_east_of_lon_max(self):
        """Just east of US_LON_MAX is outside — e.g. Bermuda's eastern coast."""
        s = _make_station(32.3, -63.9)
        s.check_bounds()
        assert s.unsupported

    # ------------------------------------------------------------------
    # Robustness
    # ------------------------------------------------------------------

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
# TestGetStationOutsideNoaaRange
#
# Locations inside the bounding box that are not covered by the NOAA points
# API (Canada, Mexico, open ocean) don't fail check_bounds().  Instead, the
# code comment says they "fall through to the retry-based check in
# get_station()" — the NOAA API returns 404, network.get() returns None,
# and after MAX_RETRIES failed attempts get_station() gives up silently.
# ---------------------------------------------------------------------------

def _make_station_with_api(lat, lon):
    """Station with a realistic GRIDPOINT_API URL so get_station() can build requests."""
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
    """get_station() handles NOAA 404 (network.get → None) without crashing.

    These locations are inside the bounding box — check_bounds() passes —
    but the NOAA points API does not cover them.
    """

    def _assert_gives_up_gracefully(self, monkeypatch, lat, lon):
        """Helper: verify get_station() exhausts retries and leaves ids unset."""
        calls = []

        def fake_get(url, **kw):
            calls.append(url)
            return None  # simulates NOAA HTTP 404

        monkeypatch.setattr(network, 'get', fake_get)
        s = _make_station_with_api(lat, lon)
        s.get_station()  # must not raise

        assert s.station_id is None, "station_id should remain None after NOAA 404s"
        assert s.hourly_url is None
        assert s.griddata_url is None
        return calls

    def test_vancouver_no_crash(self, monkeypatch):
        """Vancouver, BC — inside box, outside NOAA coverage."""
        calls = self._assert_gives_up_gracefully(monkeypatch, 49.3, -123.1)
        assert len(calls) == MAX_RETRIES

    def test_mexico_city_no_crash(self, monkeypatch):
        """Mexico City — inside box, outside NOAA coverage."""
        calls = self._assert_gives_up_gracefully(monkeypatch, 19.4, -99.1)
        assert len(calls) == MAX_RETRIES

    def test_mid_pacific_no_crash(self, monkeypatch):
        """Open Pacific — inside box, no NOAA gridded coverage."""
        calls = self._assert_gives_up_gracefully(monkeypatch, 30.0, -150.0)
        assert len(calls) == MAX_RETRIES

    def test_far_offshore_atlantic_no_crash(self, monkeypatch):
        """Far offshore Georges Bank — inside box, NOAA coverage uncertain."""
        calls = self._assert_gives_up_gracefully(monkeypatch, 41.5, -67.5)
        assert len(calls) == MAX_RETRIES

    def test_retry_count_is_max_retries(self, monkeypatch):
        """Exactly MAX_RETRIES attempts are made before giving up."""
        calls = self._assert_gives_up_gracefully(monkeypatch, 49.3, -123.1)
        assert len(calls) == MAX_RETRIES, (
            f"Expected {MAX_RETRIES} retries, got {len(calls)}"
        )

    def test_correct_url_is_requested(self, monkeypatch):
        """The points URL includes the lat,lon from the station."""
        calls = self._assert_gives_up_gracefully(monkeypatch, 49.3, -123.1)
        assert all("49.3" in url and "-123.1" in url for url in calls)

    def test_havana_no_crash(self, monkeypatch):
        """Havana, Cuba — inside box, outside NOAA coverage."""
        self._assert_gives_up_gracefully(monkeypatch, 23.1, -82.4)


# ---------------------------------------------------------------------------
# TestGeolocate
# ---------------------------------------------------------------------------

def _make_fresh_station():
    config = {
        'GRIDPOINT_API': '',
        'HISTORICAL_API': '',
    }
    return Station(config)


class TestGeolocate:
    def test_configured_lat_lon_sets_location(self):
        config = {
            'GRIDPOINT_API': '',
            'HISTORICAL_API': '',
            'LATITUDE': '42.39',
            'LONGITUDE': '-71.13',
        }
        s = Station(config)
        s.geolocate()
        assert s.location == "42.39,-71.13"
        assert s.lat == "42.39"
        assert s.lon == "-71.13"

    def test_configured_lat_lon_makes_no_network_call(self, monkeypatch):
        config = {
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

    def test_missing_lat_leaves_location_none(self):
        """geolocate() with no lat/lon configured leaves location as None."""
        s = _make_fresh_station()
        s.geolocate()
        assert s.location is None

    def test_missing_lon_leaves_location_none(self):
        """geolocate() with only latitude configured leaves location as None."""
        config = {
            'GRIDPOINT_API': '',
            'HISTORICAL_API': '',
            'LATITUDE': '42.39',
        }
        s = Station(config)
        s.geolocate()
        assert s.location is None
