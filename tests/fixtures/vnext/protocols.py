"""Narrow protocols consumed by integration tests before the vNext runtime lands.

These protocols describe the seams owned by the future runtime.  They are deliberately
kept in the test fixture package so the integration harness does not become a second
runtime implementation or import the legacy orchestration APIs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HostAdapterProtocol(Protocol):
    def discover(self) -> Mapping[str, Any]: ...

    def create_top_level_task(self, action: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def wait_tasks(
        self, targets: Sequence[Mapping[str, Any]], cursor: str | None = None
    ) -> Mapping[str, Any]: ...

    def read_task(
        self, target: Mapping[str, Any], cursor: str | None = None
    ) -> Mapping[str, Any]: ...

    def send_message(
        self, target: Mapping[str, Any], envelope: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class StoreProtocol(Protocol):
    def record_dispatch_intent(self, intent: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def ingest_receipt(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def project_status(self, run_id: str) -> Mapping[str, Any]: ...


@runtime_checkable
class ContextKernelProtocol(Protocol):
    def build_snapshot(self, scope: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def reconstruct_snapshot(self, snapshot_ref: str) -> Mapping[str, Any]: ...

    def invalidate_from_contract_delta(
        self, delta: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class SchedulerProtocol(Protocol):
    def step(self) -> Sequence[Mapping[str, Any]]: ...

    def recover(self) -> Sequence[Mapping[str, Any]]: ...


def expected_vnext_modules() -> Mapping[str, str]:
    """Return the explicit future module seams from the technical specification."""

    return {
        "host_adapter": "allinluna_runtime.adapters.host.base",
        "store": "allinluna_runtime.store",
        "context": "allinluna_runtime.context",
        "global_scheduler": "allinluna_runtime.scheduler.global_scheduler",
        "local_scheduler": "allinluna_runtime.scheduler.local_scheduler",
    }
