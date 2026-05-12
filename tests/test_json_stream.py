"""Tests verifying adafruit_json_stream parses NOAA sample forecasts correctly.

adafruit_json_stream has had historical bugs. These tests replay real API
responses through the streaming parser and compare against json.load() as
ground truth, so regressions are caught before deploying to device.

Usage note: adafruit_json_stream.load() takes an iterator of byte-chunks,
not individual bytes. Wrap a bytes object in a single-item list: iter([data]).
"""
import json
from pathlib import Path

import adafruit_json_stream
import pytest

SAMPLE_DIR = Path(__file__).parent / "sample-forecasts"


def _load_bytes(name):
    return (SAMPLE_DIR / name).read_bytes()


def _stream(data: bytes):
    """Wrap bytes as a single-chunk iterator for adafruit_json_stream.load()."""
    return adafruit_json_stream.load(iter([data]))


ALL_HOURLY = sorted(SAMPLE_DIR.glob("*_hourly.json"))
ALL_GRIDDATA = sorted(SAMPLE_DIR.glob("*_griddata.json"))


# ---------------------------------------------------------------------------
# Hourly forecast parsing
# ---------------------------------------------------------------------------

class TestHourlyStream:
    """adafruit_json_stream can navigate the hourly forecast JSON structure."""

    def test_extracts_65_periods(self):
        data = _load_bytes("boston_hourly.json")
        ref = json.loads(data)

        obj = _stream(data)
        periods = obj['properties']['periods']
        parsed = [p.as_object() for p in periods]

        assert len(parsed) == len(ref['properties']['periods'])

    def test_first_period_temperature_matches_reference(self):
        data = _load_bytes("boston_hourly.json")
        ref = json.loads(data)

        obj = _stream(data)
        periods = obj['properties']['periods']
        first = next(iter(periods)).as_object()

        assert first['temperature'] == ref['properties']['periods'][0]['temperature']

    def test_first_period_start_time_matches_reference(self):
        data = _load_bytes("boston_hourly.json")
        ref = json.loads(data)

        obj = _stream(data)
        periods = obj['properties']['periods']
        first = next(iter(periods)).as_object()

        assert first['startTime'] == ref['properties']['periods'][0]['startTime']

    def test_precipitation_value_matches_reference(self):
        data = _load_bytes("boston_hourly.json")
        ref = json.loads(data)

        obj = _stream(data)
        periods = obj['properties']['periods']
        first = next(iter(periods)).as_object()

        ref_precip = ref['properties']['periods'][0]['probabilityOfPrecipitation']['value']
        assert first['probabilityOfPrecipitation']['value'] == ref_precip

    def test_update_time_readable(self):
        data = _load_bytes("boston_hourly.json")
        ref = json.loads(data)

        obj = _stream(data)
        props = obj['properties']
        update_time = props['updateTime']
        # Must advance past updateTime before reading periods (transient)
        assert isinstance(update_time, str)
        assert update_time == ref['properties']['updateTime']


@pytest.mark.parametrize("hourly_file", [p.name for p in ALL_HOURLY])
class TestAllHourlyParse:
    """Every sample hourly file should stream-parse without error."""

    def test_all_periods_parseable(self, hourly_file):
        data = _load_bytes(hourly_file)
        ref = json.loads(data)
        ref_periods = ref['properties']['periods']

        obj = _stream(data)
        periods = obj['properties']['periods']
        count = 0
        for period in periods:
            p = period.as_object()
            assert 'temperature' in p
            assert 'startTime' in p
            count += 1

        assert count == len(ref_periods)


# ---------------------------------------------------------------------------
# Griddata parsing
# ---------------------------------------------------------------------------

class TestGriddataStream:
    """adafruit_json_stream can navigate the griddata JSON structure."""

    def test_qpf_uom_matches_reference(self):
        data = _load_bytes("boston_griddata.json")
        ref = json.loads(data)

        obj = _stream(data)
        props = obj['properties']
        qpf = props['quantitativePrecipitation']
        qpf_obj = qpf.as_object()

        assert qpf_obj.get('uom') == ref['properties']['quantitativePrecipitation'].get('uom')

    def test_qpf_value_count_matches_reference(self):
        data = _load_bytes("boston_griddata.json")
        ref = json.loads(data)

        obj = _stream(data)
        props = obj['properties']
        qpf = props['quantitativePrecipitation']
        qpf_obj = qpf.as_object()

        ref_count = len(ref['properties']['quantitativePrecipitation']['values'])
        assert len(qpf_obj['values']) == ref_count

    def test_snowfall_values_accessible(self):
        data = _load_bytes("boston_griddata.json")
        ref = json.loads(data)

        obj = _stream(data)
        props = obj['properties']
        props['quantitativePrecipitation'].finish()
        snow = props['snowfallAmount']
        snow_obj = snow.as_object()

        ref_snow_count = len(ref['properties']['snowfallAmount']['values'])
        assert len(snow_obj['values']) == ref_snow_count

    def test_update_time_accessible(self):
        data = _load_bytes("boston_griddata.json")
        ref = json.loads(data)

        obj = _stream(data)
        props = obj['properties']
        update_time = props['updateTime']

        assert update_time == ref['properties']['updateTime']


class TestGriddataMissingUom:
    """Streaming parse must not crash when snowfallAmount lacks 'uom'.

    This is the same scenario covered in test_station_forecast.py via
    monkeypatched network.get(), but here we verify the json_stream
    library itself handles the missing-key case without error."""

    def test_empty_snowfall_no_uom_parseable(self):
        payload = json.dumps({
            "properties": {
                "updateTime": "2026-05-06T18:00:00+00:00",
                "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": []},
                "snowfallAmount": {"values": []},
            }
        }).encode()

        obj = _stream(payload)
        props = obj['properties']
        props['updateTime']
        qpf = props['quantitativePrecipitation'].as_object()
        snow = props['snowfallAmount'].as_object()

        assert qpf['values'] == []
        assert snow['values'] == []
        assert 'uom' not in snow
