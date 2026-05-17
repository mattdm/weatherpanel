"""Display rendering for 64x32 RGB LED matrix.

Manages three visual layers, from bottom to top:
1. Hourly weather graph (temperature line + precipitation bars)
2. Clock and current temperature overlay
3. Boot/scale text screen — 4 pre-allocated labels shared with BaseDisplay

The text screen is topmost and visible by default. The scheduler calls
set_location(text, color) to update the location slot, writes directly to
station_label and network_label, and calls show_status() / show_scale() /
show_weather() to control which screen is active.
"""
import displayio

from line import line_generator

from base_display import BaseDisplay

QUERY_COLOR = 0x4278ff
SUCCESS_COLOR = 0x42ff78
FAILURE_COLOR = 0xff6a00
STALE_COLOR = 0x8000FF   # purple — same visual cue as clock.COLOR_UNCERTAIN

SCREEN_BOOT    = "boot"
SCREEN_SCALE   = "scale"
SCREEN_WEATHER = "weather"

COMFORT_LOW   = 68        # °F — bottom of the comfortable temperature range
COMFORT_HIGH  = 72        # °F — top of the comfortable temperature range
COMFORT_COLOR = 0x0a3c00  # warm-shifted green — natural foliage, near-triadic with the palette blues

# QPF thresholds (mm/hr) for precipitation bar dot pattern.
# Boundaries align with WMO light/moderate precipitation intensity standard.
QPF_HIGH_MM = 2.5   # >= 2.5 mm/hr: bright rain / bright-bright-dim snow — WMO light/moderate boundary
QPF_MID_MM  = 0.5   # >= 0.5 mm/hr: bright-dim — boston, austin, typical rain
                    # <  0.5 mm/hr: bright-dim-dim — drizzle, seattle, trace amounts



def _rain_color_index(qpf_mm):
    """Map per-hour rain QPF (mm) to a solid precipitation palette index.

    Rain renders as a fully solid bar; QPF determines the brightness level.
    """
    if qpf_mm >= QPF_HIGH_MM:
        return 1   # rain bright
    if qpf_mm >= QPF_MID_MM:
        return 2   # rain mid
    return 3       # rain dim


def _snow_pattern(qpf_mm):
    """Map per-hour snow QPF (mm) to a (period, run_length) dim-pattern.

    The rendering condition is (y - phase) % period < run_length.  Off-pixels
    within the bar render as dim rather than transparent.  Top tier is
    bright-bright-dim — no fully solid fill.
    """
    if qpf_mm >= QPF_HIGH_MM:
        return (3, 2)   # bright-bright-dim — top tier
    if qpf_mm >= QPF_MID_MM:
        return (2, 1)   # bright-dim
    return (3, 1)       # bright-dim-dim


_TEMP_COLOR_LABELS = (
    "?",              # 0 — transparent placeholder, never emitted
    "extremely cold", # 1
    "very cold",      # 2
    "cold",           # 3
    "cool",           # 4
    "below average",  # 5
    "average",        # 6 — center
    "above average",  # 7
    "warm",           # 8
    "hot",            # 9
    "very hot",       # 10
    "extremely hot",  # 11
)


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


class Display(BaseDisplay):
    """Manages rendering weather data to a 64x32 RGB LED matrix.

    Public interface for the boot/scale text screen:
      set_location(text, color) — smart coordinate layout for the y=12 slot
      station_label  (y=20)    — station ID; write .text and .color directly
      network_label  (y=28)    — SSID during boot; min temp during scale preview

    Class-level color constants for status labels:
      QUERY_COLOR, SUCCESS_COLOR, FAILURE_COLOR
    """

    QUERY_COLOR   = QUERY_COLOR
    SUCCESS_COLOR = SUCCESS_COLOR
    FAILURE_COLOR = FAILURE_COLOR

    SCREEN_BOOT    = SCREEN_BOOT
    SCREEN_SCALE   = SCREEN_SCALE
    SCREEN_WEATHER = SCREEN_WEATHER

    def __init__(self, config):
        """Initialize display with layered groups: forecast graph, clock/temp, text screen."""
        super().__init__(config)
        self.screen = self.SCREEN_BOOT
        self.temp_min = int(config.get('TEMP_MIN', -5))
        self.temp_max = int(config.get('TEMP_MAX', 105))

        self.temperature_palette, self.precipitation_palette = self._build_palettes()

        # Map the 4 inherited text-label slots to their semantic roles in the
        # weather boot/scale screens.  The base pre-positions them at
        # y=4, 12, 20, 28, which is exactly what these slots need.
        self._top_label     = self._text_labels[0]
        self._top_label.color = self.temperature_palette[11]

        # Location slot (y=12) — _text_labels[1] is the main coordinate label.
        # Two extra elements support 3-digit longitude rendering; they are
        # appended to _status_group alongside the main label.
        self._loc_main_label = self._text_labels[1]
        self._loc_main_label.color = QUERY_COLOR

        self._loc_neg_palette = displayio.Palette(2)
        self._loc_neg_palette.make_transparent(0)
        self._loc_neg_palette[1] = QUERY_COLOR
        self._loc_neg_bitmap = displayio.Bitmap(2, 1, 2)
        self._loc_neg_bitmap[0, 0] = 1
        self._loc_neg_bitmap[1, 0] = 1
        # x=-99 parks the indicator off-screen until a 3-digit lon is shown.
        # y=10 matches the minus glyph's dy=2 above the label baseline at y=12.
        self._loc_neg_tg = displayio.TileGrid(
            bitmap=self._loc_neg_bitmap,
            pixel_shader=self._loc_neg_palette,
            x=-99, y=10,
            tile_width=2, tile_height=1,
        )
        self._loc_lon_label = self._make_label(color=QUERY_COLOR, y=12)
        self._status_group.append(self._loc_neg_tg)
        self._status_group.append(self._loc_lon_label)

        self.station_label = self._text_labels[2]
        self.station_label.color = QUERY_COLOR

        self.network_label = self._text_labels[3]
        self.network_label.color = QUERY_COLOR

        # Layer order: forecast graph (bottom) → clock/temp → text screen (top).
        # The text screen must be topmost so boot/scale labels overlay the graph.
        self._forecast_group = self._build_forecast_group()
        self._clock_group    = self._build_clock_group()
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
        # Designed for perceptual contrast: the first step away from average is
        # clearly colored, and intermediate steps are evenly spaced to the
        # extremes. Cold extreme is deeper navy; warm extreme is deeper orange.
        temperature_colors = [
            0xFFFFFF,  # 0  transparent placeholder
            0x143cd2,  # 1  extreme cold
            0x244ddd,  # 2
            0x355ee9,  # 3
            0x456ff4,  # 4
            0x5580ff,  # 5  first step from average
            0xeeeeee,  # 6  center neutral
            0xff871e,  # 7  first step from average
            0xff7717,  # 8
            0xff680f,  # 9
            0xff5808,  # 10
            0xff4800,  # 11 extreme warm
        ]
        temp_palette = displayio.Palette(len(temperature_colors))
        temp_palette.make_transparent(0)
        for i, color in enumerate(temperature_colors):
            temp_palette[i] = color

        precipitation_colors = [
            0xff0000,   # 0 — transparent placeholder
            0x0000D0,   # 1 — rain bright (>= QPF_HIGH_MM)
            0x000070,   # 2 — rain mid    (>= QPF_MID_MM)
            0x000028,   # 3 — rain dim    (<  QPF_MID_MM)
            0x44bbdd,   # 4 — snow bright
            0x0d2830,   # 5 — dim snow
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
        comfort_palette = displayio.Palette(2)
        comfort_palette.make_transparent(0)
        comfort_palette[1] = COMFORT_COLOR
        self._comfort_bitmap = displayio.Bitmap(64, 32, 2)
        self._comfort_grid = displayio.TileGrid(
            bitmap=self._comfort_bitmap,
            pixel_shader=comfort_palette,
            tile_width=self._comfort_bitmap.width,
            tile_height=self._comfort_bitmap.height,
        )
        group.append(self.precipitation_forecast_grid)
        group.append(self.temperature_forecast_grid)
        group.append(self._comfort_grid)
        return group

    def _build_clock_group(self):
        """Create the clock and current-temperature label group."""
        group = displayio.Group(x=0, y=0)
        self.clock_label = self._make_label(
            color=0xFFFFFF,
            anchor_point=(1, 0), anchored_position=(65, 0),
        )
        self.current_temp_label = self._make_label(color=0x808080, x=0, y=4)
        group.append(self.clock_label)
        group.append(self.current_temp_label)
        return group

    def _draw_comfort_zone(self):
        """Draw a horizontal band at COMFORT_LOW–COMFORT_HIGH °F into the comfort bitmap.

        Uses outward rounding (floor the top edge, ceil the bottom edge) so the
        band is always at least 1 pixel tall even on a very wide temperature scale.
        Both edges are clamped to [0, 31] to avoid out-of-bounds writes, which
        CircuitPython raises as ValueError.
        """
        self._comfort_bitmap.fill(0)
        scale_range = self.temp_max - self.temp_min
        midpoint_temp = (self.temp_max + self.temp_min) / 2
        scale_factor = scale_range / self._comfort_bitmap.height

        raw_top    = 16 + (midpoint_temp - COMFORT_HIGH) / scale_factor
        raw_bottom = 16 + (midpoint_temp - COMFORT_LOW)  / scale_factor

        # Outward rounding without math module: int() == floor for positive values;
        # ceiling uses the fractional-part check.
        y_top    = max(0, min(31, int(raw_top)))
        y_bottom = max(0, min(31, int(raw_bottom) + (1 if raw_bottom % 1 else 0)))

        if y_top > y_bottom:
            return

        for y in range(y_top, y_bottom + 1):
            for x in range(self._comfort_bitmap.width):
                self._comfort_bitmap[x, y] = 1

    # ------------------------------------------------------------------
    # Screen-switch methods
    # ------------------------------------------------------------------

    def show_status(self):
        """Switch to boot mode: show the text screen."""
        self.screen = self.SCREEN_BOOT
        self._status_group.hidden = False
        self.flush()

    def show_weather(self):
        """Switch to weather mode: hide the text screen and clear the comfort zone band."""
        self.screen = self.SCREEN_WEATHER
        self._comfort_bitmap.fill(0)
        self._status_group.hidden = True

    def show_scale(self, city, station_id):
        """Display the temperature scale preview screen.

        Shows the all-time high (orange, top), a comfort zone band at 68–72°F,
        and the all-time low (blue, bottom).  City and station ID are intentionally
        hidden so the comfort band is readable without text interference.
        Stays visible until show_weather() is called when the first forecast renders.
        """
        self.screen = self.SCREEN_SCALE
        self._top_label.text      = f"{self.temp_max}\u00b0"
        self._loc_main_label.text = ""
        self._loc_lon_label.text  = ""
        self._loc_neg_tg.x        = -99
        self.station_label.text   = ""
        self.network_label.text   = f"{self.temp_min}\u00b0"
        self.network_label.color  = self.temperature_palette[1]
        self._draw_comfort_zone()
        self._status_group.hidden = False
        self.flush()

    def set_location(self, text, color):
        """Set the location slot (y=12) of the text screen.

        For coordinate strings ("lat,lon"), formats both values to 2 decimal
        places and chooses a layout that fits within the 64px display width:

        - 2-digit longitude (abs < 100): one label "XX.XX, -XX.XX" with space
          (worst case 64px — Puerto Rico at "18.91, -66.12").
        - 3-digit longitude (abs >= 100): "XX.XX," label + 2px drawn dash at
          y=10 (matching the font minus height) + "XXX.XX" label (63px total).

        For all other text, renders as a single label unchanged.
        """
        parts = text.split(",") if text else []
        if len(parts) == 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                lat_str = f"{lat:.2f}"
                lon_abs = abs(lon)
                lon_str = f"{lon_abs:.2f}"
                if lon_abs < 100:
                    # 2-digit longitude — single label with space fits in 64px
                    sep = ", -" if lon < 0 else ", "
                    self._loc_main_label.text  = f"{lat_str}{sep}{lon_str}"
                    self._loc_main_label.color = color
                    self._loc_lon_label.text   = ""
                    self._loc_neg_tg.x         = -99
                else:
                    # 3-digit longitude — split rendering with 2px drawn dash
                    self._loc_main_label.text  = f"{lat_str},"
                    self._loc_main_label.color = color
                    neg_x = self._loc_main_label.width
                    if lon < 0:
                        self._loc_neg_palette[1] = color
                        self._loc_neg_tg.x       = neg_x
                        self._loc_lon_label.x    = neg_x + 2
                    else:
                        self._loc_neg_tg.x    = -99
                        self._loc_lon_label.x = neg_x
                    self._loc_lon_label.text  = lon_str
                    self._loc_lon_label.color = color
                return
            except (ValueError, TypeError):
                pass
        # Plain text — single label, extras hidden
        self._loc_main_label.text  = text or ""
        self._loc_main_label.color = color
        self._loc_lon_label.text   = ""
        self._loc_neg_tg.x         = -99

    # ------------------------------------------------------------------
    # Scale and data methods
    # ------------------------------------------------------------------

    def set_temp_scale(self, temp_min, temp_max):
        """Update the temperature scale used for the hourly forecast graph.

        Called after a successful scale query so that update_forecast() uses
        the queried range rather than the config defaults."""
        self.temp_min = temp_min
        self.temp_max = temp_max

    def update_clock(self, clock):
        """Update clock display with current time and sync status color."""
        t = clock.pretty_time
        print(f"Clock: {t!r} (group y={self._clock_group.y})")
        self.clock_label.text = t
        self.clock_label.color = clock.color
        self.flush()

    def mark_temp_stale(self):
        """Turn the current-temp label purple to signal stale hourly data.

        Called by the scheduler when hourly_update_age exceeds the staleness
        threshold. Reverts automatically the next time update_forecast() renders
        fresh data — that method always repaints current_temp_label from the
        temperature palette at x == 0."""
        self.current_temp_label.color = STALE_COLOR
        self.flush()

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
        scale_factor = scale_range / height
        midpoint_temp = (self.temp_max + self.temp_min) / 2

        x = 0
        peakpoint = height
        valleypoint = 0
        previous_point = None

        # Sparse precipitation bars encode QPF amount via dot density.  Rain and
        # snow sections are tracked independently because their liquid-equivalent
        # QPF can differ (snow_fraction splits the total).
        #
        # Each section's pattern is a (period, run_length) pair: draw the pixel
        # when (y - phase) % period < run_length.  This generalises the simple
        # "one dot every N rows" approach to allow runs of consecutive dots
        # (e.g. 2-on-1-off) without any change to the phase-walking logic.
        #
        # The phase is an absolute-y offset so that when the bar ceiling shifts
        # 1 pixel (probability changed slightly), interior dots stay on the same
        # grid rows across adjacent columns.  Only the explicitly-drawn ceiling
        # dot moves.  This breaks the horizontal-stripe pattern that would appear
        # if the dot grid shifted in lockstep with the bar height.
        #
        # On a pattern change, the phase resets so the section's ceiling row is
        # naturally in the dot grid — no gap at the top.  On continuation, the
        # phase walks by (period - 1) for period >= 3 (steeper diagonal, shorter
        # visible runs) or by 1 for period == 2.
        prev_rain_color_index = None
        prev_snow_pattern = None
        snow_phase = 0

        print("Plotting hours")
        for hour in hourly_data.values():

            if hour.end < current_time:
                print(f"Hour {x:2} expired at {hour.end}")
                continue

            hourly_temp_point = max(0, min(height - 1, round(height // 2 + (midpoint_temp - hour.temperature) / scale_factor)))

            # Track temperature extremes in the text overlay areas to reposition
            # labels so they don't obscure the temperature line.
            if x < self.current_temp_label.width or x > width - max(17, self.clock_label.width):
                peakpoint = min(peakpoint, hourly_temp_point)
                valleypoint = max(valleypoint, hourly_temp_point)

            for y in range(0, height):
                self.temperature_forecast_bitmap[x, y] = 0

            hour_date = hour.start[:10]
            hour_slot = None
            for slot in historical_data:
                if slot is not None and slot['date'] == hour_date:
                    hour_slot = slot
                    break
            color = _temp_color_index(len(self.temperature_palette), hour.temperature, hour_slot)

            if x > 0 and previous_point is not None and abs(previous_point - hourly_temp_point) > 1:
                # Draw line back to previous point to avoid ugly gaps.
                for (line_x, line_y) in line_generator((x, hourly_temp_point), (x - 1, previous_point)):
                    self.temperature_forecast_bitmap[line_x, line_y] = color
            else:
                self.temperature_forecast_bitmap[x, hourly_temp_point] = color

            if x == 0:
                self.current_temp_label.text = f"{hour.temperature}°"
                self.current_temp_label.color = self.temperature_palette[color]
                label = _TEMP_COLOR_LABELS[color] if 0 <= color < len(_TEMP_COLOR_LABELS) else "?"
                print(f"Temperature: '{hour.temperature}°' (color index {color}: {label})")

            if hour.precipitation:
                hourly_precipitation_point = height - int(((hour.precipitation / 100) * height) + 0.5)
            else:
                hourly_precipitation_point = height

            precip_start_row = hourly_precipitation_point
            total_precip_rows = height - precip_start_row
            snow_fraction = hour.snow_fraction
            rain_row_count = round((1.0 - snow_fraction) * total_precip_rows)
            snow_start_row = precip_start_row + rain_row_count

            # Compute rain color index (solid fill) and snow dot pattern.
            qpf_mm = hour.qpf_mm
            rain_color_index = _rain_color_index(qpf_mm * (1.0 - snow_fraction))
            snow_pattern = _snow_pattern(qpf_mm * snow_fraction)

            snow_period, snow_run = snow_pattern

            # Blend the first rain column of a tier change: interleave current
            # tier with its neighbor so the transition is gradual rather than a
            # hard edge.  No blend on the very first plotted column.
            blend_rain = (
                prev_rain_color_index is not None
                and rain_color_index != prev_rain_color_index
            )
            prev_rain_color_index = rain_color_index

            # On a snow pattern change, anchor the phase so the section's ceiling
            # row falls on the dot grid — the ceiling is always drawn explicitly
            # too, but starting in-phase means the first interior dot is exactly
            # one period below it rather than arbitrarily placed.  On continuation,
            # walk the phase to shift the dot column each hour.
            # Sparser periods (>= 3) walk faster (shift = period - 1 ≡ -1 mod
            # period) to steepen the diagonal and shorten visible linear runs.
            # Using period - 1 keeps gcd(shift, period) = 1 so all phase values
            # are visited; shift = 2 for period = 4 would skip half the phases.
            if snow_pattern != prev_snow_pattern:
                snow_phase = snow_start_row % snow_period
            elif snow_period > 1:
                snow_shift = (snow_period - 1) if snow_period >= 3 else 1
                snow_phase = (snow_phase + snow_shift) % snow_period
            prev_snow_pattern = snow_pattern

            for y in range(0, height):
                if y < precip_start_row:
                    self.precipitation_forecast_bitmap[x, y] = 0  # transparent
                elif y < snow_start_row:
                    # Rain section (ceiling + interior).  On a tier change,
                    # blend: alternate current tier with its neighbor, anchored
                    # at precip_start_row so parity is consistent across columns
                    # with the same bar height.
                    if blend_rain and y % 2 == 1:
                        # Step one tier dimmer, or back to mid if already at dim.
                        # Parity is anchored to the absolute row, not the bar top,
                        # so the pattern stays fixed when the bar height shifts.
                        self.precipitation_forecast_bitmap[x, y] = rain_color_index + 1 if rain_color_index < 3 else rain_color_index - 1
                    else:
                        self.precipitation_forecast_bitmap[x, y] = rain_color_index
                elif y == snow_start_row:
                    # Snow ceiling: always bright snow.
                    self.precipitation_forecast_bitmap[x, y] = 4
                # Snow interior: absolute-y phase grid keeps dots stable as
                # the bar height varies between hours.
                elif (y - snow_phase) % snow_period < snow_run:
                    self.precipitation_forecast_bitmap[x, y] = 4
                else:
                    self.precipitation_forecast_bitmap[x, y] = 5  # dim snow

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

        if x < width // 2:
            print(f"Warning: Only {x} hours plotted, forecast may be stale")

        self.flush()
        return x


