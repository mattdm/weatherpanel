"""Line drawing utilities for connecting temperature dots on the display."""


def column_fill_range(y, prev_y, next_y):
    """Return (fill_min, fill_max) row range for a temperature column.

    Uses a "warmer claims all" rule: when this column is warmer than a
    neighbour (smaller y = higher on screen), it owns every row from its own
    dot up to that neighbour's row minus one. When it is colder or equal, it
    contributes only its own dot in that direction.

    This is gap-free — the boundary between adjacent columns is always
    diagonally adjacent — and eliminates L-shaped bumps for both symmetric
    and asymmetric transitions. For delta ≤ 2 the result is identical to the
    old midpoint rule.
    """
    if y < prev_y:
        left_min, left_max = y, prev_y - 1
    else:
        left_min = left_max = y

    if y < next_y:
        right_min, right_max = y, next_y - 1
    else:
        right_min = right_max = y

    return min(left_min, right_min), max(left_max, right_max)


