"""Translate legacy resource profiles into vNext ResourceEnvelope values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..domain import ResourceEnvelope
from .common import CompatibilityReport, digest


@dataclass(frozen=True)
class ResourceTranslation:
    envelope: ResourceEnvelope
    report: CompatibilityReport

    def to_dict(self) -> dict[str, Any]:
        return {"resource_envelope": self.envelope.to_dict(), "report": self.report.to_dict()}


class LegacyResourceTranslator:
    """Pure translator.  It never writes a profile or changes runtime state."""

    PROFILE_DEFAULTS = {
        "premium": {"slots": "auto", "model": None, "reasoning": None},
        "balanced": {"slots": "auto", "model": None, "reasoning": None},
        "economy": {"slots": 1, "model": None, "reasoning": "medium"},
        "speed": {"slots": "auto", "model": None, "reasoning": "high"},
        "fast": {"slots": "auto", "model": None, "reasoning": "high"},
        "ultra-fast": {"slots": "auto", "model": None, "reasoning": "high"},
        "all-luna": {"slots": "auto", "model": "gpt-5.6-luna", "reasoning": "high"},
        "mad-luna": {"slots": "auto", "model": "gpt-5.6-luna", "reasoning": "max"},
    }

    def translate(self, value: str | Mapping[str, Any] | None) -> ResourceTranslation:
        raw: dict[str, Any]
        if isinstance(value, str):
            raw = {"profile": value}
        elif isinstance(value, Mapping):
            raw = dict(value)
        else:
            raw = {}
        profile = str(raw.get("profile") or raw.get("name") or "balanced")
        defaults = dict(self.PROFILE_DEFAULTS.get(profile, {"slots": "auto", "model": None, "reasoning": None}))
        losses: list[str] = []
        warnings: list[str] = []
        if profile not in self.PROFILE_DEFAULTS:
            warnings.append(f"unknown legacy profile {profile!r}; translated with neutral auto defaults")
        policy = raw.get("resource_policy", raw)
        if not isinstance(policy, Mapping):
            policy = {}
        concurrency = policy.get("concurrency", {})
        if not isinstance(concurrency, Mapping):
            concurrency = {}
        desired = concurrency.get("desired", policy.get("desired_concurrency", defaults["slots"]))
        hard_lock = policy.get("hard_model_lock")
        if isinstance(hard_lock, Mapping):
            hard_lock = hard_lock.get("model") or hard_lock.get("family")
        explicit_model = str(hard_lock or policy.get("model") or defaults["model"]) if (hard_lock or policy.get("model") or defaults["model"]) else None
        reasoning = policy.get("reasoning") or policy.get("reasoning_level") or defaults["reasoning"]
        model_policy = "explicit" if explicit_model else "auto"
        reasoning_policy = "explicit" if reasoning else "auto"
        external_policy = str(policy.get("external_action_policy") or "ask")
        if external_policy not in {"ask", "deny", "allow"}:
            warnings.append(f"unknown external action policy {external_policy!r}; using ask")
            external_policy = "ask"
        def slot(value: Any, *, positive: bool = False) -> str | int:
            if value == "auto":
                return value
            if isinstance(value, int) and not isinstance(value, bool) and value >= (1 if positive else 0):
                return value
            return "auto"

        envelope = ResourceEnvelope(
            top_level_slots=slot(desired, positive=True),
            total_subagent_slots=slot(policy.get("total_subagent_slots", "auto")),
            subagent_slots_per_lane=slot(policy.get("subagent_slots_per_lane", "auto")),
            model_policy=model_policy,
            model=explicit_model,
            reasoning_policy=reasoning_policy,
            reasoning=str(reasoning) if reasoning else None,
            time_budget=policy.get("time_budget") or policy.get("budget_minutes"),
            token_or_credit_budget=policy.get("token_or_credit_budget"),
            external_action_policy=external_policy,
        )
        for key in ("counterpilot", "acceptance", "role_overrides", "fallback_models", "risk_level", "topology"):
            if key in raw or key in policy:
                losses.append(f"legacy {key} is not represented in ResourceEnvelope")
        model_evidence = {
            "requested": explicit_model,
            "resolved": explicit_model,
            "actual": None,
            "status": "unresolved",
            "fallback": "none",
            "receipt": None,
        }
        report = CompatibilityReport("legacy-resource-profile", digest(raw), tuple(losses), (), tuple(warnings), True, model_evidence)
        return ResourceTranslation(envelope, report)

    translate_profile = translate


__all__ = ["LegacyResourceTranslator", "ResourceTranslation"]
