"""`tt-llm serve` -- run the gateway (server box, cloud, or laptop sidecar with `--local-only`)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from tt_llm.app import create_app
from tt_llm.config import GatewayConfig, load_config
from tt_llm.storage import MemoryStorage, SqlStorage, Storage

cli = typer.Typer(help="Team Tracker LLM gateway", no_args_is_help=True)


def build_app(cfg: GatewayConfig):  # type: ignore[no-untyped-def]
    storage: Storage = MemoryStorage()
    if cfg.storage.url:
        sql = SqlStorage.from_url(cfg.storage.url, cfg.storage.schema_name)
        asyncio.run(sql.create())
        storage = sql
    return create_app(cfg, storage=storage)


def apply_local_only(cfg: GatewayConfig, host: str) -> GatewayConfig:
    """`--local-only`: only local models, and (bound to loopback) no gateway token needed on the laptop."""
    cfg = cfg.model_copy(update={"privacy_mode": "local_only"})
    GatewayConfig.model_validate(cfg.model_dump())  # re-run the loopback check on the model host
    if host in {"127.0.0.1", "localhost", "::1"}:
        cfg = cfg.model_copy(update={"auth": cfg.auth.model_copy(update={"allow_anonymous": True})})
    return cfg


@cli.command("serve")
def serve(
    config: Annotated[Path | None, typer.Option(help="llm_gateway.yaml")] = None,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8700,
    local_only: Annotated[
        bool, typer.Option("--local-only", help="Only local models; nothing leaves this host")
    ] = False,
    certfile: Annotated[Path | None, typer.Option(help="PEM certificate (HTTPS)")] = None,
    keyfile: Annotated[Path | None, typer.Option(help="PEM private key")] = None,
) -> None:
    cfg = load_config(config)
    if local_only:
        cfg = apply_local_only(cfg, host)
    if bool(certfile) != bool(keyfile):
        raise typer.BadParameter("--certfile and --keyfile go together")
    uvicorn.run(
        build_app(cfg), host=host, port=port,
        ssl_certfile=str(certfile) if certfile else None, ssl_keyfile=str(keyfile) if keyfile else None,
    )  # fmt: skip


if __name__ == "__main__":
    cli()
