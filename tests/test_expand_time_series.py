"""Tests for _expand_time_series griddata helper."""
from station import _expand_time_series


def test_single_hour():
    values = [{'validTime': '2026-04-20T06:00:00+00:00/PT1H', 'value': 3.0}]
    result = _expand_time_series(values)
    assert result == {'2026-04-20T06': 3.0}


def test_multi_hour_distributes_evenly():
    values = [{'validTime': '2026-04-20T06:00:00+00:00/PT6H', 'value': 12.0}]
    result = _expand_time_series(values)
    assert len(result) == 6
    for h in range(6, 12):
        assert result[f'2026-04-20T{h:02}'] == 2.0


def test_day_rollover():
    values = [{'validTime': '2026-04-20T22:00:00+00:00/PT4H', 'value': 8.0}]
    result = _expand_time_series(values)
    assert result['2026-04-20T22'] == 2.0
    assert result['2026-04-20T23'] == 2.0
    assert result['2026-04-21T00'] == 2.0
    assert result['2026-04-21T01'] == 2.0


def test_null_value_treated_as_zero():
    values = [{'validTime': '2026-04-20T06:00:00+00:00/PT2H', 'value': None}]
    result = _expand_time_series(values)
    assert result['2026-04-20T06'] == 0.0
    assert result['2026-04-20T07'] == 0.0


def test_multiple_entries():
    values = [
        {'validTime': '2026-04-20T06:00:00+00:00/PT3H', 'value': 9.0},
        {'validTime': '2026-04-20T09:00:00+00:00/PT3H', 'value': 6.0},
    ]
    result = _expand_time_series(values)
    assert result['2026-04-20T06'] == 3.0
    assert result['2026-04-20T09'] == 2.0


def test_empty_values():
    assert _expand_time_series([]) == {}
