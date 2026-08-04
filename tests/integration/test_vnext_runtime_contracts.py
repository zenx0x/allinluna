"""Executable acceptance skeleton for the future vNext runtime.

The runtime is intentionally absent at the current baseline.  Each scenario is guarded
at the module boundary, so a missing implementation is reported as a skip with the
explicit future import path rather than being converted into a passing fake test.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.fixtures.vnext.protocols import (
    ContextKernelProtocol,
    HostAdapterProtocol,
    SchedulerProtocol,
    StoreProtocol,
    expected_vnext_modules,
)


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


def import_vnext_runtime() -> dict[str, ModuleType]:
    """Import every required seam, failing the suite loudly when any is absent."""

    modules: dict[str, ModuleType] = {}
    missing: list[str] = []
    for name, module_path in expected_vnext_modules().items():
        try:
            modules[name] = importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            if exc.name and not exc.name.startswith("allinluna_runtime"):
                raise
            missing.append(f"{name}={module_path}")
    if missing:
        raise unittest.SkipTest(
            "vNext runtime seams are not present at this baseline: " + ", ".join(missing)
        )
    return modules


class VNextRuntimeIntegrationSkeleton(unittest.TestCase):
    """The method names are the concrete behaviors future adapters must satisfy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = import_vnext_runtime()

    def test_two_level_scheduler_dispatches_global_then_lane_ready_work(self) -> None:
        self.fail("Implement against GlobalScheduler + LocalScheduler protocol")

    def test_recursive_ownership_narrows_and_promotion_is_explicit(self) -> None:
        self.fail("Implement against WorkUnit ownership and PromotionRequest")

    def test_blocked_lane_does_not_stop_unrelated_ready_lane(self) -> None:
        self.fail("Implement against SchedulerProtocol")

    def test_dispatch_intent_and_receipt_ingestion_are_idempotent(self) -> None:
        self.fail("Implement against StoreProtocol")

    def test_coordinator_recovery_does_not_duplicate_dispatch(self) -> None:
        self.fail("Implement against StoreProtocol + SchedulerProtocol")

    def test_host_loss_preserves_worktree_and_commit_identity(self) -> None:
        self.fail("Implement against HostAdapterProtocol + WorkspaceAdapter")

    def test_contract_delta_invalidates_and_rebuilds_dependent_context(self) -> None:
        self.fail("Implement against ContextKernelProtocol")

    def test_jit_permission_is_requested_at_action_boundary(self) -> None:
        self.fail("Implement against the future PermissionIntent protocol")

    def test_legacy_plan_import_is_read_only_and_produces_vnext_run(self) -> None:
        self.fail("Implement against the future legacy_plan importer")

    def test_upper_views_exclude_raw_tool_logs(self) -> None:
        self.fail("Implement against ContextKernelProtocol typed views")


def _protocol_names_are_explicit() -> tuple[type[object], ...]:
    """Keep future test failures tied to named protocol seams, not duck typing."""

    return (
        HostAdapterProtocol,
        StoreProtocol,
        ContextKernelProtocol,
        SchedulerProtocol,
    )


if __name__ == "__main__":
    unittest.main()
