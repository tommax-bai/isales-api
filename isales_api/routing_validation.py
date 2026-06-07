"""Shared validation for multi-referee labels + routing rules.

Spec: openspec/changes/engine-multi-referee-and-restructure — capability
`web-admin-ui` / `data-model`. The RoutingRule pydantic schema already enforces
action shape (transition target / goal_type / restructure source / route /
tool); these helpers add the cross-entity checks the schema can't do alone:
referee/restructure/persona rows need a non-empty unique label, and every
routing rule must reference a referee + (for route/tool actions) a persona label
or tool alias that actually exists on the campaign.

engine-tools-multidialogue-gating: persona rows are labelled too, but the
persona label namespace is INDEPENDENT of the referee/restructure namespace
(a referee "warm" and a persona "warm" may coexist — addressed by kind + label).
Route actions may target a persona label or a builtin dialogue route
(closing / recovery / restructure); tool actions must target a campaign.tools
alias.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from fastapi import HTTPException, status
from isales_common.enums import RoleKind
from isales_common.schemas.jsonb import RoutePersonaAction, RouteToolAction, RoutingRule

# Kinds that require a non-empty label. Uniqueness is namespace-scoped (see
# ``_label_namespace``) so persona labels don't collide with referee labels.
_LABELLED_KINDS = {RoleKind.REFEREE, RoleKind.RESTRUCTURE, RoleKind.PERSONA}

# Builtin dialogue routes a route action may name directly (besides persona labels).
_BUILTIN_ROUTES = {"closing", "recovery", "restructure"}


def _kind_value(kind: Any) -> str:
    return kind.value if hasattr(kind, "value") else str(kind)


def _label_of(rc: Any) -> str | None:
    return getattr(rc, "label", None)


def _label_namespace(kind: str) -> str:
    """persona labels are isolated; referee + restructure share one namespace."""
    return "persona" if kind == RoleKind.PERSONA.value else "referee_restructure"


def validate_role_labels(role_configs: Iterable[Any]) -> None:
    """referee/restructure/persona rows MUST carry a non-empty label, unique per
    campaign WITHIN their namespace.

    Accepts both api NestedWrite DTOs and ORM RoleConfig rows (both expose
    ``kind`` + ``label``).
    """
    seen: set[tuple[str, str]] = set()
    for rc in role_configs:
        kind = _kind_value(rc.kind)
        if kind not in {k.value for k in _LABELLED_KINDS}:
            continue
        label = _label_of(rc)
        if not (label and label.strip()):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="role_label_required"
            )
        key = (_label_namespace(kind), label)
        if key in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"role_label_duplicate:{label}"
            )
        seen.add(key)


def referee_labels_of(role_configs: Iterable[Any]) -> set[str]:
    return {
        _label_of(rc)
        for rc in role_configs
        if _kind_value(rc.kind) == RoleKind.REFEREE.value and _label_of(rc)
    }


def persona_labels_of(role_configs: Iterable[Any]) -> set[str]:
    return {
        _label_of(rc)
        for rc in role_configs
        if _kind_value(rc.kind) == RoleKind.PERSONA.value and _label_of(rc)
    }


def validate_routing_rules(
    routing_rules: Sequence[RoutingRule],
    referee_labels: set[str],
    *,
    primary_referee_label: str | None = None,
    persona_labels: set[str] | None = None,
    tool_aliases: set[str] | None = None,
) -> None:
    """Every rule MUST bind to an existing referee label; route/tool actions MUST
    target an existing persona label / builtin route / tool alias."""
    persona_labels = persona_labels or set()
    tool_aliases = tool_aliases or set()
    for rule in routing_rules:
        if rule.referee not in referee_labels:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"routing_rule_unknown_referee:{rule.referee}",
            )
        action = rule.action
        if isinstance(action, RoutePersonaAction) and (
            action.to not in persona_labels and action.to not in _BUILTIN_ROUTES
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"routing_rule_unknown_persona:{action.to}",
            )
        if isinstance(action, RouteToolAction) and action.tool not in tool_aliases:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"routing_rule_unknown_tool:{action.tool}",
            )
    if primary_referee_label and primary_referee_label not in referee_labels:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"primary_referee_unknown:{primary_referee_label}",
        )
