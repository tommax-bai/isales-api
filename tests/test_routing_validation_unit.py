"""Pure-function unit tests for routing validation (no DB).

engine-tools-multidialogue-gating §3: persona label namespace isolation +
route→persona / tool→alias 422s. The HTTP round-trip lives in
test_multi_referee_routing.py (DB-backed, skips without Postgres).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from isales_common.enums import RoleKind
from isales_common.schemas.jsonb import RoutingRule

from isales_api.routing_validation import (
    persona_labels_of,
    validate_role_labels,
    validate_routing_rules,
)


def _rule(referee: str, match, action: dict) -> RoutingRule:
    return RoutingRule.model_validate({"referee": referee, "match": match, "action": action})


def _rc(kind: RoleKind, label: str | None):
    return SimpleNamespace(kind=kind, label=label)


# --- route → persona ------------------------------------------------------

def test_route_to_known_persona_ok():
    validate_routing_rules(
        [_rule("j", ["x"], {"type": "route", "to": "warm"})],
        {"j"},
        persona_labels={"warm"},
        tool_aliases=set(),
    )


def test_route_to_builtin_ok():
    for builtin in ("closing", "recovery", "restructure"):
        validate_routing_rules(
            [_rule("j", ["x"], {"type": "route", "to": builtin})],
            {"j"},
            persona_labels=set(),
            tool_aliases=set(),
        )


def test_route_to_unknown_persona_422():
    with pytest.raises(HTTPException) as ei:
        validate_routing_rules(
            [_rule("j", ["x"], {"type": "route", "to": "ghost"})],
            {"j"},
            persona_labels={"warm"},
            tool_aliases=set(),
        )
    assert ei.value.status_code == 422
    assert ei.value.detail == "routing_rule_unknown_persona:ghost"


# --- tool → alias ---------------------------------------------------------

def test_tool_known_alias_ok():
    validate_routing_rules(
        [_rule("j", ["x"], {"type": "tool", "tool": "bye"})],
        {"j"},
        tool_aliases={"bye"},
    )


def test_tool_unknown_alias_422():
    with pytest.raises(HTTPException) as ei:
        validate_routing_rules(
            [_rule("j", ["x"], {"type": "tool", "tool": "nope"})],
            {"j"},
            tool_aliases={"bye"},
        )
    assert ei.value.status_code == 422
    assert ei.value.detail == "routing_rule_unknown_tool:nope"


def test_legacy_transition_still_ok():
    # no persona/tool target → only the referee check applies
    validate_routing_rules(
        [_rule("j", ["x"], {"type": "transition", "to": "transfer"})],
        {"j"},
    )


# --- label namespace isolation -------------------------------------------

def test_persona_and_referee_same_label_coexist():
    # referee "warm" + persona "warm" must NOT collide (separate namespaces).
    validate_role_labels([
        _rc(RoleKind.REFEREE, "warm"),
        _rc(RoleKind.PERSONA, "warm"),
    ])


def test_duplicate_persona_label_rejected():
    with pytest.raises(HTTPException) as ei:
        validate_role_labels([
            _rc(RoleKind.PERSONA, "warm"),
            _rc(RoleKind.PERSONA, "warm"),
        ])
    assert ei.value.status_code == 422
    assert ei.value.detail.startswith("role_label_duplicate")


def test_persona_without_label_rejected():
    with pytest.raises(HTTPException) as ei:
        validate_role_labels([_rc(RoleKind.PERSONA, None)])
    assert ei.value.detail == "role_label_required"


def test_persona_labels_of_filters_kind():
    rcs = [
        _rc(RoleKind.PERSONA, "warm"),
        _rc(RoleKind.PERSONA, "calm"),
        _rc(RoleKind.REFEREE, "judge"),
        _rc(RoleKind.MAIN, None),
    ]
    assert persona_labels_of(rcs) == {"warm", "calm"}
