"""The single public ``allinluna`` Skill API.

This facade is the only user-facing compiler surface.  It accepts a goal,
existing plan, active run, or Research Routes packet and returns typed vNext
inputs/actions.  It does not expose internal phase, resource-card, or
governance concepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..domain import RunIntent
from ..engine.coordinator import CoordinatorEngine
from ..resource import ResourceBroker
from ..store import Store
from .base import CompiledRunGraph
from .goal_compiler import GoalCompiler, RepositoryContextInspector, TaskDecomposer
from .manifest import PackRegistry, builtin_registry
from .research_routes import ResearchRoutesBridge


EXACT_ACTION_RELAY_CONTRACT: Mapping[str, Any] = {
    "protocol": "allinluna-action-relay/v1",
    "priority": "highest",
    "rules": (
        "Invoke HostAction.tool exactly with HostAction.arguments.",
        "Never translate, approximate, or substitute a host tool.",
        "A top_level_task never falls back to a subagent, current thread, or direct execution.",
        "Ingest the raw receipt immediately, then tick again.",
        "If the exact capability is unavailable, return HOST_CAPABILITY_BLOCKED.",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class PermissionIntent:
    action: str
    scopes: tuple[str, ...] = ()
    reason: str = ""
    status: str = "requested"

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "scopes": list(self.scopes), "reason": self.reason, "status": self.status}


class JITPermissionRouter:
    """Permission decisions occur when an action is about to cross a boundary."""

    def request(self, action: str, *, scopes: tuple[str, ...] = (), policy: str = "ask", authorized: bool = False, reason: str = "") -> PermissionIntent:
        if policy == "deny":
            status = "denied"
        elif policy == "allow" and authorized:
            status = "allowed"
        else:
            status = "ask"
        return PermissionIntent(action, tuple(scopes), reason, status)


@dataclass(frozen=True)
class SkillCompilation:
    intent: RunIntent
    task_graph: CompiledRunGraph
    input_kind: str
    compatibility: Mapping[str, Any] = field(default_factory=dict)
    permission_intents: tuple[PermissionIntent, ...] = ()
    action_relay_contract: Mapping[str, Any] = field(default_factory=lambda: EXACT_ACTION_RELAY_CONTRACT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_kind": self.input_kind,
            "run_intent": self.intent.to_dict(),
            "task_graph": self.task_graph.to_dict(),
            "compatibility": dict(self.compatibility),
            "permission_intents": [item.to_dict() for item in self.permission_intents],
            "action_relay_contract": dict(self.action_relay_contract),
        }


class SinglePublicSkillAPI:
    """Public API for one Skill and all supported input forms."""

    id = "allinluna"
    version = "1.1.1"

    def __init__(
        self,
        *,
        registry: PackRegistry | None = None,
        permission_router: JITPermissionRouter | None = None,
        goal_compiler: GoalCompiler | None = None,
    ) -> None:
        self.registry = registry or builtin_registry()
        self.permissions = permission_router or JITPermissionRouter()
        self.goal_compiler = goal_compiler or GoalCompiler()

    def compile_run_intent(self, request: str | Mapping[str, Any] | RunIntent, *, repository: Mapping[str, Any] | None = None, pack: str | None = None) -> tuple[RunIntent, str, Mapping[str, Any]]:
        if isinstance(request, RunIntent):
            return request, str(request.pack.id), {}
        if isinstance(request, str):
            return self._goal_intent(request, repository=repository, pack=pack), "idea", {}
        raw = dict(request)
        if isinstance(raw.get("run_intent"), Mapping):
            intent = RunIntent.from_dict(raw["run_intent"])
            return intent, "run-intent", {}
        if raw.get("research_route") is not None or raw.get("route_packet") is not None:
            packet = raw.get("research_route", raw.get("route_packet"))
            bridge = self.registry.require("research-routes-bridge")
            return bridge.to_intent(packet, repository=repository), "research-route", {}
        if raw.get("existing_plan") is not None or raw.get("plan") is not None:
            from ..compat.legacy_plan import LegacyPlanImportAPI
            imported = LegacyPlanImportAPI().translate(raw.get("existing_plan", raw.get("plan")))
            return imported.intent, "existing-plan", imported.report.to_dict()
        if raw.get("active_run") is not None or raw.get("run_state") is not None:
            from ..compat.legacy_run_state import LegacyRunStateImportAPI
            imported = LegacyRunStateImportAPI().translate(raw.get("active_run", raw.get("run_state")))
            return imported.intent, "active-run", imported.report.to_dict()
        goal = raw.get("goal") or raw.get("idea") or raw.get("objective")
        if not goal:
            raise ValueError("allinluna accepts a goal, existing_plan, active_run, or research_route")
        return self._goal_intent(str(goal), repository=repository or raw.get("repository"), pack=pack or raw.get("pack"), raw=raw), "idea", {}

    def compile(self, request: str | Mapping[str, Any] | RunIntent, *, repository: Mapping[str, Any] | None = None, pack: str | None = None) -> SkillCompilation:
        intent, input_kind, compatibility = self.compile_run_intent(request, repository=repository, pack=pack)
        workflow_pack = self.registry.require(str(intent.pack.id), str(intent.pack.version))
        graph = self.goal_compiler.compile(intent, workflow_pack)
        # Permissions are intentionally absent at compile time. The Action
        # Bridge persists a PermissionIntent only when a concrete external
        # action reaches the dispatch boundary.
        return SkillCompilation(intent, graph, input_kind, compatibility, (), EXACT_ACTION_RELAY_CONTRACT)

    def start(self, request: str | Mapping[str, Any] | RunIntent, *, store: Store | None = None, db_path: str | Path | None = None, repository: Mapping[str, Any] | None = None, pack: str | None = None, dispatch: bool = False, host: Any = None) -> Mapping[str, Any]:
        compilation = self.compile(request, repository=repository, pack=pack)
        owned_store = store is None
        runtime_store = store or Store(str(db_path or "runtime.db"))
        try:
            run_id = compilation.task_graph.run_id
            engine = CoordinatorEngine(runtime_store, host=host, resource_broker=ResourceBroker(compilation.intent.resource_envelope))
            engine.start(compilation.intent, compilation.task_graph, run_id=run_id)
            result: dict[str, Any] = {"run_ref": f"run://{run_id}", "compilation": compilation.to_dict(), "status": "created", "actions": []}
            if dispatch:
                tick = engine.tick(run_id)
                result.update({"status": tick.status, "actions": list(tick.actions), "receipts": list(tick.receipts)})
            else:
                result["actions"] = [action.to_dict() for action in engine.scheduler.preview(run_id)]
            return result
        finally:
            if owned_store:
                runtime_store.close()

    def next_actions(self, run_id: str, *, store: Store) -> list[Any]:
        return [action.to_dict() for action in CoordinatorEngine(store).scheduler.preview(run_id)]

    def permission_at_action(self, action: str, *, scopes: tuple[str, ...] = (), policy: str = "ask", authorized: bool = False, reason: str = "") -> PermissionIntent:
        return self.permissions.request(action, scopes=scopes, policy=policy, authorized=authorized, reason=reason)

    @staticmethod
    def _external_actions(intent: RunIntent) -> tuple[str, ...]:
        result: list[str] = []
        if intent.authorization_intent.git_operations:
            result.append("git")
        if intent.authorization_intent.live_external_mutation:
            result.append("live-external-mutation")
        if intent.authorization_intent.publication:
            result.append("publication")
        return tuple(result)

    @staticmethod
    def _goal_intent(goal: str, *, repository: Mapping[str, Any] | None = None, pack: str | Mapping[str, Any] | None = None, raw: Mapping[str, Any] | None = None) -> RunIntent:
        raw = raw or {}
        pack_value = pack or raw.get("pack") or "delivery"
        if isinstance(pack_value, Mapping):
            pack_ref = dict(pack_value)
            pack_config = dict(pack_ref.get("config", {}) or {})
            pack_config.update(dict(raw.get("pack_config", {}) or {}))
            pack_ref["config"] = pack_config
        else:
            pack_config = dict(raw.get("pack_config", {}) or {})
            for key in ("domains", "outcome_domains", "tasks"):
                if key in raw and key not in pack_config:
                    pack_config[key] = raw[key]
            pack_ref = {"id": str(pack_value), "version": "1.0.0", "config": pack_config}
        generated_id = "".join(char.lower() if char.isascii() and char.isalnum() else "-" for char in goal)[:32].strip("-")
        return RunIntent(
            intent_id=str(raw.get("intent_id") or f"intent-{generated_id or 'goal'}").strip("-") or "intent-goal",
            goal=goal,
            done_when=tuple(str(item) for item in raw.get("done_when", ("the requested outcome is evidenced",))),
            repository=repository or {"mode": "projectless", "roots": (), "protected_paths": ()},
            authorization_intent=raw.get("authorization_intent", {"implementation_writes": True, "git_operations": False, "destructive_operations": False, "live_external_mutation": False, "publication": False}),
            resource_envelope=raw.get("resource_envelope", {"top_level_slots": "auto", "total_subagent_slots": "auto", "subagent_slots_per_lane": "auto", "model_policy": "auto", "model": None, "reasoning_policy": "auto", "reasoning": None, "external_action_policy": "ask"}),
            pack=pack_ref,
            constraints=tuple(str(item) for item in raw.get("constraints", ())),
        )


__all__ = ["EXACT_ACTION_RELAY_CONTRACT", "GoalCompiler", "JITPermissionRouter", "PermissionIntent", "RepositoryContextInspector", "SinglePublicSkillAPI", "SkillCompilation", "TaskDecomposer"]
