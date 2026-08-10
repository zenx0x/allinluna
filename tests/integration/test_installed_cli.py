from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_cli(environment: Path) -> Path:
    return environment / ("Scripts/allinluna.exe" if os.name == "nt" else "bin/allinluna")


def _build_wheel(tmp_path: Path) -> Path:
    configured_wheel = os.environ.get("ALLINLUNA_WHEEL")
    if configured_wheel:
        wheel = Path(configured_wheel).resolve()
        assert wheel.is_file(), f"configured candidate wheel is missing: {wheel}"
        return wheel

    wheel_dir = tmp_path / "wheel"
    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheel_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(wheel_dir.glob("allinluna-*.whl"))
    assert len(wheels) == 1, f"expected exactly one All in Luna wheel, found: {wheels}"
    return wheels[0]


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return environment


def test_installed_cli_entrypoint_and_runtime_import(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path)
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = _venv_python(environment)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_environment(),
    )
    assert install.returncode == 0, install.stdout + install.stderr

    executable = _venv_cli(environment)
    assert executable.is_file()
    version = subprocess.run(
        [str(executable), "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_environment(),
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("allinluna ")

    help_result = subprocess.run(
        [str(executable), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_environment(),
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "compile" in help_result.stdout

    compiled = subprocess.run(
        [str(executable), "compile", "--goal", "package smoke coverage"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_environment(),
    )
    assert compiled.returncode == 0, compiled.stderr
    assert '"goal": "package smoke coverage"' in compiled.stdout

    imported = subprocess.run(
        [str(python), "-c", "from allinluna_runtime.cli import main; print(main)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_environment(),
    )
    assert imported.returncode == 0, imported.stderr
    assert "function main" in imported.stdout
