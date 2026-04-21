"""Tests for bin/update-firmware bash script.

Approach: source the script in a subprocess (the BASH_SOURCE guard prevents main()
from running) and call individual functions with controlled environments.
Hardware-dependent steps (bootloader polling, UF2 copy, CIRCUITPY remount) are
not tested here — they require a real device.
"""

import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / "bin" / "update-firmware")


def _base_env() -> dict:
    """Environment for subprocesses: inherit everything except TMPDIR (which may
    point to a non-existent path on this machine, breaking mktemp calls)."""
    env = {**os.environ}
    env.pop("TMPDIR", None)
    return env


def bash(script: str, env: dict | None = None, input: str | None = None) -> subprocess.CompletedProcess:
    """Run a bash snippet that sources update-firmware first."""
    full_env = _base_env()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", "-c", f"source {SCRIPT}\n{script}"],
        capture_output=True,
        text=True,
        env=full_env,
        input=input,
    )


def run_script(*args: str, env: dict | None = None, input: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the script directly (runs main)."""
    full_env = _base_env()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", SCRIPT, *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=input,
    )


# ---------------------------------------------------------------------------
# Syntax
# ---------------------------------------------------------------------------

class TestSyntax:
    def test_bash_syntax_check(self):
        result = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_script_is_executable(self):
        assert os.access(SCRIPT, os.X_OK)


# ---------------------------------------------------------------------------
# Argument parsing (via main)
# ---------------------------------------------------------------------------

class TestArgParsing:
    def test_unknown_option_exits_nonzero(self):
        result = run_script("--badoption")
        assert result.returncode != 0

    def test_unknown_option_prints_error(self):
        result = run_script("--badoption")
        assert "Unknown option" in result.stderr

    def test_dry_run_exits_zero(self):
        result = run_script("--dry-run", "10.1.4")
        assert result.returncode == 0, result.stderr

    def test_dry_run_shows_marker(self):
        result = run_script("--dry-run", "10.1.4")
        assert "[dry-run]" in result.stdout

    def test_explicit_version_appears_in_dry_run(self):
        result = run_script("--dry-run", "9.2.4")
        assert "9.2.4" in result.stdout

    def test_explicit_version_in_uf2_url(self):
        result = run_script("--dry-run", "10.1.4")
        assert "adafruit-circuitpython-adafruit_matrixportal_s3-en_US-10.1.4.uf2" in result.stdout


# ---------------------------------------------------------------------------
# major_version
# ---------------------------------------------------------------------------

class TestMajorVersion:
    def test_stable_version(self):
        result = bash("major_version 10.1.4")
        assert result.returncode == 0
        assert result.stdout.strip() == "10"

    def test_older_major(self):
        result = bash("major_version 9.2.4")
        assert result.stdout.strip() == "9"

    def test_single_digit_minor(self):
        result = bash("major_version 10.0.0")
        assert result.stdout.strip() == "10"

    def test_prerelease(self):
        result = bash("major_version 10.0.0-beta.1")
        assert result.stdout.strip() == "10"


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------

def fake_uname_env(arch: str) -> tuple[dict, str]:
    """Return (env, tmpdir) with a fake uname script returning arch."""
    tmpdir = tempfile.mkdtemp(dir="/tmp")
    fake = Path(tmpdir) / "uname"
    fake.write_text(f'#!/bin/bash\necho "{arch}"\n')
    fake.chmod(0o755)
    env = {"PATH": f"{tmpdir}:{os.environ['PATH']}"}
    return env, tmpdir


class TestDetectPlatform:
    def test_x86_64_returns_linux_amd64(self):
        env, _ = fake_uname_env("x86_64")
        result = bash("detect_platform", env=env)
        assert result.stdout.strip() == "linux-amd64"
        assert result.returncode == 0

    def test_aarch64_returns_linux_aarch64(self):
        env, _ = fake_uname_env("aarch64")
        result = bash("detect_platform", env=env)
        assert result.stdout.strip() == "linux-aarch64"
        assert result.returncode == 0

    def test_unknown_arch_returns_empty(self):
        env, _ = fake_uname_env("mips")
        result = bash("detect_platform", env=env)
        assert result.stdout.strip() == ""

    def test_unknown_arch_warns(self):
        env, _ = fake_uname_env("mips")
        result = bash("detect_platform", env=env)
        assert "mips" in result.stderr
        assert "Unrecognised" in result.stderr


# ---------------------------------------------------------------------------
# read_current_version
# ---------------------------------------------------------------------------

class TestReadCurrentVersion:
    def test_reads_version_from_boot_out(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(d, "boot_out.txt").write_text(
                "Adafruit CircuitPython 10.1.4 on 2025-01-01; Adafruit Matrix Portal S3\n"
            )
            result = bash("read_current_version", env={"CIRCUITPY_VOLUME": d})
            assert result.stdout.strip() == "10.1.4"

    def test_reads_older_version(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(d, "boot_out.txt").write_text(
                "Adafruit CircuitPython 9.2.1 on 2024-06-01; Adafruit Matrix Portal S3\n"
            )
            result = bash("read_current_version", env={"CIRCUITPY_VOLUME": d})
            assert result.stdout.strip() == "9.2.1"

    def test_missing_boot_out_returns_empty(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            result = bash("read_current_version", env={"CIRCUITPY_VOLUME": d})
            assert result.stdout.strip() == ""
            assert result.returncode == 0

    def test_unreadable_format_returns_empty(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(d, "boot_out.txt").write_text("some unexpected content\n")
            result = bash("read_current_version", env={"CIRCUITPY_VOLUME": d})
            assert result.stdout.strip() == ""

    def test_prerelease_version_parsed(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(d, "boot_out.txt").write_text(
                "Adafruit CircuitPython 10.0.0-beta.1 on 2025-01-01; Adafruit Matrix Portal S3\n"
            )
            result = bash("read_current_version", env={"CIRCUITPY_VOLUME": d})
            assert result.stdout.strip() == "10.0.0-beta.1"


# ---------------------------------------------------------------------------
# save_cp_version
# ---------------------------------------------------------------------------

class TestSaveCpVersion:
    def test_dry_run_prints_write_target(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            target = Path(d) / ".cp-version"
            result = bash(
                "DRY_RUN=true; save_cp_version 10.1.4",
                env={"CP_VERSION_FILE": str(target)},
            )
            assert result.returncode == 0
            assert f"[dry-run] echo 10.1.4 > {target}" in result.stdout
            assert not target.exists()

    def test_real_write_creates_file(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            target = Path(d) / ".cp-version"
            result = bash(
                "DRY_RUN=false; save_cp_version 10.1.4",
                env={"CP_VERSION_FILE": str(target)},
            )
            assert result.returncode == 0
            assert target.read_text() == "10.1.4\n"

    def test_main_dry_run_mentions_version_file(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            target = Path(d) / ".cp-version"
            result = run_script(
                "--dry-run",
                "10.1.4",
                env={"CP_VERSION_FILE": str(target)},
            )
            assert result.returncode == 0
            assert f"[dry-run] echo 10.1.4 > {target}" in result.stdout


# ---------------------------------------------------------------------------
# wait_for_path
# ---------------------------------------------------------------------------

class TestWaitForPath:
    def test_times_out_on_missing_path(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            nonexistent = os.path.join(d, "does_not_exist")
            # POLL_INTERVAL=0 so it loops instantly; timeout=0 so first check fails
            result = bash(
                f"POLL_INTERVAL=0; wait_for_path {nonexistent!r} testlabel 0"
            )
            assert result.returncode == 1

    def test_succeeds_when_path_exists(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            result = bash(
                f"POLL_INTERVAL=0; wait_for_path {d!r} testlabel 0"
            )
            assert result.returncode == 0


# ---------------------------------------------------------------------------
# URL construction (via dry-run output)
# ---------------------------------------------------------------------------

class TestUrlConstruction:
    def test_uf2_url_correct(self):
        result = run_script("--dry-run", "10.1.4")
        expected = (
            "https://adafruit-circuit-python.s3.amazonaws.com"
            "/bin/adafruit_matrixportal_s3/en_US"
            "/adafruit-circuitpython-adafruit_matrixportal_s3-en_US-10.1.4.uf2"
        )
        assert expected in result.stdout

    def test_mpycross_url_correct_linux_amd64(self):
        env, _ = fake_uname_env("x86_64")
        result = run_script("--dry-run", "10.1.4", env=env)
        expected = (
            "https://adafruit-circuit-python.s3.amazonaws.com"
            "/bin/mpy-cross/linux-amd64"
            "/mpy-cross-linux-amd64-10.1.4.static"
        )
        assert expected in result.stdout

    def test_mpycross_url_correct_linux_aarch64(self):
        env, _ = fake_uname_env("aarch64")
        result = run_script("--dry-run", "10.1.4", env=env)
        expected = (
            "https://adafruit-circuit-python.s3.amazonaws.com"
            "/bin/mpy-cross/linux-aarch64"
            "/mpy-cross-linux-aarch64-10.1.4.static"
        )
        assert expected in result.stdout


# ---------------------------------------------------------------------------
# Major-version warning (via dry-run, with fake CIRCUITPY_VOLUME)
# ---------------------------------------------------------------------------

class TestMajorVersionWarning:
    def test_same_major_no_warning(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(d, "boot_out.txt").write_text(
                "Adafruit CircuitPython 10.0.3 on 2025-01-01; ...\n"
            )
            result = run_script("--dry-run", "10.1.4", env={"CIRCUITPY_VOLUME": d})
            assert "MAJOR VERSION CHANGE" not in result.stdout

    def test_different_major_shows_warning(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            Path(d, "boot_out.txt").write_text(
                "Adafruit CircuitPython 9.2.4 on 2025-01-01; ...\n"
            )
            result = run_script("--dry-run", "10.1.4", env={"CIRCUITPY_VOLUME": d})
            assert "MAJOR VERSION CHANGE" in result.stdout
            assert "9.2.4 -> 10.1.4" in result.stdout
