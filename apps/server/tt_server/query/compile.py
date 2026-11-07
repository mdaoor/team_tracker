"""Filter AST -> SQLAlchemy predicate over `work_items` (Phase 6 §6.1).

Everything is parameterised; names from the document are looked up (dimension keys, handles, team slugs) and unknown ones raise
`FilterError` with the JSON pointer of the offending value. The caller joins `Project` and `WorkflowState` and applies
`acl.visible()` last, so a shared view only ever shows what the opener may see.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import and_, func, not_, or_, true
from tt_filter import And, Node, Not, Or
from tt_schema.enums import (
    ProjectKind,
)

from tt_server.db import models as m
from tt_server.query.clauses import _CLAUSES
from tt_server.query.clauses import risk_query as risk_query
from tt_server.query.context import OPEN_VIOLATION as OPEN_VIOLATION
from tt_server.query.context import PRIORITY_ORDER as PRIORITY_ORDER
from tt_server.query.context import SEVERITY_ORDER as SEVERITY_ORDER
from tt_server.query.context import QueryCtx as QueryCtx
from tt_server.query.context import priority_rank as priority_rank
from tt_server.query.context import wants_inbox
from tt_server.query.context import weight_expr as weight_expr


async def compile_node(node: Node, qc: QueryCtx) -> Any:
    if isinstance(node, And):
        return and_(true(), *[await compile_node(n, qc) for n in node.items]) if node.items else true()
    if isinstance(node, Or):
        return or_(*[await compile_node(n, qc) for n in node.items])
    if isinstance(node, Not):
        return not_(await compile_node(node.item, qc))
    # SQL's three-valued logic would make NOT(unknown) unknown: treat "unknown" as "no match" so `not` is a true complement
    return func.coalesce(await _CLAUSES[node.kind](node, qc), sa.false())


async def compile_filter(node: Node, qc: QueryCtx) -> Any:
    pred = await compile_node(node, qc)
    if not wants_inbox(node):
        pred = and_(pred, m.Project.kind != ProjectKind.INBOX)
    return pred
