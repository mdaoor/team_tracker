"""Products (tree), product links, objectives and key results (object model §6)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, ProductLinkRole, ScopeType, TargetType, ValueSource

from tt_server.acl.context import AclContext
from tt_server.acl.grants import on_team_linked_to_product
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, Invalid, NotFound
from tt_server.domain.overlay.classify import classify
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value


async def _would_cycle(uow: UnitOfWork, product_id: str, parent_id: str | None) -> bool:
    seen = {product_id}
    while parent_id:
        if parent_id in seen:
            return True
        seen.add(parent_id)
        parent_id = await uow.session.scalar(
            select(m.Product.parent_product_id).where(m.Product.id == parent_id)
        )
    return False


async def create_product(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    key: str,
    name: str,
    owner_id: str,
    actor_id: str,
    parent_product_id: str | None = None,
    description: str | None = None,
    default_process_id: str | None = None,
) -> m.Product:
    """Create a product (optionally under a parent) with its dimension value and audit event."""
    s = uow.session
    key = key.upper()
    if await s.scalar(
        select(m.Product.id).where(
            m.Product.workspace_id == ws.id, m.Product.key == key, m.Product.deleted_at.is_(None)
        )
    ):
        raise Conflict("product_exists", f"product {key} already exists", pointer="/key")
    if parent_product_id and not await s.scalar(
        select(m.Product.id).where(m.Product.id == parent_product_id, m.Product.workspace_id == ws.id)
    ):
        raise NotFound("not_found", "parent product not found", pointer="/parent_product_id")
    product = m.Product(
        id=new_id(),
        workspace_id=ws.id,
        key=key,
        name=name,
        owner_id=owner_id,
        parent_product_id=parent_product_id,
        description=description,
        default_process_id=default_process_id,
    )
    s.add(product)
    await s.flush()
    dims = await ensure_builtin_dimensions(s, ws.id)
    await ensure_value(
        s,
        dims["product"],
        backing_type="product",
        backing_id=product.id,
        key=key,
        name=name,
        parent_backing_id=parent_product_id,
    )
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PRODUCT,
        entity_id=product.id,
        event_type="product.created",
        payload={"after": {"key": key, "name": name}},
    )
    return product


async def reparent(
    uow: UnitOfWork, ws: m.Workspace, product: m.Product, parent_id: str | None, *, actor_id: str
) -> None:
    """Move a product under another one (or to the top), refusing cycles and refreshing inherited classifications."""
    if await _would_cycle(uow, product.id, parent_id):
        raise Invalid("cycle", "a product cannot be its own ancestor", pointer="/parent_product_id")
    product.parent_product_id = parent_id
    dims = await ensure_builtin_dimensions(uow.session, ws.id)
    await ensure_value(
        uow.session,
        dims["product"],
        backing_type="product",
        backing_id=product.id,
        key=product.key,
        name=product.name,
        parent_backing_id=parent_id,
    )
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PRODUCT,
        entity_id=product.id,
        event_type="product.updated",
        payload={"fields_changed": ["parent_product_id"]},
    )


async def link(
    uow: UnitOfWork,
    ws: m.Workspace,
    product: m.Product,
    *,
    target_type: TargetType,
    target_id: str,
    role: ProductLinkRole,
    actor_id: str,
    weight: float | None = None,
) -> m.ProductLink:
    """A project may link to many products but to exactly one with role=delivers (service rule)."""
    s = uow.session
    existing = await s.scalar(
        select(m.ProductLink).where(
            m.ProductLink.product_id == product.id,
            m.ProductLink.target_type == target_type,
            m.ProductLink.target_id == target_id,
            m.ProductLink.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise Conflict("link_exists", "already linked", pointer="/target_id")
    if role == ProductLinkRole.DELIVERS and target_type == TargetType.PROJECT:
        other = await s.scalar(
            select(m.ProductLink).where(
                m.ProductLink.target_type == TargetType.PROJECT,
                m.ProductLink.target_id == target_id,
                m.ProductLink.role == ProductLinkRole.DELIVERS,
                m.ProductLink.deleted_at.is_(None),
            )
        )
        if other is not None:
            raise Conflict("delivers_exists", "the project already has a delivering product", pointer="/role")
    row = m.ProductLink(
        id=new_id(),
        product_id=product.id,
        target_type=target_type,
        target_id=target_id,
        role=role,
        weight=weight,
    )
    s.add(row)
    await s.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PRODUCT,
        entity_id=product.id,
        event_type="product_link.added",
        payload={"after": {"target_type": target_type.value, "target_id": target_id, "role": role.value}},
    )
    if target_type == TargetType.TEAM:
        await on_team_linked_to_product(uow, ws, product, target_id)
    if role == ProductLinkRole.DELIVERS and target_type == TargetType.PROJECT:
        await inherit_product_for_project(uow, ws, target_id, product, actor_id=actor_id)
    return row


async def unlink(
    uow: UnitOfWork,
    ws: m.Workspace,
    product: m.Product,
    *,
    target_type: TargetType,
    target_id: str,
    actor_id: str,
) -> m.ProductLink:
    """Soft-delete a live link. Removing a project's delivering link also drops the product values its items inherited
    from it (manual classifications stay), so the project can be linked under another product."""
    s = uow.session
    row = await s.scalar(
        select(m.ProductLink).where(
            m.ProductLink.product_id == product.id,
            m.ProductLink.target_type == target_type,
            m.ProductLink.target_id == target_id,
            m.ProductLink.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFound("not_found", "link not found")
    row.deleted_at = utcnow()
    await s.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PRODUCT,
        entity_id=product.id,
        event_type="product_link.removed",
        payload={
            "before": {"target_type": target_type.value, "target_id": target_id, "role": row.role.value}
        },
    )
    if row.role == ProductLinkRole.DELIVERS and target_type == TargetType.PROJECT:
        for c in (
            await s.scalars(
                select(m.Classification).where(
                    m.Classification.inherited_from_type == TargetType.PRODUCT,
                    m.Classification.inherited_from_id == product.id,
                    m.Classification.source == ValueSource.INHERITED,
                    m.Classification.deleted_at.is_(None),
                    (
                        (m.Classification.target_type == TargetType.PROJECT)
                        & (m.Classification.target_id == target_id)
                    )
                    | (
                        (m.Classification.target_type == TargetType.WORK_ITEM)
                        & m.Classification.target_id.in_(
                            select(m.WorkItem.id).where(m.WorkItem.project_id == target_id)
                        )
                    ),
                )
            )
        ).all():
            c.deleted_at = utcnow()
    return row


async def delivering_product(session: object, project_id: str) -> m.Product | None:
    """The product that delivers a project, if any."""
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)  # noqa: S101
    return await session.scalar(
        select(m.Product)
        .join(m.ProductLink, m.ProductLink.product_id == m.Product.id)
        .where(
            m.ProductLink.target_type == TargetType.PROJECT,
            m.ProductLink.target_id == project_id,
            m.ProductLink.role == ProductLinkRole.DELIVERS,
            m.ProductLink.deleted_at.is_(None),
            m.Product.deleted_at.is_(None),
        )
    )


async def inherit_product_for_project(
    uow: UnitOfWork, ws: m.Workspace, project_id: str, product: m.Product, *, actor_id: str
) -> int:
    """Items inherit the `product` classification from their project's delivering product (unless manual)."""
    s = uow.session
    dims = await ensure_builtin_dimensions(s, ws.id)
    value = await ensure_value(
        s,
        dims["product"],
        backing_type="product",
        backing_id=product.id,
        key=product.key,
        name=product.name,
        parent_backing_id=product.parent_product_id,
    )
    n = 0
    targets: list[tuple[TargetType, str]] = [(TargetType.PROJECT, project_id)] + [
        (TargetType.WORK_ITEM, i)
        for i in (
            await s.scalars(
                select(m.WorkItem.id).where(
                    m.WorkItem.project_id == project_id, m.WorkItem.deleted_at.is_(None)
                )
            )
        ).all()
    ]
    for tt, tid in targets:
        if await classify(
            uow,
            ws,
            target_type=tt,
            target_id=tid,
            dimension=dims["product"],
            value=value,
            source=ValueSource.INHERITED,
            set_by=actor_id,
            inherited_from=(TargetType.PRODUCT, product.id),
            emit_event=False,
        ):
            n += 1
    return n


async def create_objective(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    scope_type: ScopeType,
    scope_id: str,
    title: str,
    period: str,
    owner_id: str,
    actor_id: str,
) -> m.Objective:
    """Create an objective with its backing dimension value and audit event."""
    s = uow.session
    obj = m.Objective(
        id=new_id(),
        workspace_id=ws.id,
        scope_type=scope_type,
        scope_id=scope_id,
        title=title,
        period=period,
        owner_id=owner_id,
    )
    s.add(obj)
    await s.flush()
    dims = await ensure_builtin_dimensions(s, ws.id)
    await ensure_value(
        s, dims["objective"], backing_type="objective", backing_id=obj.id, key=obj.id, name=title
    )
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.OBJECTIVE,
        entity_id=obj.id,
        event_type="objective.created",
        payload={"after": {"title": title, "period": period}},
        source=EventSource.OVERLAY,
    )
    return obj


async def get_product(uow: UnitOfWork, ws_id: str, ref: str) -> m.Product:
    """A visible product by id or (upper-cased) key; invisible or absent is a 404."""
    stmt = select(m.Product).where(
        m.Product.workspace_id == ws_id,
        m.Product.deleted_at.is_(None),
        (m.Product.id == ref) | (m.Product.key == ref.upper()),
    )
    acl = uow.context.get("acl")
    p: m.Product | None
    p = await uow.session.scalar(visible(stmt, acl, m.Product) if acl is not None else stmt)
    if p is None:
        raise NotFound("not_found", "product not found")
    return p


async def list_products(uow: UnitOfWork, ws_id: str, acl: AclContext) -> list[m.Product]:
    """Live products the caller can see, by key."""
    return list(
        (
            await uow.session.scalars(
                visible(
                    select(m.Product).where(m.Product.workspace_id == ws_id, m.Product.deleted_at.is_(None)),
                    acl,
                    m.Product,
                ).order_by(m.Product.key)
            )
        ).all()
    )


async def patch_product(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Product, data: dict[str, Any]
) -> m.Product:
    """Edit a product; a parent change goes through `reparent` (cycle check, inheritance refresh)."""
    if "parent_product_id" in data:
        await reparent(uow, ws, p, data.pop("parent_product_id"), actor_id=actor_id)
    for k, v in data.items():
        setattr(p, k, v)
    return p


async def list_links(uow: UnitOfWork, p: m.Product) -> list[m.ProductLink]:
    """Live links of a product."""
    return list(
        (
            await uow.session.scalars(
                select(m.ProductLink).where(
                    m.ProductLink.product_id == p.id, m.ProductLink.deleted_at.is_(None)
                )
            )
        ).all()
    )
