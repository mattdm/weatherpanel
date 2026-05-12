"""Display rendering for 64x32 RGB LED matrix.

Manages three visual layers, from bottom to top:
1. Hourly weather graph (temperature line + precipitation bars)
2. Clock and current temperature overlay
3. Status overlay — boot progress or temperature scale preview

The status overlay is topmost and visible by default. The scheduler writes
directly to the public label attributes (location_label, station_label,
network_label) and calls show_status() / show_scale() / show_weather() to
control which screen is active.
"""
import displayio

from line import line_generator

from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label

import matrix


QUERY_COLOR = 0x4278ff
SUCCESS_COLOR = 0x42ff78
FAILURE_COLOR = 0xff6a00


def _temp_color_index(palette_len, temperature, historical=None):
    """Map temperature to color palette index based on historical deviation.

    Temperatures in the historical average range map to center (neutral gray).
    Colder temps spread toward blue indices, warmer toward orange, proportional
    to how far they deviate from the average toward the historical extremes.
    This makes unusually cold/warm temps visually obvious.
    """
    center = palette_len // 2
    buckets = center - 1

    if not historical:
        return center
    if temperature < historical['ave-low']:
        spread = historical['low'] - historical['ave-low']
        if spread == 0:
            return 1
        idx = center - min(buckets, int((temperature - historical['ave-low']) / (spread / buckets)))
        return max(1, min(palette_len - 1, idx))
    if temperature > historical['ave-high']:
        spread = historical['high'] - historical['ave-high']
        if spread == 0:
            return palette_len - 1
        idx = center + min(buckets, int((temperature - historical['ave-high']) / (spread / buckets)))
        return max(1, min(palette_len - 1, idx))
    return center


class Display:
    """Manages rendering weather data to a 64x32 RGB LED matrix.

    Public label attributes for the status overlay — the scheduler writes
    .text and .color directly:
      location_label  (y=12) — city name
      station_label   (y=20) — station ID
      network_label   (y=28) — network SSID during boot; min temp during scale preview

    Class-level color constants for status labels:
      QUERY_COLOR, SUCCESS_COLOR, FAILURE_COLOR
    """

    QUERY_COLOR = QUERY_COLOR
    SUCCESS_COLOR = SUCCESS_COLOR
    FAILURE_COLOR = FAILURE_COLOR

    def __init__(self, config):
        """Initialize display with layered groups: forecast graph, clock/temp, status overlay."""
        self.temp_min = int(config.get('TEMP_MIN', -5))
        self.temp_max = int(config.get('TEMP_MAX', 105))

        self._font = bitmap_font.load_font("/fonts/dogica-pixel-8.pcf")
        self.temperature_palette, self.precipitation_palette = self._build_palettes()

        self.root_group = displayio.Group()
        self._display = matrix.display_set_root(self.root_group, swapgb=config['SWAP_GREEN_BLUE'])

        # Layer order: forecast (bottom) → clock → status overlay (top)
        self._forecast_group = self._build_forecast_group()
        self._clock_group = self._build_clock_group()
        self._status_group = self._build_status_group()
        self.root_group.append(self._forecast_group)
        self.root_group.append(self._clock_group)
        self.root_group.append(self._status_group)

    # ------------------------------------------------------------------
    # Private builders — each creates one group and returns it
    # ------------------------------------------------------------------

    def _build_palettes(self):
        """Create and return the temperature and precipitation color palettes."""
        # Diverging palette: cold blue → neutral gray → warm orange.
        # Index 0 is transparent; index 6 (center) is neutral for average temps.
        temperature_colors = [
            0xFFFFFF,
            0x174afd,
            0x4278ff,
            0x6f9dff,
            0x9ebfff,
            0xcedfff,
            0xeeeeee,
            0xffe2cf,
            0xffc6a0,
            0xffa872,
            0xff8a43,
            0xff6a00,
        ]
        temp_palette = displayio.Palette(len(temperature_colors))
        temp_palette.make_transparent(0)
        for i, color in enumerate(temperature_colors):
            temp_palette[i] = color

        precipitation_colors = [
            0xff0000,
            0x0000D0,  # rain
            0x44bbdd,  # snow
        ]
        precip_palette = displayio.Palette(len(precipitation_colors))
        precip_palette.make_transparent(0)
        for i, color in enumerate(precipitation_colors):
            precip_palette[i] = color

        return temp_palette, precip_palette

    def _build_forecast_group(self):
        """Create the hourly forecast group with precipitation and temperature bitmaps."""
        group = displayio.Group(x=0, y=0)
        self.precipitation_forecast_bitmap = displayio.Bitmap(64, 32, len(self.precipitation_palette))
        self.precipitation_forecast_grid = displayio.TileGrid(
            bitmap=self.precipitation_forecast_bitmap,
            pixel_shader=self.precipitation_palette,
            tile_width=self.precipitation_forecast_bitmap.width,
            tile_height=self.precipitation_forecast_bitmap.height,
        )
        self.temperature_forecast_bitmap = displayio.Bitmap(64, 32, len(self.temperature_palette))
        self.temperature_forecast_grid = displayio.TileGrid(
            bitmap=self.temperature_forecast_bitmap,
            pixel_shader=self.temperature_palette,
            tile_width=self.temperature_forecast_bitmap.width,
            tile_height=self.temperature_forecast_bitmap.height,
        )
        group.append(self.precipitation_forecast_grid)
        group.append(self.temperature_forecast_grid)
        return group

    def _build_clock_group(self):
        """Create the clock and current-temperature label group."""
        group = displayio.Group(x=0, y=0)
        self.clock_label = Label(
            self._font, text="", color=0xFFFFFF,
            anchor_point=(1, 0), anchored_position=(65, 0),
        )
        self.current_temp_label = Label(self._font, text="", color=0x808080, x=-1, y=4)
        group.append(self.clock_label)
        group.append(self.current_temp_label)
        return group

    def _build_status_group(self):
        """Create the four-slot status overlay used for boot progress and scale preview.

        Slot layout:
          y= 4  _top_label     — empty during boot; max temp during scale preview
          y=12  location_label — city during both modes
          y=20  station_label  — station ID during both modes
          y=28  network_label  — network SSID during boot; min temp during scale preview
        """
        group = displayio.Group(x=0, y=0)
        self._top_label     = Label(self._font, text="", color=self.temperature_palette[11], x=0, y=4)
        self.location_label = Label(self._font, text="", color=QUERY_COLOR, x=0, y=12)
        self.station_label  = Label(self._font, text="", color=QUERY_COLOR, x=0, y=20)
        self.network_label  = Label(self._font, text="", color=QUERY_COLOR, x=0, y=28)
        group.append(self._top_label)
        group.append(self.location_label)
        group.append(self.station_label)
        group.append(self.network_label)
        return group

    # ------------------------------------------------------------------
    # Screen-switch methods
    # ------------------------------------------------------------------

    def show_status(self):
        """Show the status overlay."""
        self._status_group.hidden = False
        self._display.refresh()

    def show_weather(self):
        """Switch to weather mode: hide the status overlay."""
        self._status_group.hidden = True

    def show_scale(self, city, station_id):
        """Display the temperature scale preview screen.

        Shows the all-time high (orange, top) and low (blue, bottom) with city
        name and station ID in between.  Stays visible until show_weather() is
        called when the first forecast renders.
        """
        self._top_label.text      = f"{self.temp_max}\u00b0"
        self.location_label.text  = city or ""
        self.location_label.color = 0xFFFFFF
        self.station_label.text   = station_id or ""
        self.station_label.color  = 0xFFFFFF
        self.network_label.text   = f"{self.temp_min}\u00b0"
        self.network_label.color  = self.temperature_palette[1]
        self._status_group.hidden = False
        self._display.refresh()

    # ------------------------------------------------------------------
    # Scale and data methods
    # ------------------------------------------------------------------

    def set_temp_scale(self, temp_min, temp_max):
        """Update the temperature scale used for the hourly forecast graph.

        Called after a successful scale query so that update_forecast() uses
        the queried range rather than the config defaults."""
        self.temp_min = temp_min
        self.temp_max = temp_max

    def flush(self):
        """Push the current display state to screen without updating any labels."""
        self._display.refresh()

    def update_clock(self, clock):
        """Update clock display with current time and sync status color."""
        t = clock.pretty_time
        print(f"Clock: {t!r} (group y={self._clock_group.y})")
        self.clock_label.text = t
        self.clock_label.color = clock.color
        self._display.refresh()

    def update_forecast(self, hourly_data, historical_data, current_time):
        """Render hourly forecast as temperature line and precipitation bars.

        Each column represents one hour:
        - Temperature: dot with color based on historical deviation, connected with lines
        - Precipitation: vertical bar from bottom, split between rain (blue) and snow (cyan)

        historical_data is a 3-slot list (today, tomorrow, day-after); each slot is
        either a dict with 'date'/'low'/'ave-low'/'ave-high'/'high' or None. The
        correct slot is selected per hour by matching the hour's local calendar date
        against the slot dates. Unmatched or None slots fall back to neutral gray.

        Returns number of hours successfully plotted.
        """
        height = self.temperature_forecast_bitmap.height
        width = self.temperature_forecast_bitmap.width

        scale_range = self.temp_max - self.temp_min
        if scale_range <= 0:
            # Defensive guard: min ≥ max is a misconfiguration (e.g. a stale
            # sentinel value that slipped through). Fall back to defaults so
            # the display renders rather than crashing with ZeroDivisionError.
            print(f"Warning: temp scale degenerate (min={self.temp_min}, max={self.temp_max})"
                  " — falling back to defaults")
            from appconfig import DEFAULTS
            scale_range = DEFAULTS['TEMP_MAX'] - DEFAULTS['TEMP_MIN']
            self.temp_min = DEFAULTS['TEMP_MIN']
            self.temp_max = DEFAULTS['TEMP_MAX']
        scale_factor = scale_range / height
        midpoint_temp = (self.temp_max + self.temp_min) / 2

        x = 0
        peakpoint = height
        valleypoint = 0
        previous_point = None

        print("Plotting hours", end="")
        for hour in hourly_data:

            if hour.end < current_time:
                print(f"\nHour {x:2} expired at {hour.end}")
                continue

            hourly_temp_point = max(0, min(height - 1, round(height // 2 + (midpoint_temp - hour.temperature) / scale_factor)))

            # Track temperature extremes in the text overlay areas to reposition
            # labels so they don't obscure the temperature line.
            if x < self.current_temp_label.width or x > width - max(17, self.clock_label.width):
                if hourly_temp_point < peakpoint:
                    peakpoint = hourly_temp_point
                if hourly_temp_point > valleypoint:
                    valleypoint = hourly_temp_point

            for y in range(0, height):
                self.temperature_forecast_bitmap[x, y] = 0

            hour_date = hour.start[:10]
            hour_slot = None
            for slot in historical_data:
                if slot is not None and slot['date'] == hour_date:
                    hour_slot = slot
                    break
            color = self._temp_color_index(hour.temperature, hour_slot)

            if x > 0 and previous_point is not None and abs(previous_point - hourly_temp_point) > 1:
                # Draw line back to previous point to avoid ugly gaps.
                for (line_x, line_y) in line_generator((x, hourly_temp_point), (x - 1, previous_point)):
                    self.temperature_forecast_bitmap[line_x, line_y] = color
            else:
                self.temperature_forecast_bitmap[x, hourly_temp_point] = color

            if x == 0:
                self.current_temp_label.text = f"{hour.temperature}°"
                self.current_temp_label.color = self.temperature_palette[color]

            if hour.precipitation:
                hourly_precipitation_point = height - int(((hour.precipitation / 100) * height) + 0.5)
            else:
                hourly_precipitation_point = height

            precip_start_row = hourly_precipitation_point
            total_precip_rows = height - precip_start_row
            snow_fraction = hour.snow_fraction or 0.0
            rain_row_count = round((1.0 - snow_fraction) * total_precip_rows)
            snow_start_row = precip_start_row + rain_row_count

            for y in range(0, height):
                if y < precip_start_row:
                    self.precipitation_forecast_bitmap[x, y] = 0  # transparent
                elif y < snow_start_row:
                    self.precipitation_forecast_bitmap[x, y] = 1  # rain (dark blue)
                else:
                    self.precipitation_forecast_bitmap[x, y] = 2  # snow (cyan)

            x += 1
            previous_point = hourly_temp_point
            if x >= self.temperature_forecast_bitmap.width:
                break

        # Reposition clock/temp overlay to avoid obscuring temperature extremes.
        if peakpoint < 8:
            if valleypoint < 24:
                self._clock_group.y = valleypoint + 3
            else:
                self._clock_group.y = 14  # center 8px font group in 32px display
        else:
            self._clock_group.y = 0

        for col in range(x, width):
            for y in range(0, height):
                self.temperature_forecast_bitmap[col, y] = 0
                self.precipitation_forecast_bitmap[col, y] = 0

        print()

        if x < width // 2:
            print(f"Warning: Only {x} hours plotted, forecast may be stale")

        self._display.refresh()
        return x

    def _temp_color_index(self, temperature, historical=None):
        return _temp_color_index(len(self.temperature_palette), temperature, historical)

    def _temp_color(self, temperature, historical=None):
        return self.temperature_palette[self._temp_color_index(temperature, historical)]
