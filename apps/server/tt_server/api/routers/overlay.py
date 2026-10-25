"""Management overlay endpoints (Phase 3), assembled from the per-resource routers.

The endpoints live next to their concern: `overlay_dimensions` (dimensions, values, classifications),
`overlay_process` (processes, stages, placements), `overlay_products` (products, priorities, objectives),
`overlay_weights`, `overlay_risks` (risks, approvals), `overlay_review` (review queue) and `overlay_items`
(bulk edit, provenance, rollup, patch). Orchestration lives in `tt_server.domain.overlay` / `domain.items.patch`.

This module keeps the old import surface (`overlay.patch_item`, `overlay.move_stage`, ...) for the command layer
(`api/commands/ops.py`), which calls the endpoint functions directly.
"""

from __future__ import annotations

from fastapi import APIRouter

from tt_server.api.routers import (
    overlay_dimensions,
    overlay_items,
    overlay_process,
    overlay_products,
    overlay_review,
    overlay_risks,
    overlay_weights,
)
from tt_server.api.routers.overlay_dimensions import ClassifyIn, ValueIn, classify, create_value
from tt_server.api.routers.overlay_items import BulkEdit, ItemPatch, bulk_apply, etag_of, patch_item
from tt_server.api.routers.overlay_process import StageMove, move_stage
from tt_server.api.routers.overlay_review import BulkReviewIn, ResolveIn, bulk_review, resolve_review
from tt_server.api.routers.overlay_risks import (
    ActionIn,
    DecisionIn,
    RiskIn,
    RiskLinkIn,
    add_risk_action,
    create_risk,
    decide,
    link_risk,
)
from tt_server.api.routers.overlay_weights import WeightIn, override_weight

# registration order is the route-matching order; it follows the original single-module layout
router = APIRouter()
for _sub in (
    overlay_dimensions,
    overlay_process,
    overlay_products,
    overlay_weights,
    overlay_risks,
    overlay_review,
    overlay_items,
):
    router.include_router(_sub.router)

__all__ = [
    "ActionIn",
    "BulkEdit",
    "BulkReviewIn",
    "ClassifyIn",
    "DecisionIn",
    "ItemPatch",
    "ResolveIn",
    "RiskIn",
    "RiskLinkIn",
    "StageMove",
    "ValueIn",
    "WeightIn",
    "add_risk_action",
    "bulk_apply",
    "bulk_review",
    "classify",
    "create_risk",
    "create_value",
    "decide",
    "etag_of",
    "link_risk",
    "move_stage",
    "override_weight",
    "patch_item",
    "resolve_review",
    "router",
]
