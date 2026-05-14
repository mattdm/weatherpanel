"""CircuitPython entry point for weather panel display.

Loads configuration from defaults merged with environment variables from
settings.toml, then runs the main scheduler loop.

See ``src/appconfig.py`` for the full list of configuration keys, their
defaults, types, and non-obvious notes.
"""
import gc
gc.collect()
print(f"Free memory: {gc.mem_free()} (at start)")
from os import getenv

import supervisor
from appconfig import DEFAULTS, BOOL_KEYS, INT_KEYS, SECRETS, coerce_config

config = dict(DEFAULTS)

for conf in config:
    val = getenv(conf)
    if val:
        config[conf] = val
        if conf in SECRETS:
            print(f"{conf} = '****'")
        else:
            print(f"{conf} = '{val}'")
    else:
        print(f"{conf} = '{config[conf]}' (default)")

# getenv() always returns strings; coerce bool and int keys to their proper types
# so that settings.toml values like SWAP_GREEN_BLUE = 0 are treated as falsy.
config, _config_errors = coerce_config(config, BOOL_KEYS, INT_KEYS)

# sticky_on_error keeps the display showing error traceback until user intervention.
# Set before any further processing so that even a config error produces a visible
# traceback rather than a silent reload loop.
print(f"Setting reload on error to {config['RELOAD_ON_ERROR']}")
supervisor.set_next_code_file(None, reload_on_error=config['RELOAD_ON_ERROR'], sticky_on_error=True)

if _config_errors:
    for _k, _msg in _config_errors.items():
        print(f"Config error: {_msg}")

import board, digitalio
up = digitalio.DigitalInOut(board.BUTTON_UP)
up.switch_to_input(pull=digitalio.Pull.UP)
dn = digitalio.DigitalInOut(board.BUTTON_DOWN)
dn.switch_to_input(pull=digitalio.Pull.UP)
button_held = not up.value or not dn.value

if (_config_errors or config.get('FORCE_PORTAL') or not config.get('CIRCUITPY_WIFI_SSID')
        or not config.get('LATITUDE') or not config.get('LONGITUDE') or button_held):
    import portal
    portal.run(config, config_errors=_config_errors)
else:
    import scheduler
    try:
        scheduler.run(config)
    except scheduler.PortalNeeded:
        print("Wi-Fi unavailable — entering configuration portal")
        import portal
        portal.run(config)
