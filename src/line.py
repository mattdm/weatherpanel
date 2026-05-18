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


def line_generator(start_point, end_point):
    """
    Iterates using Bresenham's algorithm to return a line
    from start_point to end_point, end point not included
    """
    start_x, start_y = start_point
    end_x, end_y = end_point

    delta_x = abs(end_x - start_x)
    delta_y = abs(end_y - start_y)
    step_x = 1 if start_x < end_x else -1
    step_y = 1 if start_y < end_y else -1

    error = delta_x - delta_y

    current_x, current_y = start_x, start_y

    while True:
        if current_x == end_x and current_y == end_y:
            return

        yield (current_x, current_y)

        error2 = 2 * error
        if error2 > -delta_y:
            error -= delta_y
            current_x += step_x
        if error2 < delta_x:
            error += delta_x
            current_y += step_y

