"""Test configuration: mock CircuitPython-only modules so src/ can be imported on host Python."""
import sys
import types
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Stub out CircuitPython-only modules that src/ files import at module level.
# The actual hardware-dependent logic isn't under test; we only need the
# module-level imports to not crash on host Python.
for mod_name in [
    'network',
    'wifi',
    'rtc',
    'board',
    'displayio',
    'rgbmatrix',
    'framebufferio',
    'microcontroller',
    'watchdog',
    'supervisor',
    'adafruit_connection_manager',
    'adafruit_ntp',
    'adafruit_requests',
    'adafruit_bitmap_font',
    'adafruit_bitmap_font.bitmap_font',
    'adafruit_display_text',
    'adafruit_display_text.label',
    'neopixel',
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# display.py does `from adafruit_display_text.label import Label` — give
# the stub a callable Label so the import succeeds.
sys.modules['adafruit_display_text.label'].Label = type('Label', (), {})
sys.modules['adafruit_bitmap_font.bitmap_font'].load_font = lambda *a, **kw: None
