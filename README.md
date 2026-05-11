# weatherpanel

Show the upcoming weather and current time on a 64×32 RGB LED matrix panel.

Fetches NOAA data and plots temperature and chance-of-precipitation for the
next 64 hours, column-by column.

Temperature is color-coded relative to historical highs and lows from the last
10 years for your (relatively) precise location. (Since it's NOAA data, this is
US-only. Sorry!)


Precipitation is shown as bottom-up bars, zero to 100%, with rain and snow
rendered separately.

Location is determined by IP geolocation by default, or can be set to fixed
coordinates in `settings.toml`.

## Software

That's this repo. It's in CircuitPython.

DST code adapted from [Minimal Time-Zone Handling for CircuitPython](https://emergent.unpythonic.net/01595021837).

## Hardware

- [Adafruit Matrix Portal S3](https://www.adafruit.com/product/5778) — the
  controller board; plugs directly into the back of a HUB75 matrix panel.
- A [64×32 HUB75 RGB LED matrix](https://www.adafruit.com/category/327) — any
  of Adafruit's 64×32 panels will work; pitch is a matter of viewing distance.

Power the matrix with a USB-C supply through the Matrix Portal S3.

## CircuitPython

Developed and tested with **CircuitPython 10.x** on the Matrix Portal S3.

The required Adafruit libraries are listed in `circuitpython-requirements.txt`. Refresh the
repo-local `lib/` cache automatically with:

```
make update-libraries
```

`make deploy` syncs that local `lib/` tree to `CIRCUITPY/lib/` on the device.

Or manage the libraries manually by copying them from the
[Adafruit CircuitPython Bundle](https://circuitpython.org/libraries) into the
repo's `lib/` directory:

- `adafruit_bitmap_font`
- `adafruit_display_text`
- `adafruit_connection_manager`
- `adafruit_ntp`
- `adafruit_requests`
- `adafruit_json_stream` (deployed to device; not called by app code but
  tested in `tests/test_json_stream.py` as a regression guard for library bugs)

The display also requires the **dogica-pixel-8** bitmap font. Copy it to
`fonts/dogica-pixel-8.pcf` on the device. The font is available from
[dogica](https://www.pentacom.jp/pentacom/bitfontmaker2/gallery/?id=3780).

## Setup

Copy `settings.toml` to `settings_real.toml` (which is gitignored) and edit it:

```toml
CIRCUITPY_WIFI_SSID = "your network"
CIRCUITPY_WIFI_PASSWORD = "your password"
```

Location defaults to IP geolocation. To use fixed coordinates instead:

```toml
LATITUDE = "42.39"
LONGITUDE = "-71.10"
```

Other optional settings:

| Key                 | Type | Default                              | Description                                                                               |
| ------------------- | ---- | ------------------------------------ | ----------------------------------------------------------------------------------------- |
| `SWAP_GREEN_BLUE`   | bool | `False`                              | Set to `1` if the panel has G/B pins reversed                                             |
| `TEMP_SCALE_RANGE`  | int  | `110`                                | °F span of the temperature color scale (~−5°F to ~105°F)                                  |
| `TEMP_MIDPOINT`     | int  | `50`                                 | Temperature (°F) at the center of the color scale                                         |
| `CLOCK_TWENTYFOUR`  | bool | `False`                              | Set to `1` for 24-hour time display                                                       |
| `CLOCK_DELIMITER`   | str  | `:`                                  | Hour/minute separator character                                                           |
| `RELOAD_ON_ERROR`   | bool | `False`                              | Reload code on unhandled exception; if `False`, the traceback stays on screen until reset |
| `USER_AGENT`        | str  | `weatherpanel (codeberg.org/mattdm/weatherpanel)` | User-Agent header for API requests (required by api.weather.gov)       |
| `GEOLOCATION_API`   | str  | `http://ip-api.com/json/`            | IP geolocation endpoint                                                                   |
| `GRIDPOINT_API`     | str  | `https://api.weather.gov/points/`    | NOAA gridpoint base URL                                                                   |
| `HISTORICAL_API`    | str  | `https://data.rcc-acis.org/GridData` | RCC ACIS historical normals endpoint                                                      |
| `AP_SSID`           | str  | `WP`                                 | SSID for the configuration portal access point (see [Wi-Fi configuration portal](#wi-fi-configuration-portal)) |
| `AP_PASSWORD`       | str  | _(open)_                             | Password for the portal AP; omit for an open network                                      |
| `FORCE_PORTAL`      | bool | `False`                              | Enter the configuration portal unconditionally on boot (debug/testing)                    |

## Deploy

With the Matrix Portal S3 mounted as a USB drive at `/run/media/$USER/CIRCUITPY` (which is the normal default on any modern Linux distro), run:

```
make
```

This compiles `src/*.py` to `.mpy` and copies everything to the device.
It also syncs the repo-local `lib/` tree to `CIRCUITPY/lib/`; if `lib/` has not
been populated yet, run `make update-libraries` first.

It requires `mpy-cross` at `./bin/mpy-cross`; run `make update-firmware` to
download the correct build automatically, or get it manually from the
[mpy-cross releases](https://adafruit-circuit-python.s3.amazonaws.com/index.html?prefix=bin/mpy-cross/).

The `.mpy` format is version-specific: `mpy-cross` must match the major
CircuitPython version on the device (e.g. a 10.x `mpy-cross` for CircuitPython
10.x). `make update-firmware` handles this automatically.

This is really unnecessary on the S3-based MatrixPortal, but previously I had this on the
older M4 board, and that was getting rather resource-limited.

`code.py` runs automatically when the board boots — that's CircuitPython's default. I've
kept it as just as minimal springboard into the main code.

## Updating CircuitPython

A full update (firmware + libraries + app code) goes like this:

```
make update-firmware   # flash new CircuitPython UF2, download matching mpy-cross
make update-libraries  # refresh repo-local lib/ from the latest bundles
make deploy            # sync repo-local lib/ to the device, then deploy app code
```

Each step can also be run independently. The firmware step is interactive:
the script waits for you to double-tap the Reset button to enter the bootloader,
then polls for the drives to appear and disappear.

`make update-firmware` also writes `.cp-version` in the repo, so
`make update-libraries` knows which CircuitPython bundle format to use even if
the device is not mounted.

**Note:** entering bootloader mode (double-tap Reset) cannot be automated —
you have to do that part yourself.

### Make target reference

| Target | What it does |
| --- | --- |
| `make device-info` | Print CircuitPython version and boot info from the mounted device |
| `make update-firmware` | Download and flash latest CircuitPython UF2; download matching `mpy-cross` |
| `make update-libraries` | Refresh the repo-local `lib/` tree via `circup` |
| `make libs` | Sync the current repo-local `lib/` tree to the device |
| `make deploy` | Sync `lib/`, then compile and copy project code, settings, and fonts to the device |

`make update-firmware` also accepts a version argument via the underlying script:

```
bin/update-firmware 9.2.4
```

Use `bin/update-firmware --dry-run` to preview what it would do without touching anything.

## External services

No API keys are required. The app uses:

- [ip-api.com](http://ip-api.com) — IP-based geolocation (HTTP, used only if no latitude / longitude is configured)
- [api.weather.gov](https://www.weather.gov/documentation/services-web-api) — NOAA hourly forecast and grid data (US locations only)
- [data.rcc-acis.org](https://www.rcc-acis.org/docs_griddata.html) — RCC ACIS PRISM historical temperature normals

## Supported timezones

The clock supports US timezones only (CircuitPython lacks `zoneinfo`):
Eastern, Central, Mountain, Arizona (no DST), Pacific, Alaska, and Hawaii.
Timezone is detected automatically from the geolocation or NOAA station data.

## Development

Install dev dependencies (host Python, not on the board):

```
pip install -r requirements-dev.txt
```

This includes `pytest`, `ruff`, and `circup` (for `make update-libraries`).

Run tests (from the `tests/` directory to avoid `code.py` shadowing
Python's stdlib `code` module):

```
cd tests && pytest .
```

### Sample forecasts

`tests/sample-forecasts/` contains recorded NOAA API responses from real
locations captured during interesting weather events.  Tests replay these
through the station parser via monkeypatched `network.get()`, so no live API
calls are needed and the test data is fully reproducible.

Each location has two files:

- `{name}_hourly.json` — trimmed `forecast/hourly` response (65 periods,
  only the fields `station.py` parses)
- `{name}_griddata.json` — trimmed `forecastGridData` response
  (`quantitativePrecipitation`, `snowfallAmount`, `updateTime` only)

To capture a new sample forecast, fetch the two endpoints for your target
gridpoint and trim them to the same structure.  For example, for Soda Springs
CA (`STO/87,93`):

```
curl -H 'Accept: application/json' \
     'https://api.weather.gov/gridpoints/STO/87,93/forecast/hourly' \
     -o soda_springs_hourly_raw.json

curl -H 'Accept: application/json' \
     'https://api.weather.gov/gridpoints/STO/87,93' \
     -o soda_springs_griddata_raw.json
```

Then trim with `python3 -c` or `jq` to keep only `properties.periods` (capped
at 65) for hourly, and `properties.{quantitativePrecipitation,snowfallAmount,updateTime}`
for griddata.

## Wi-Fi configuration portal

When the device needs to be configured — either because no Wi-Fi credentials have been set yet,
or because Wi-Fi has been failing for two minutes — it enters access-point mode and runs a
small web-based configuration form.

### How it works

1. The matrix displays a **Wi-Fi QR code**. Scan it with a phone to join the `WP` access point
   (or whatever `AP_SSID` is set to).
2. Once your phone connects, the display switches to a **URL QR code**. Scan it (or follow the
   printed link) to open the configuration form in your browser.
3. Choose your Wi-Fi network from the dropdown, enter the password, and optionally set your
   latitude/longitude. Submit the form.
4. The device saves the new settings to `settings.toml` and reboots. It then connects to your
   Wi-Fi and starts showing the weather.

### When the portal is entered automatically

| Trigger | Condition |
| ------- | --------- |
| No credentials | `CIRCUITPY_WIFI_SSID` is still the default placeholder value |
| Persistent failure | Wi-Fi has been unavailable for more than 2 minutes after boot |
| `FORCE_PORTAL = 1` | Debug/testing override in `settings.toml` |

If the portal is running because Wi-Fi was previously configured (the persistent-failure case),
it will automatically reload and retry after 30 minutes.

### Portal configuration keys

These optional keys in `settings.toml` control the portal's AP:

| Key           | Type | Default | Description                                             |
| ------------- | ---- | ------- | ------------------------------------------------------- |
| `AP_SSID`     | str  | `WP`    | SSID for the configuration access point                 |
| `AP_PASSWORD` | str  | _(open)_ | Password for the AP; omit or leave blank for open network |
| `FORCE_PORTAL`| bool | `False` | Enter portal unconditionally on boot (debug/testing)    |

### Simulating the portal on desktop

```
bin/simulate settings.toml --portal
```

This starts the portal loop on your desktop, using a real HTTP server bound to your LAN IP.
Open the printed URL in a browser to see and submit the form. No settings file is written by
default.

To do a full round-trip (submit the form and write settings back to disk):

```
bin/simulate my-settings.toml --portal --portal-save
```

Pass a path to a nonexistent file to bootstrap a fresh `settings.toml` from scratch:

```
bin/simulate new-device.toml --portal --portal-save
```
