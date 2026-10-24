"""Rich rendering of the capture card (UX §2.2)."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from tt_client.pack import ContextPack
from tt_client.preview import build_preview
from tt_schema.client import ClientMessage

console = Console(highlight=False)


def card(
    msg: ClientMessage, pack: ContextPack | None, *, queued: bool = False, fallback: bool = False
) -> Panel:
    table = Table.grid(padding=(0, 2))
    for row in build_preview(msg, pack):
        table.add_row(f"↳ {row.label}", row.value, f"[dim]{row.cue}[/dim]")
    title = f"{msg.action.value}" + ("  [yellow]queued[/yellow]" if queued else "")
    subtitle = "will be sorted by your manager" if fallback else None
    return Panel(table, title=title, subtitle=subtitle, expand=False)


def show(messages: list[ClientMessage], pack: ContextPack | None, **kw: bool) -> None:
    for m in messages:
        console.print(card(m, pack, **kw))
