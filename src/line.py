"""Bresenham line drawing for connecting temperature dots on the display."""

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

        