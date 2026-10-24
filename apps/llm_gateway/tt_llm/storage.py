"""Run traces and budgets: in memory by default, Postgres schema `llm` when `storage.url` is set."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


@dataclass
class Step:
    n: int
    kind: str  # model | tool
    name: str
    input: Any
    output: Any
    ms: int


@dataclass
class Run:
    id: str
    task: str
    workspace: str
    actor: str
    model_alias: str
    status: str = "running"
    model: str | None = None
    cost_usd: float = 0.0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    steps: list[Step] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class Storage(Protocol):
    async def save_run(self, run: Run) -> None: ...
    async def get_run(self, run_id: str) -> Run | None: ...
    async def spent(self, workspace: str, day: date) -> float: ...
    async def add_spend(self, workspace: str, day: date, usd: float) -> None: ...


class MemoryStorage:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.budget: dict[tuple[str, date], float] = {}

    async def save_run(self, run: Run) -> None:
        self.runs[run.id] = run

    async def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    async def spent(self, workspace: str, day: date) -> float:
        return self.budget.get((workspace, day), 0.0)

    async def add_spend(self, workspace: str, day: date, usd: float) -> None:
        self.budget[(workspace, day)] = self.budget.get((workspace, day), 0.0) + usd


class SqlStorage:
    """`agent_runs`, `agent_steps`, `budgets` in a dedicated schema of the (shared) Postgres instance."""

    def __init__(self, engine: AsyncEngine, schema: str = "llm") -> None:
        self.engine, self.schema = engine, schema
        self.meta = sa.MetaData(schema=schema)
        self.runs = sa.Table(
            "agent_runs",
            self.meta,
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("task", sa.Text, nullable=False),
            sa.Column("workspace", sa.Text, nullable=False),
            sa.Column("actor", sa.Text, nullable=False),
            sa.Column("model_alias", sa.Text, nullable=False),
            sa.Column("model", sa.Text),
            sa.Column("status", sa.Text, nullable=False),
            sa.Column("cost_usd", sa.Numeric(18, 6), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True)),
            sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        )
        self.steps = sa.Table(
            "agent_steps",
            self.meta,
            sa.Column("run_id", sa.Text, sa.ForeignKey(f"{schema}.agent_runs.id"), primary_key=True),
            sa.Column("n", sa.Integer, primary_key=True),
            sa.Column("kind", sa.Text, nullable=False),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("input", JSONB),
            sa.Column("output", JSONB),
            sa.Column("ms", sa.Integer, nullable=False),
        )
        self.budgets = sa.Table(
            "budgets",
            self.meta,
            sa.Column("workspace", sa.Text, primary_key=True),
            sa.Column("day", sa.Date, primary_key=True),
            sa.Column("usd", sa.Numeric(18, 6), nullable=False, server_default="0"),
        )
        self._sf = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str, schema: str = "llm") -> SqlStorage:
        return cls(create_async_engine(url), schema)

    async def create(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"'))
            await conn.run_sync(self.meta.create_all)

    async def save_run(self, run: Run) -> None:
        row = {
            "id": run.id,
            "task": run.task,
            "workspace": run.workspace,
            "actor": run.actor,
            "model_alias": run.model_alias,
            "model": run.model,
            "status": run.status,
            "cost_usd": run.cost_usd,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "meta": run.meta,
        }
        from sqlalchemy.dialects.postgresql import insert

        async with self._sf() as s, s.begin():
            stmt = insert(self.runs).values(**row)
            await s.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=row))
            await s.execute(sa.delete(self.steps).where(self.steps.c.run_id == run.id))
            for st in run.steps:
                await s.execute(
                    sa.insert(self.steps).values(
                        run_id=run.id,
                        n=st.n,
                        kind=st.kind,
                        name=st.name,
                        input=st.input,
                        output=st.output,
                        ms=st.ms,
                    )
                )

    async def get_run(self, run_id: str) -> Run | None:
        async with self._sf() as s:
            r = (await s.execute(sa.select(self.runs).where(self.runs.c.id == run_id))).mappings().first()
            if r is None:
                return None
            steps = (
                (
                    await s.execute(
                        sa.select(self.steps).where(self.steps.c.run_id == run_id).order_by(self.steps.c.n)
                    )
                )
                .mappings()
                .all()
            )
        return Run(
            id=r["id"],
            task=r["task"],
            workspace=r["workspace"],
            actor=r["actor"],
            model_alias=r["model_alias"],
            status=r["status"],
            model=r["model"],
            cost_usd=float(r["cost_usd"]),
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            meta=r["meta"],
            steps=[Step(x["n"], x["kind"], x["name"], x["input"], x["output"], x["ms"]) for x in steps],
        )

    async def spent(self, workspace: str, day: date) -> float:
        async with self._sf() as s:
            v = await s.scalar(
                sa.select(self.budgets.c.usd).where(
                    self.budgets.c.workspace == workspace, self.budgets.c.day == day
                )
            )
        return float(v or 0)

    async def add_spend(self, workspace: str, day: date, usd: float) -> None:
        from sqlalchemy.dialects.postgresql import insert

        async with self._sf() as s, s.begin():
            stmt = insert(self.budgets).values(workspace=workspace, day=day, usd=usd)
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["workspace", "day"], set_={"usd": self.budgets.c.usd + usd}
                )
            )
