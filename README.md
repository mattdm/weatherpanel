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

Developed and tested with **CircuitPython 9.x** on the Matrix Portal S3.
Download firmware from [circuitpython.org](https://circuitpython.org/board/adafruit_matrixportal_s3/).

Install these libraries from the
[Adafruit CircuitPython Bundle](https://circuitpython.org/libraries) into the
`lib/` directory on `CIRCUITPY`:

- `adafruit_bitmap_font`
- `adafruit_display_text`
- `adafruit_connection_manager`
- `adafruit_ntp`
- `adafruit_requests`

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
| `CLOCK_DELIMINATOR` | str  | `:`                                  | Hour/minute separator character                                                           |
| `RELOAD_ON_ERROR`   | bool | `False`                              | Reload code on unhandled exception; if `False`, the traceback stays on screen until reset |
| `USER_AGENT`        | str  | `weatherpanel (codeberg.org/mattdm/weatherpanel)` | User-Agent header for API requests (required by api.weather.gov)       |
| `GEOLOCATION_API`   | str  | `http://ip-api.com/json/`            | IP geolocation endpoint                                                                   |
| `GRIDPOINT_API`     | str  | `https://api.weather.gov/points/`    | NOAA gridpoint base URL                                                                   |
| `HISTORICAL_API`    | str  | `https://data.rcc-acis.org/GridData` | RCC ACIS historical normals endpoint                                                      |

## Deploy

With the Matrix Portal S3 mounted as a USB drive at `/run/media/$USER/CIRCUITPY` (which is the normal default on any modern Linux distro), run:

```
make
```

This compiles `src/*.py` to `.mpy` and copies everything to the device.
It requires `mpy-cross` at `./bin/mpy-cross`; download the correct build for
your host from the [mpy-cross releases](https://adafruit-circuit-python.s3.amazonaws.com/index.html?prefix=bin/mpy-cross/).

This is really unnecessary on the S3-based MatrixPortal, but previously I had this on the
older M4 board, and that was getting rather resource-limited.

`code.py` runs automatically when the board boots — that's CircuitPython's default. I've
kept it as just as minimal springboard into the main code.

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

Run tests (from the `tests/` directory to avoid `code.py` shadowing
Python's stdlib `code` module):

```
cd tests && pytest .
```

## Future

Right now, you have to plug it in to a computer and edit files to set up the wifi and (optional
but recommended) the location. I have the vague idea of, when wifi isn't configured or fails too
many times, have it switch to access-point mode with a little configuration app. Maybe even with
a wifi QR code displayed on the matrix.
