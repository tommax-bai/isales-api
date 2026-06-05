"""Shared validation for multi-referee labels + routing rules.

Spec: openspec/changes/engine-multi-referee-and-restructure — capability
`web-admin-ui` / `data-model`. The RoutingRule pydantic schema already enforces
action shape (transition target / goal_type / restructure source); these helpers
add the cross-entity checks the schema can't do alone: referee/restructure rows
need a non-empty unique label, and every routing rule (+ primary_referee_label)
must reference a referee label that actually exists on the campaign.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from fastapi import HTTPException, status
from isales_common.enums import RoleKind
from isales_common.schemas.jsonb import RoutingRule

_LABELLED_KINDS = {RoleKind.REFEREE, RoleKind.RESTRUCTURE}


def _kind_value(kind: Any) -> str:
    return kind.value if hasattr(kind, "value") else str(kind)


def _label_of(rc: Any) -> str | None:
    return getattr(rc, "label", None)


def validate_role_labels(role_configs: Iterable[Any]) -> None:
    """referee/restructure rows MUST carry a non-empty label, unique per campaign.

    Accepts both api NestedWrite DTOs and ORM RoleConfig rows (both expose
    ``kind`` + ``label``).
    """
    seen: set[str] = set()
    for rc in role_configs:
        kind = _kind_value(rc.kind)
        if kind not in {k.value for k in _LABELLED_KINDS}:
            continue
        label = _label_of(rc)
        if not (label and label.strip()):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="role_label_required"
            )
        if label in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"role_label_duplicate:{label}"
            )
        seen.add(label)


def referee_labels_of(role_configs: Iterable[Any]) -> set[str]:
    return {
        _label_of(rc)
        for rc in role_configs
        if _kind_value(rc.kind) == RoleKind.REFEREE.value and _label_of(rc)
    }


def validate_routing_rules(
    routing_rules: Sequence[RoutingRule],
    referee_labels: set[str],
    *,
    primary_referee_label: str | None = None,
) -> None:
    """Every rule (+ the primary referee) MUST bind to an existing referee label."""
    for rule in routing_rules:
        if rule.referee not in referee_labels:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"routing_rule_unknown_referee:{rule.referee}",
            )
    if primary_referee_label and primary_referee_label not in referee_labels:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"primary_referee_unknown:{primary_referee_label}",
        )
