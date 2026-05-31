"""Tests for state_snapshot: embed/read roundtrip, partial state, LED color,
and the bin/png-state reader script.
"""
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from state_snapshot import make_png_info, read_state, snapshot_state

_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Minimal fake objects for snapshot_state
# ---------------------------------------------------------------------------

def _make_hour(temperature=55, precipitation=20, snow_fraction=0.1,
               start="2026-05-09T10:00:00-04:00",
               end="2026-05-09T11:00:00-04:00",
               forecast="Mostly Cloudy"):
    from station import Hour
    h = Hour()
    h.start = start
    h.end = end
    h.temperature = temperature
    h.precipitation = precipitation
    h.snow_fraction = snow_fraction
    h.forecast = forecast
    return h


def _make_station():
    """Return a minimal Station-like mock with known field values."""
    s = MagicMock()
    s.lat = "42.388"
    s.lon = "-71.099"
    s.city = "Somerville"
    s.state = "MA"
    s.tz = "America/New_York"
    s.station_id = "KBOS"
    s.hourly_model_updated = "2026-05-09T14:00:00+00:00"
    s.historical = [
        {'date': '2026-05-09', 'low': 28.0, 'ave-low': 42.0,
         'ave-high': 58.0, 'high': 74.0},
        {'date': '2026-05-10', 'low': 30.0, 'ave-low': 43.0,
         'ave-high': 59.0, 'high': 75.0},
        None,
        None,
    ]
    s.hourly = [_make_hour()]
    return s


def _make_display():
    """Return a minimal Display-like mock."""
    d = MagicMock()
    d.temp_min = -5
    d.temp_max = 105
    d._clock_group = MagicMock()
    d._clock_group.y = 4
    d._status_group = MagicMock()
    d._status_group.hidden = True
    return d


def _make_clock():
    """Return a minimal Clock-like mock."""
    c = MagicMock()
    c.tz = "America/New_York"
    c.isotime = "2026-05-09T10:32:00-04:00"
    c.today = "2026-05-09"
    c.pretty_time = "10:32"
    c.color = 0xFFFFFF
    c.twentyfour = False
    return c


def _save_png(state_dict):
    """Save a PNG with embedded state; return the path as a tmp-file bytes object."""
    buf = io.BytesIO()
    img = Image.new("RGB", (8, 8))
    img.save(buf, format="PNG", pnginfo=make_png_info(state_dict))
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# snapshot_state — field extraction
# ---------------------------------------------------------------------------

class TestSnapshotState:
    def test_historical_populated_slots_only(self):
        station = _make_station()
        state = snapshot_state(station=station)
        hist = state['historical']
        # Only the two non-None slots (indices 0 and 1) appear
        assert len(hist) == 2
        assert hist[0]['slot'] == 0
        assert hist[0]['date'] == '2026-05-09'
        assert hist[0]['ave_low'] == pytest.approx(42.0)
        assert hist[1]['slot'] == 1
        assert hist[1]['date'] == '2026-05-10'

    def test_historical_hyphen_keys_renamed(self):
        """TOML-unfriendly 'ave-low' / 'ave-high' are stored as 'ave_low' / 'ave_high'."""
        station = _make_station()
        state = snapshot_state(station=station)
        for slot in state['historical']:
            assert 'ave_low' in slot
            assert 'ave_high' in slot
            assert 'ave-low' not in slot
            assert 'ave-high' not in slot

    def test_hourly_fields_extracted(self):
        station = _make_station()
        state = snapshot_state(station=station)
        h = state['hourly'][0]
        assert h['temperature'] == 55
        assert h['precipitation'] == 20
        assert h['snow_fraction'] == pytest.approx(0.1)
        assert h['forecast'] == "Mostly Cloudy"

    def test_all_none_returns_empty(self):
        assert snapshot_state() == {}

    def test_missing_station_no_station_section(self):
        state = snapshot_state(display=_make_display())
        assert 'station' not in state
        assert 'hourly' not in state
        assert 'historical' not in state

    def test_explicit_hourly_override(self):
        """Explicit hourly= kwarg is used instead of station.hourly."""
        station = _make_station()
        extra_hour = _make_hour(temperature=99, forecast="Extreme Heat")
        state = snapshot_state(station=station, hourly=[extra_hour])
        assert state['hourly'][0]['temperature'] == 99
        assert state['hourly'][0]['forecast'] == "Extreme Heat"

    def test_explicit_historical_override(self):
        """Explicit historical= kwarg overrides station.historical."""
        station = _make_station()
        state = snapshot_state(station=station, historical=[None, None, None, None])
        assert 'historical' not in state

    def test_explicit_historical_no_station(self):
        """historical= without station still populates the section."""
        hist = [{'date': '2026-05-09', 'low': 20.0, 'ave-low': 35.0,
                 'ave-high': 55.0, 'high': 75.0}]
        state = snapshot_state(historical=hist)
        assert state['historical'][0]['slot'] == 0
        assert state['historical'][0]['date'] == '2026-05-09'


# ---------------------------------------------------------------------------
# LED color via statusled_sim
# ---------------------------------------------------------------------------

class TestLEDSnapshot:
    def test_led_color_and_sticky(self):
        import statusled_sim
        led = statusled_sim.StatusLED()
        led.working(statusled_sim.BLUE)
        state = snapshot_state(led=led)
        assert state['led']['color'] == [0, 0, 255]
        assert state['led']['sticky'] is False

    def test_led_failure_is_sticky(self):
        import statusled_sim
        led = statusled_sim.StatusLED()
        led.failure()
        state = snapshot_state(led=led)
        assert state['led']['sticky'] is True
        assert state['led']['color'] == [255, 128, 0]

    def test_led_off(self):
        import statusled_sim
        led = statusled_sim.StatusLED()
        led.clear()
        state = snapshot_state(led=led)
        assert state['led']['color'] == [0, 0, 0]
        assert state['led']['sticky'] is False


# ---------------------------------------------------------------------------
# PNG roundtrip
# ---------------------------------------------------------------------------

class TestPNGRoundtrip:
    def test_roundtrip_station(self, tmp_path):
        station = _make_station()
        state = snapshot_state(station=station)
        img = Image.new("RGB", (8, 8))
        path = tmp_path / "test.png"
        img.save(str(path), pnginfo=make_png_info(state))

        read = read_state(path)
        assert read['station']['city'] == "Somerville"
        assert read['station']['station_id'] == "KBOS"

    def test_roundtrip_historical(self, tmp_path):
        station = _make_station()
        state = snapshot_state(station=station)
        path = tmp_path / "test.png"
        Image.new("RGB", (8, 8)).save(str(path), pnginfo=make_png_info(state))

        read = read_state(path)
        assert len(read['historical']) == 2
        assert read['historical'][0]['ave_low'] == pytest.approx(42.0)

    def test_roundtrip_hourly(self, tmp_path):
        hours = [_make_hour(temperature=72, precipitation=0, snow_fraction=0.0)]
        state = snapshot_state(hourly=hours)
        path = tmp_path / "test.png"
        Image.new("RGB", (8, 8)).save(str(path), pnginfo=make_png_info(state))

        read = read_state(path)
        assert read['hourly'][0]['temperature'] == 72

    def test_roundtrip_full(self, tmp_path):
        state = snapshot_state(
            station=_make_station(),
            clock=_make_clock(),
            display=_make_display(),
        )
        path = tmp_path / "full.png"
        Image.new("RGB", (8, 8)).save(str(path), pnginfo=make_png_info(state))

        read = read_state(path)
        assert 'station' in read
        assert 'clock' in read
        assert 'display' in read
        assert read['clock']['pretty_time'] == "10:32"
        assert read['display']['timetemp_y'] == 4

    def test_no_metadata_returns_empty(self, tmp_path):
        path = tmp_path / "plain.png"
        Image.new("RGB", (8, 8)).save(str(path))
        assert read_state(path) == {}

    def test_partial_state_missing_sections_absent(self, tmp_path):
        state = snapshot_state(display=_make_display())
        path = tmp_path / "partial.png"
        Image.new("RGB", (8, 8)).save(str(path), pnginfo=make_png_info(state))

        read = read_state(path)
        assert 'station' not in read
        assert 'clock' not in read
        assert 'led' not in read
        assert 'display' in read


# ---------------------------------------------------------------------------
# bin/png-state CLI
# ---------------------------------------------------------------------------

class TestPNGStateCLI:
    def _run(self, *args):
        cmd = [sys.executable, str(_ROOT / "bin" / "png-state"), *args]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_toml_output(self, tmp_path):
        state = snapshot_state(station=_make_station(), display=_make_display())
        path = tmp_path / "cli_test.png"
        Image.new("RGB", (8, 8)).save(str(path), pnginfo=make_png_info(state))

        result = self._run(str(path))
        assert result.returncode == 0
        assert "Somerville" in result.stdout
        assert "temp_min" in result.stdout

    def test_json_flag(self, tmp_path):
        state = snapshot_state(station=_make_station())
        path = tmp_path / "cli_json.png"
        Image.new("RGB", (8, 8)).save(str(path), pnginfo=make_png_info(state))

        result = self._run(str(path), "--json")
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert parsed['station']['city'] == "Somerville"

    def test_no_metadata_exits_nonzero(self, tmp_path):
        path = tmp_path / "plain.png"
        Image.new("RGB", (8, 8)).save(str(path))

        result = self._run(str(path))
        assert result.returncode == 1
        assert "No weatherpanel state" in result.stderr

    def test_hourly_summary_in_toml_output(self, tmp_path):
        """Hourly data is rendered as a compact human-readable block, not raw TOML."""
        hours = [_make_hour(temperature=52, precipitation=30, forecast="Partly Cloudy")]
        state = snapshot_state(hourly=hours)
        path = tmp_path / "hourly.png"
        Image.new("RGB", (8, 8)).save(str(path), pnginfo=make_png_info(state))

        result = self._run(str(path))
        assert result.returncode == 0
        # The compact format includes the temperature and forecast string
        assert "52" in result.stdout
        assert "Partly Cloudy" in result.stdout
