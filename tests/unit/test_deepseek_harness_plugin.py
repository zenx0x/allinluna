from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

from allinluna_runtime.adapters.host.base import (
    DEEPSEEK_HARNESS_CREATE_TASK_TOOL,
    HostAction,
)
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
    assert package["dsh"]["profile"]["bundles"] == [
        "@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "@zenx0x/allinflash"
    ]
    assert "- id: allinflash" in patch
    assert "commandArgs: ['run', 'allinluna', '--adapter', 'deepseek-harness']" in patch


def test_deepseek_harness_plugin_is_a_dsh_bundle() -> None:
    package = json.loads((PLUGIN / "package.json").read_text(encoding="utf-8"))

    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"


def test_deepseek_harness_top_level_action_keeps_its_exact_opcode() -> None:
    action = HostAction(
        action_id="action-allinflash",
        kind="create-top-level-task",
        idempotency_key="intent:allinflash",
        tool=DEEPSEEK_HARNESS_CREATE_TASK_TOOL,
        arguments={"target": {"type": "project"}, "prompt": "lane bootstrap", "model": "deepseek-v4-flash", "title": "Lane"},
        execution_class="top_level_task",
        tool_policy={"exact_tool": DEEPSEEK_HARNESS_CREATE_TASK_TOOL, "substitutions": [], "on_unavailable": "block"},
        host_capability_required=DEEPSEEK_HARNESS_CREATE_TASK_TOOL,
    )

    assert action.tool == DEEPSEEK_HARNESS_CREATE_TASK_TOOL
    assert action.tool_policy["exact_tool"] == DEEPSEEK_HARNESS_CREATE_TASK_TOOL


def test_cli_deepseek_harness_adapter_emits_the_allinflash_opcode() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "runtime.db"
        result = subprocess.run(
            [
                "uv", "run", "allinluna", "--adapter", "deepseek-harness", "--db", str(database),
                "start", "--goal", "verify allinflash adapter", "--model", "deepseek-v4-flash",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    actions = payload["actions"]
    assert actions
    assert actions[0]["tool"] == DEEPSEEK_HARNESS_CREATE_TASK_TOOL
    assert actions[0]["host_capability_required"] == DEEPSEEK_HARNESS_CREATE_TASK_TOOL


def test_cli_deepseek_harness_adapter_ingests_an_exact_child_receipt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "runtime.db"
        start = subprocess.run(
            [
                "uv", "run", "allinluna", "--adapter", "deepseek-harness", "--db", str(database),
                "start", "--goal", "verify All in Flash child receipt", "--model", "deepseek-v4-flash",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert start.returncode == 0, start.stderr
        start_payload = json.loads(start.stdout)
        action = start_payload["actions"][0]
        receipt = {
            "receipt_id": f"allinflash-receipt-{action['action_id']}",
            "status": "ready",
            "source": "deepseek-harness",
            "host_adapter": "deepseek-harness",
            "host_id": "allinflash-dsh",
            "thread_id": "dsh-child-test",
            "action_id": action["action_id"],
            "action_kind": action["kind"],
            "idempotency_key": action["idempotency_key"],
            "dispatch_key": action["idempotency_key"],
            "dispatch_id": action["dispatch_id"],
            "task_id": action["task_id"],
            "actual": True,
            "actual_tool": DEEPSEEK_HARNESS_CREATE_TASK_TOOL,
            "actual_capability": DEEPSEEK_HARNESS_CREATE_TASK_TOOL,
            "action_contract_hash": action["action_contract_hash"],
            "payload": {"provider": "spawn", "child_id": "dsh-child-test"},
        }
        run_id = start_payload["run_ref"].removeprefix("run://")
        ingest = subprocess.run(
            [
                "uv", "run", "allinluna", "--adapter", "deepseek-harness", "--db", str(database),
                "ingest-receipt", run_id, json.dumps(receipt),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    assert ingest.returncode == 0, ingest.stderr
    result = json.loads(ingest.stdout)
    assert result["receipt"]["thread_id"] == "dsh-child-test"
    assert result["receipt"]["actual_tool"] == DEEPSEEK_HARNESS_CREATE_TASK_TOOL
