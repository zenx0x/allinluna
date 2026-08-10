from __future__ import annotations

import pytest

from allinluna_runtime.packs.public_skill import DEFAULT_RESOURCE_ENVELOPE, SinglePublicSkillAPI
from allinluna_runtime.resource import ResourceBroker
from allinluna_runtime.resource_policy import (
    CapabilityProfile,
    ResourcePolicyError,
    ResourcePolicyResolver,
)


def test_layered_capability_routes_merge_without_overwriting_higher_priority_values() -> None:
    resolver = ResourcePolicyResolver(
        {
            "capability_routes": {
                "planning.semantic": {"model": "route-planning-a", "reasoning": "high"},
            },
            "resource_routes": {
                "planning.semantic": {"model": "route-planning-b", "reasoning": "max"},
                "work.implementation": {"model": "route-implementation", "reasoning": "medium"},
            },
        }
    )

    assert resolver.resolve(operation="planning").resolved["model"] == "route-planning-a"
    assert resolver.resolve(operation="spawn-subagent").resolved["model"] == "route-implementation"


def test_automatic_host_fingerprint_changes_when_routes_change() -> None:
    base = {
        "host_id": "neutral-host",
        "host_version": "1",
        "plugin_version": "1",
        "tools": ["read"],
        "capability_routes": {
            "lane.synthesis": {"model": "route-a", "reasoning": "medium"},
        },
    }
    changed = {
        **base,
        "capability_routes": {
            "lane.synthesis": {"model": "route-b", "reasoning": "medium"},
        },
    }

    assert CapabilityProfile.from_value(base).host_fingerprint != CapabilityProfile.from_value(changed).host_fingerprint


def test_invalid_policy_modes_fail_closed() -> None:
    resolver = ResourcePolicyResolver({"model_policy": "provider-default"})

    with pytest.raises(ResourcePolicyError, match="unknown resource policy mode"):
        resolver.resolve(operation="planning")


def test_hard_lock_rejects_reroute_before_actual_telemetry_exists() -> None:
    resolver = ResourcePolicyResolver({"route_assurance": "hard_lock"})
    assurance = resolver.assess(
        {
            "requested": {"model": "requested-route", "reasoning": "medium"},
            "resolved": {
                "model": "different-route",
                "reasoning": "medium",
                "route_assurance": "hard_lock",
            },
            "actual": None,
            "actual_state": "unresolved",
        }
    )

    assert assurance.blocking is True
    assert assurance.state == "blocked"


def test_broker_accepts_semantic_overrides_without_a_vendor_default() -> None:
    broker = ResourceBroker(
        {
            "capability_routes": {
                "planning.semantic": {"model": "planning-route", "reasoning": "high"},
                "work.implementation": {"model": "implementation-route", "reasoning": "medium"},
            }
        }
    )

    allocation = broker.allocate_top_level_slots(
        [{"id": "semantic-task", "capability_class": "planning.semantic"}]
    )[0]

    assert allocation.receipt.resolved["capability_class"] == "planning.semantic"
    assert allocation.model == "planning-route"
    assert "model" not in DEFAULT_RESOURCE_ENVELOPE


def test_public_skill_compiles_a_neutral_resource_envelope() -> None:
    compilation = SinglePublicSkillAPI().compile({"goal": "compile a neutral route"})
    envelope = compilation.intent.resource_envelope.to_dict()

    assert envelope["model_policy"] == "auto"
    assert envelope["reasoning_policy"] == "auto"
    assert envelope["capability_class"] == "lane.synthesis"
    assert envelope["route_assurance"] == "observe_if_exposed"
    assert envelope["model"] is None
    assert envelope["reasoning"] is None
