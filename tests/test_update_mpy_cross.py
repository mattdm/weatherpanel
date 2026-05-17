"""Tests for bin/update-mpy-cross bash script.

Approach: source the script in a subprocess (the BASH_SOURCE guard prevents main()
from running) and call individual functions with controlled environments.
"""

import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = str(Path(__file__).parent.parent / "bin" / "update-mpy-cross")


def _base_env() -> dict:
    """Environment for subprocesses: inherit everything except TMPDIR."""
    env = {**os.environ}
    env.pop("TMPDIR", None)
    return env


def bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a bash snippet that sources update-mpy-cross first."""
    full_env = _base_env()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", "-c", f"source {SCRIPT}\n{script}"],
        capture_output=True,
        text=True,
        env=full_env,
    )


def run_script(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the script directly (runs main)."""
    full_env = _base_env()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", SCRIPT, *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


def fake_uname_env(arch: str) -> tuple[dict, str]:
    """Return (env, tmpdir) with a fake uname script returning arch."""
    tmpdir = tempfile.mkdtemp(dir="/tmp")
    fake = Path(tmpdir) / "uname"
    fake.write_text(f'#!/bin/bash\necho "{arch}"\n')
    fake.chmod(0o755)
    env = {"PATH": f"{tmpdir}:{os.environ['PATH']}"}
    return env, tmpdir


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
# Argument parsing
# ---------------------------------------------------------------------------

class TestArgParsing:
    def test_unknown_option_exits_nonzero(self):
        result = run_script("--badoption")
        assert result.returncode != 0

    def test_unknown_option_prints_error(self):
        result = run_script("--badoption")
        assert "Unknown option" in result.stderr

    def test_dry_run_exits_zero(self):
        result = run_script("--dry-run", "10.2.0")
        assert result.returncode == 0, result.stderr

    def test_dry_run_shows_marker(self):
        result = run_script("--dry-run", "10.2.0")
        assert "[dry-run]" in result.stdout

    def test_explicit_version_appears_in_dry_run(self):
        result = run_script("--dry-run", "10.2.0")
        assert "10.2.0" in result.stdout


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------

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
# resolve_version
# ---------------------------------------------------------------------------

class TestResolveVersion:
    def test_reads_cp_version_file(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            target = Path(d) / ".cp-version"
            target.write_text("10.2.0\n")
            result = bash("resolve_version", env={"CP_VERSION_FILE": str(target)})
            assert result.stdout.strip() == "10.2.0"
            assert result.returncode == 0

    def test_falls_back_to_boot_out(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            absent = Path(d) / ".cp-version-missing"
            Path(d, "boot_out.txt").write_text(
                "Adafruit CircuitPython 10.1.4 on 2025-01-01; Adafruit Matrix Portal S3\n"
            )
            result = bash(
                "resolve_version",
                env={"CP_VERSION_FILE": str(absent), "CIRCUITPY_VOLUME": d},
            )
            assert result.stdout.strip() == "10.1.4"

    def test_missing_both_returns_empty(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            absent = Path(d) / ".cp-version-missing"
            result = bash(
                "resolve_version",
                env={"CP_VERSION_FILE": str(absent), "CIRCUITPY_VOLUME": d},
            )
            assert result.stdout.strip() == ""
            assert result.returncode == 0

    def test_no_version_arg_uses_cp_version_file(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            target = Path(d) / ".cp-version"
            target.write_text("10.2.0\n")
            result = run_script("--dry-run", env={"CP_VERSION_FILE": str(target)})
            assert result.returncode == 0
            assert "10.2.0" in result.stdout

    def test_no_version_anywhere_exits_nonzero(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            absent = Path(d) / ".cp-version-missing"
            result = run_script(
                env={"CP_VERSION_FILE": str(absent), "CIRCUITPY_VOLUME": d}
            )
            assert result.returncode != 0
            assert "version unknown" in result.stderr


# ---------------------------------------------------------------------------
# URL construction (via dry-run output)
# ---------------------------------------------------------------------------

class TestUrlConstruction:
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

    def test_prerelease_version_in_url(self):
        env, _ = fake_uname_env("x86_64")
        result = run_script("--dry-run", "10.0.0-beta.1", env=env)
        assert "mpy-cross-linux-amd64-10.0.0-beta.1.static" in result.stdout
