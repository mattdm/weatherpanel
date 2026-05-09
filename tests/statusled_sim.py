"""CPython-compatible simulation of the StatusLED hardware module.

Provides the same API as src/statusled.py but records the current LED color
in a module-level variable instead of writing to hardware.  bin/simulate
injects this into sys.modules["statusled"] before importing the scheduler,
then reads statusled_sim.color to paint the neopixel dot in the live window.
"""

BLUE   = (0,   0,   255)
PURPLE = (128, 0,   255)
CYAN   = (0,   255, 255)
YELLOW = (255, 200, 0)
GREEN  = (0,   255, 0)
ORANGE = (255, 128, 0)
RED    = (255, 0,   0)
OFF    = (0,   0,   0)

# Current LED color — updated by every StatusLED method call.
color = OFF


class StatusLED:
    """Simulated NeoPixel status indicator — same API as the real one."""

    def __init__(self):
        global color
        color = OFF
        self._sticky = False

    @property
    def color(self):
        """Current LED color as an (R, G, B) tuple (reads module-level state)."""
        import statusled_sim as _mod
        return _mod.color

    def working(self, c):
        global color
        color = c
        self._sticky = False

    def success(self):
        global color
        if not self._sticky:
            color = GREEN

    def failure(self):
        global color
        color = ORANGE
        self._sticky = True

    def wifi_down(self):
        global color
        color = RED
        self._sticky = True

    def clear(self):
        global color
        color = OFF
        self._sticky = False

    def idle(self):
        global color
        if not self._sticky:
            color = OFF
