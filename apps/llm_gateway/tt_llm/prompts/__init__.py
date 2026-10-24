"""Versioned prompt library: `prompts/<task>/<version>.md`. The gateway is the only place prompts live."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).parent


@lru_cache
def _read(task: str, version: str) -> str:
    return (ROOT / task / f"{version}.md").read_text(encoding="utf-8").strip()


def load(task: str, version: str = "v1", **slots: str) -> str:
    """Read a prompt and fill `<<SLOT>>` markers."""
    text = _read(task, version)
    for k, v in slots.items():
        text = text.replace(f"<<{k.upper()}>>", v)
    return text


def versions(task: str) -> list[str]:
    return sorted(p.stem for p in (ROOT / task).glob("*.md"))
