"""Line drawing utilities for connecting temperature dots on the display."""


def column_fill_range(y, prev_y, next_y):
    """Return (fill_min, fill_max) row range for a temperature column.

    Splits the gap to each neighbour at the midpoint — each column owns the
    half of the gap closer to its own dot. This eliminates L-shaped bumps by
    guaranteeing no two adjacent columns share a row for symmetric transitions,
    and minimises overlap for asymmetric ones. The result is always gap-free:
    the boundary rows of adjacent columns are diagonally adjacent.
    """
    if y < prev_y:
        left_min, left_max = y, (y + prev_y) // 2
    elif y > prev_y:
        left_min, left_max = (prev_y + y) // 2 + 1, y
    else:
        left_min = left_max = y

    if y < next_y:
        right_min, right_max = y, (y + next_y) // 2
    elif y > next_y:
        right_min, right_max = (next_y + y) // 2 + 1, y
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

