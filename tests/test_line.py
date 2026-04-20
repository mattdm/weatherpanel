"""Tests for Bresenham line drawing algorithm."""
from line import line_generator


def test_horizontal_right():
    points = list(line_generator((0, 0), (5, 0)))
    assert len(points) == 5
    assert points[0] == (0, 0)
    assert points[-1] == (4, 0)
    assert all(y == 0 for _, y in points)


def test_horizontal_left():
    points = list(line_generator((5, 0), (0, 0)))
    assert len(points) == 5
    assert points[0] == (5, 0)
    assert points[-1] == (1, 0)


def test_vertical_down():
    points = list(line_generator((0, 0), (0, 5)))
    assert len(points) == 5
    assert all(x == 0 for x, _ in points)


def test_vertical_up():
    points = list(line_generator((0, 5), (0, 0)))
    assert len(points) == 5
    assert all(x == 0 for x, _ in points)


def test_diagonal():
    points = list(line_generator((0, 0), (3, 3)))
    assert len(points) == 3
    for i, (x, y) in enumerate(points):
        assert x == i
        assert y == i


def test_same_point_yields_nothing():
    points = list(line_generator((3, 3), (3, 3)))
    assert points == []


def test_adjacent_points():
    points = list(line_generator((0, 0), (1, 0)))
    assert points == [(0, 0)]


def test_steep_line():
    """A line steeper than 45 degrees should still connect."""
    points = list(line_generator((0, 0), (1, 5)))
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    assert min(ys) == 0
    assert max(ys) == 4  # endpoint excluded
    assert all(0 <= x <= 1 for x in xs)


def test_no_gaps():
    """Adjacent pixels in the line should differ by at most 1 in each axis."""
    points = list(line_generator((0, 0), (10, 7)))
    for i in range(1, len(points)):
        dx = abs(points[i][0] - points[i-1][0])
        dy = abs(points[i][1] - points[i-1][1])
        assert dx <= 1 and dy <= 1, f"Gap between {points[i-1]} and {points[i]}"
