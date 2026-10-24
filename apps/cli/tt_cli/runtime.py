"""Wiring shared by the CLI commands (kept out of tt_client: this is where the terminal lives)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer
from tt_client.api import TTApi
from tt_client.config import ClientConfig, config_dir, data_dir
from tt_client.feedback import FeedbackLog
from tt_client.outbox import Outbox
from tt_client.pack import ContextPack, PackCache
from tt_client.parser.llm import GatewayTranslator, LLMTranslator, NullTranslator
from tt_client.parser.structured import ParseContext

GHOST_EXAMPLES = [
    "fix the login redirect loop, took 2h, plan to finish tomorrow 5pm",
    "doing 1. write tests, 2. deploy | est 3h",
    "stuck on db access waiting for ops",
]


@dataclass
class Runtime:
    cfg: ClientConfig
    outbox: Outbox
    cache: PackCache
    feedback: FeedbackLog
    api: TTApi
    data: Path

    def pack(self, *, refresh: bool = True) -> ContextPack | None:
        if refresh:
            try:
                return self.cache.refresh(self.api)
            except Exception:  # noqa: S110 - a broken server must never stop capture
                pass
        return self.cache.load()[0]

    def translator(self) -> LLMTranslator:
        if self.cfg.gateway_url:
            return GatewayTranslator(
                self.cfg.gateway_url,
                mode=self.cfg.llm_mode,
                timeout=self.cfg.llm_timeout,
                verify=self.cfg.ca_bundle or True,
            )
        return NullTranslator()

    def parse_ctx(self) -> ParseContext:
        return ParseContext(
            actor=self.cfg.handle, now=now(), tz=self.cfg.time_zone, hours_per_day=self.cfg.hours_per_day
        )


def now() -> datetime:
    override = os.environ.get("TT_NOW")
    return datetime.fromisoformat(override) if override else datetime.now(UTC)


def load_runtime(*, require_login: bool = True) -> Runtime:
    cfg = ClientConfig.load()
    if require_login and not (cfg.workspace and cfg.handle):
        typer.echo("Not logged in. Try one of these once you have run `tt login`:\n")
        for ex in GHOST_EXAMPLES:
            typer.echo(f'  tt "{ex}"')
        typer.echo("\n  tt login --url https://tt.example --workspace acme --handle @you --token tt_...")
        raise typer.Exit(2)
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    return Runtime(
        cfg,
        Outbox(data),
        PackCache(data / "pack.json"),
        FeedbackLog(data / "feedback.jsonl"),
        TTApi(cfg),
        data,
    )


__all__ = ["GHOST_EXAMPLES", "Runtime", "config_dir", "load_runtime", "now"]
