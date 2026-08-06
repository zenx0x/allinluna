from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_cli(environment: Path) -> Path:
    return environment / ("Scripts/allinluna.exe" if os.name == "nt" else "bin/allinluna")


def test_installed_cli_entrypoint_and_runtime_import(tmp_path: Path) -> None:
    environment = tmp_path / "venv"
    # Reuse the host's build tooling when present; the test still installs
    # into an isolated venv, but does not require network access to bootstrap
    # a second copy of setuptools/wheel.
    venv.EnvBuilder(with_pip=True, system_site_packages=True, clear=True).create(environment)
    python = _venv_python(environment)
    uv = shutil.which("uv")
    install_command = (
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(ROOT)]
        if uv
        else [str(python), "-m", "pip", "install", "--no-deps", str(ROOT)]
    )
    install = subprocess.run(
        install_command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    executable = _venv_cli(environment)
    assert executable.is_file()
    version = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=False
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("allinluna ")

    imported = subprocess.run(
        [str(python), "-c", "from allinluna_runtime.cli import main; print(main)"] ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    assert "function main" in imported.stdout
