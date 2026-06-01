"""Tests for appconfig color and settings file loading."""
from pathlib import Path

from appconfig import COLOR_DEFAULTS, DEFAULTS, load_colors, load_settings


class TestLoadSettings:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = load_settings(str(tmp_path / "no_such_file.toml"))
        assert result == {}

    def test_quoted_string_value(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('CIRCUITPY_WIFI_SSID = "MyNetwork"\n')
        assert load_settings(str(f))['CIRCUITPY_WIFI_SSID'] == 'MyNetwork'

    def test_bare_integer_returned_as_string(self, tmp_path):
        """Bare integer literals mirror what os.getenv() returns on CircuitPython."""
        f = tmp_path / "settings.toml"
        f.write_text('SWAP_GREEN_BLUE = 0\nAUTO_SCALE = 1\n')
        result = load_settings(str(f))
        assert result['SWAP_GREEN_BLUE'] == '0'
        assert result['AUTO_SCALE'] == '1'

    def test_negative_integer_value(self, tmp_path):
        f = tmp_path / "settings.toml"
        f.write_text('TEMP_MIN = -5\n')
        assert load_settings(str(f))['TEMP_MIN'] == '-5'


class TestColorDefaults:
    def test_all_keys_present(self):
        """COLOR_DEFAULTS contains all 16 expected color keys."""
        expected = {
            'TEMP_COLOR_COLD', 'TEMP_COLOR_CENTER', 'TEMP_COLOR_WARM',
            'COMFORT_COLOR',
            'RAIN_COLOR_BRIGHT', 'RAIN_COLOR_MID', 'RAIN_COLOR_DIM',
            'SNOW_COLOR_BRIGHT', 'SNOW_COLOR_DIM',
            'STATUS_QUERY_COLOR', 'STATUS_SUCCESS_COLOR',
            'STATUS_FAILURE_COLOR', 'STATUS_STALE_COLOR',
            'CLOCK_NORMAL_COLOR', 'CLOCK_ERROR_COLOR', 'CLOCK_UNCERTAIN_COLOR',
        }
        assert set(COLOR_DEFAULTS.keys()) == expected


class TestLoadColors:
    def test_missing_file_returns_defaults(self, tmp_path):
        result = load_colors(str(tmp_path / "no_such_file.toml"))
        assert result == COLOR_DEFAULTS

    def test_valid_override_applied(self, tmp_path):
        f = tmp_path / "colors.toml"
        f.write_text('TEMP_COLOR_COLD = "0xff0000"\n')
        result = load_colors(str(f))
        assert result['TEMP_COLOR_COLD'] == 0xff0000

    def test_hash_format_accepted(self, tmp_path):
        """'#rrggbb' format is accepted alongside '0xrrggbb'."""
        f = tmp_path / "colors.toml"
        f.write_text('TEMP_COLOR_WARM = "#00ff00"\n')
        result = load_colors(str(f))
        assert result['TEMP_COLOR_WARM'] == 0x00ff00

    def test_invalid_hex_keeps_default(self, tmp_path):
        f = tmp_path / "colors.toml"
        f.write_text('TEMP_COLOR_COLD = "not_a_color"\n')
        result = load_colors(str(f))
        assert result['TEMP_COLOR_COLD'] == COLOR_DEFAULTS['TEMP_COLOR_COLD']

    def test_unrecognized_key_ignored(self, tmp_path):
        f = tmp_path / "colors.toml"
        f.write_text('UNKNOWN_KEY = "0x123456"\n')
        result = load_colors(str(f))
        assert 'UNKNOWN_KEY' not in result

    def test_case_insensitive_hex(self, tmp_path):
        f = tmp_path / "colors.toml"
        f.write_text('TEMP_COLOR_COLD = "0xAABBCC"\n')
        result = load_colors(str(f))
        assert result['TEMP_COLOR_COLD'] == 0xAABBCC


class TestColorParity:
    """Parity test: colors.toml and COLOR_DEFAULTS in appconfig.py must agree.

    This is not a tautology.  The two are independent artifacts: COLOR_DEFAULTS
    is the firmware fallback (used when colors.toml is absent or a key is
    missing), while colors.toml is the committed template that ships on the
    device.  Updating one without the other silently diverges their values.
    Changing a color intentionally requires editing both files — this test
    makes that an enforced, explicit step rather than an easy miss.
    """

    def test_colors_toml_matches_defaults(self):
        repo_root = Path(__file__).parent.parent
        result = load_colors(str(repo_root / "colors.toml"))
        assert result == COLOR_DEFAULTS


class TestSettingsParity:
    """Parity test: specific keys in settings.toml must match DEFAULTS in appconfig.py.

    This is not a tautology.  settings.toml is the committed user-facing
    template; DEFAULTS is the firmware fallback.  Only the API endpoint keys
    are checked — credentials, location fields, and boolean flags are
    intentional placeholders or deployment-tuned values that legitimately
    differ from the code defaults.
    """

    _PARITY_KEYS = ('USER_AGENT', 'GRIDPOINT_API', 'HISTORICAL_API')

    def test_api_keys_match_defaults(self):
        repo_root = Path(__file__).parent.parent
        loaded = load_settings(str(repo_root / "settings.toml"))
        for key in self._PARITY_KEYS:
            assert loaded[key] == DEFAULTS[key], (
                f"settings.toml[{key!r}] = {loaded[key]!r} "
                f"but DEFAULTS[{key!r}] = {DEFAULTS[key]!r}"
            )
