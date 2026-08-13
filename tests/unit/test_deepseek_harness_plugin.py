from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "deepseek-harness"


def _profile_files(*, profile_dir: Path) -> dict[Path, str]:
    spec = importlib.util.spec_from_file_location(
        "install_allinflash_profile", ROOT / "scripts" / "install_allinflash_profile.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.profile_files(profile_dir=profile_dir, allinluna_root=ROOT)


def test_deepseek_harness_plugin_has_valid_node_module_metadata() -> None:
    package = json.loads((PLUGIN / "package.json").read_text(encoding="utf-8"))

    assert package["name"] == "@zenx0x/allinflash"
    assert package["type"] == "module"
    assert package["peerDependencies"]["@deepseek-ai/dsh-tools"] == "^0.1.0-rc.6"


def test_deepseek_harness_plugin_parses_in_node() -> None:
    result = subprocess.run(
        ["node", "--check", str(PLUGIN / "index.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


def test_allinflash_profile_contains_the_exact_plugin_and_runtime_command(tmp_path: Path) -> None:
    files = _profile_files(profile_dir=tmp_path / "profiles" / "allinflash")
    package = json.loads(next(content for path, content in files.items() if path.name == "package.json"))
    patch = next(content for path, content in files.items() if path.name == "cordis.patch.yml")

    assert package["dependencies"]["@zenx0x/allinflash"].startswith("file:")
    assert package["dsh"]["profile"]["bundles"] == ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]
    assert "- insert:" in patch
    assert "name: '@zenx0x/allinflash'" in patch
    assert "commandArgs: ['run', 'allinluna']" in patch
