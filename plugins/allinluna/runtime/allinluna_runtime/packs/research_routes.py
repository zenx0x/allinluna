"""Route-neutral Research Routes -> All in Luna compiler bridge."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from ..domain import Run, RunIntent, Task
from .base import CompiledRunGraph, PackManifest, contract_for, task_for


class ResearchRoutesBridge:
    id = "research-routes-bridge"
    version = "1.0.0"
    manifest = PackManifest(
        pack_id=id,
        version=version,
        display_name="Research Routes Bridge",
        entrypoints={
            "compile_goal": "allinluna_runtime.packs.research_routes:ResearchRoutesBridge",
            "enrich_context": "allinluna_runtime.packs.research_routes:ResearchRoutesBridge",
            "verifiers": "allinluna_runtime.packs.research_routes:ResearchRoutesBridge",
            "compose_result": "allinluna_runtime.packs.research_routes:ResearchRoutesBridge",
        },
        imports=("https://github.com/zenx0x/research-routes/contracts/route-packet/v1",),
        exports=("contract://research-routes-bridge/evidence-boundary@1",),
        capabilities=({"id": "research-routes", "kind": "plugin", "required": True},),
        external_action_policy="deny",
    )

    def __init__(self, packet: Mapping[str, Any] | None = None) -> None:
        self.packet = dict(packet or {})

    def to_intent(self, packet: Mapping[str, Any], *, repository: Mapping[str, Any] | None = None, done_when: tuple[str, ...] = ("the bounded evidence package is delivered",)) -> RunIntent:
        goal = str(packet.get("goal") or packet.get("question") or packet.get("title") or "Translate a research route into a bounded execution input")
        claims = tuple(packet.get("claims", ()))
        evidence = tuple(packet.get("evidence", ()))
        unknowns = tuple(packet.get("unknowns", packet.get("open_questions", ())))
        constraints = (
            "route-neutral: preserve competing routes and failure regimes",
            "claims/evidence remain distinct from HumanDecision and experiment authorization",
            "do not promote evidence into implementation or canonical state",
        ) + tuple(str(item) for item in packet.get("constraints", ()))
        allowed_prefixes = ("artifact://", "file://", "connector://", "git://", "receipt://", "context://", "snapshot://")
        source_refs = tuple(str(item) for item in packet.get("source_refs", ()) if isinstance(item, str) and item.startswith(allowed_prefixes))
        return RunIntent(
            intent_id=str(packet.get("packet_id") or packet.get("id") or "research-route"),
            goal=goal,
            done_when=done_when,
            repository=repository or {"mode": "projectless", "roots": (), "protected_paths": ()},
            authorization_intent={"implementation_writes": False, "git_operations": False, "destructive_operations": False, "live_external_mutation": False, "publication": False},
            resource_envelope={"top_level_slots": "auto", "total_subagent_slots": "auto", "subagent_slots_per_lane": "auto", "model_policy": "auto", "model": None, "reasoning_policy": "auto", "reasoning": None, "external_action_policy": "deny"},
            pack={"id": self.id, "version": self.version, "config": {"claims": list(claims), "evidence": list(evidence), "unknowns": list(unknowns), "human_decisions": list(packet.get("human_decisions", ())), "experiment_authorization": packet.get("experiment_authorization"), "canonical_state": False}},
            constraints=constraints,
            source_refs=source_refs,
        )

    def compile_goal(self, run_intent: RunIntent) -> CompiledRunGraph:
        run_id = f"run-{run_intent.intent_id}"
        contract = contract_for(
            contract_id=f"contract-{run_intent.intent_id}-evidence-boundary",
            outcome="Compile a route-neutral evidence boundary for downstream human choice",
            done_when=run_intent.done_when,
            ownership=(),
            exports=({"name": "evidence-boundary", "kind": "decision", "version": 1, "description": "Claims, Evidence, unknowns, contradictions, and failure regimes; no implementation authorization"},),
        )
        task = task_for(run_id=run_id, task_id="research-evidence-boundary", outcome=contract.outcome, contract=contract)
        return CompiledRunGraph(run_id=run_id, tasks=(task,), contracts=(contract,), metadata={"pack": self.id, "route_neutral": True, "forbidden_scope": list(run_intent.repository.protected_paths), "preserve": ["claims", "evidence", "unknowns", "failure_regimes", "human_decisions", "experiment_authorization"], "forbidden_promotion": ["implementation", "canonical-state"]})

    def enrich_context(self, scope: Any, bundle: Any) -> Any:
        additions = {"scope": str(scope), "pack": self.id, "excluded": ["raw_tool_logs", "unrelated_lanes"], "route_neutral": True}
        if hasattr(bundle, "to_dict"):
            data = bundle.to_dict()
            data.setdefault("known_facts", []).append(additions)
            try:
                return replace(bundle, known_facts=tuple(data["known_facts"]))
            except TypeError:
                return data
        result = dict(bundle or {})
        result.setdefault("known_facts", []).append(additions)
        return result

    def verifiers(self, task: Task) -> list[Any]:
        return [lambda evidence: isinstance(evidence, Mapping) and evidence.get("route_neutral") is True]

    def compose_result(self, run: Run) -> Mapping[str, Any]:
        return {"kind": "result", "run_id": str(run.id), "status": run.status.value, "pack": self.id, "route_neutral": True}


__all__ = ["ResearchRoutesBridge"]
