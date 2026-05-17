"""Tests for line drawing utilities: Bresenham line_generator and column_fill_range."""
from line import line_generator, column_fill_range


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


# ---------------------------------------------------------------------------
# column_fill_range property tests
# ---------------------------------------------------------------------------

def test_cfr_no_l_shapes_symmetric_peak():
    """Adjacent columns must not share a row for a symmetric peak.

    Sequence: col0=5, col1=7, col2=5. No two adjacent columns should
    include the same row.
    """
    ys = [5, 7, 5]
    ranges = [
        column_fill_range(ys[0], ys[0], ys[1]),  # col 0: no left neighbour
        column_fill_range(ys[1], ys[0], ys[2]),  # col 1
        column_fill_range(ys[2], ys[1], ys[2]),  # col 2: no right neighbour
    ]
    for col_a, col_b in [(0, 1), (1, 2)]:
        rows_a = set(range(ranges[col_a][0], ranges[col_a][1] + 1))
        rows_b = set(range(ranges[col_b][0], ranges[col_b][1] + 1))
        assert rows_a.isdisjoint(rows_b), (
            f"L-shape: col {col_a} rows {rows_a} and col {col_b} rows {rows_b} overlap"
        )


def test_cfr_no_l_shapes_symmetric_valley():
    """Adjacent columns must not share a row for a symmetric valley."""
    ys = [7, 5, 7]
    ranges = [
        column_fill_range(ys[0], ys[0], ys[1]),
        column_fill_range(ys[1], ys[0], ys[2]),
        column_fill_range(ys[2], ys[1], ys[2]),
    ]
    for col_a, col_b in [(0, 1), (1, 2)]:
        rows_a = set(range(ranges[col_a][0], ranges[col_a][1] + 1))
        rows_b = set(range(ranges[col_b][0], ranges[col_b][1] + 1))
        assert rows_a.isdisjoint(rows_b), (
            f"L-shape: col {col_a} rows {rows_a} and col {col_b} rows {rows_b} overlap"
        )


def test_cfr_gap_free_large_delta():
    """Boundary rows of adjacent columns are diagonally adjacent — no gaps.

    For a steep drop (delta=6), the last row of the lower column and the
    first row of the upper column must differ by exactly 1.
    """
    # col 0: y=2, prev boundary=2, next_y=8
    # col 1: y=8, prev_y=2, next boundary=8
    r0 = column_fill_range(2, 2, 8)
    r1 = column_fill_range(8, 2, 8)
    # col 0 covers [2, mid], col 1 covers [mid+1, 8] — boundary must be adjacent
    assert r0[1] + 1 == r1[0], (
        f"Gap between col 0 max row {r0[1]} and col 1 min row {r1[0]}"
    )


def test_cfr_always_contains_own_row():
    """A column's fill range always contains its own y value."""
    cases = [
        (5, 5, 5),
        (5, 3, 7),
        (7, 5, 9),
        (10, 10, 3),
        (0, 15, 0),
    ]
    for y, prev_y, next_y in cases:
        lo, hi = column_fill_range(y, prev_y, next_y)
        assert lo <= y <= hi, f"Own row {y} not in [{lo}, {hi}] for prev={prev_y} next={next_y}"
