"""Products (tree), product links, objectives and key results (object model §6)."""

from __future__ import annotations

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import EventSource, ProductLinkRole, ScopeType, TargetType, ValueSource

from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
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
    s = uow.session
    key = key.upper()
    if await s.scalar(
        select(m.Product.id).where(
            m.Product.workspace_id == ws.id, m.Product.key == key, m.Product.deleted_at.is_(None)
        )
    ):
        raise ApiError(409, "product_exists", f"product {key} already exists", pointer="/key")
    if parent_product_id and not await s.scalar(
        select(m.Product.id).where(m.Product.id == parent_product_id, m.Product.workspace_id == ws.id)
    ):
        raise ApiError(404, "not_found", "parent product not found", pointer="/parent_product_id")
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
    if await _would_cycle(uow, product.id, parent_id):
        raise ApiError(400, "cycle", "a product cannot be its own ancestor", pointer="/parent_product_id")
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
        raise ApiError(409, "link_exists", "already linked", pointer="/target_id")
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
            raise ApiError(
                409, "delivers_exists", "the project already has a delivering product", pointer="/role"
            )
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
    if role == ProductLinkRole.DELIVERS and target_type == TargetType.PROJECT:
        await inherit_product_for_project(uow, ws, target_id, product, actor_id=actor_id)
    return row


async def delivering_product(session: object, project_id: str) -> m.Product | None:
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
