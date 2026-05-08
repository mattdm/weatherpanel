"""Onboard NeoPixel status indicator for the Matrix Portal S3.

The single NeoPixel on the board provides at-a-glance feedback about what the
device is currently doing, without obscuring the 64×32 matrix display.

Working colors indicate which operation is in progress:
  BLUE   -- hourly weather forecast fetch
  PURPLE -- historical data fetch
  CYAN   -- station info, location, bounds check
  YELLOW -- Wi-Fi connect / basic network

Result colors:
  GREEN  -- success (transient; cleared by the next idle() call)
  ORANGE -- failure (sticky; persists until a successful operation clears it)
  RED    -- Wi-Fi down (sticky; persists until reconnected)
"""
import board  # type: ignore
import neopixel  # type: ignore

BLUE   = (0,   0,   255)
PURPLE = (128, 0,   255)
CYAN   = (0,   255, 255)
YELLOW = (255, 200, 0)
GREEN  = (0,   255, 0)
ORANGE = (255, 128, 0)
RED    = (255, 0,   0)
OFF    = (0,   0,   0)


class StatusLED:
    """Wraps the onboard NeoPixel with operation-aware status semantics."""

    def __init__(self):
        self._pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=1.0)
        self._pixel.fill(OFF)
        self._sticky = False

    def working(self, color):
        """Show a working color for the operation in progress.

        Clears any sticky failure state — if we are actively trying again,
        show that rather than leaving the old error color lit.
        """
        self._pixel.fill(color)
        self._sticky = False

    def success(self):
        """Show green to indicate the last operation succeeded.

        Does nothing if a sticky failure is set — a single success does not
        clear a persistent error until the next idle() call.
        """
        if not self._sticky:
            self._pixel.fill(GREEN)

    def failure(self):
        """Show orange and latch sticky — persists until the next working() call."""
        self._pixel.fill(ORANGE)
        self._sticky = True

    def wifi_down(self):
        """Show red and latch sticky — persists until Wi-Fi is restored."""
        self._pixel.fill(RED)
        self._sticky = True

    def clear(self):
        """Turn the LED off and clear any sticky state unconditionally."""
        self._pixel.fill(OFF)
        self._sticky = False

    def idle(self):
        """Return to idle state between operations.

        Turns the LED off when there is no persistent issue. Leaves sticky
        orange/red alone so errors remain visible across loop iterations.
        """
        if not self._sticky:
            self._pixel.fill(OFF)
