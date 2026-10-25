"""The command catalogue (`GET /commands/catalogue`): every op the server accepts, with its JSON Schema.

It feeds the manual forms, the role lens and the gateway's command translator. Cached on disk with its ETag like the
ContextPack, so forms and `tt ops` work offline from the last copy.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REFRESH_SECONDS = 15 * 60


@dataclass(frozen=True)
class OpMeta:
    op: str
    group: str
    description: str
    permission: str
    preview: bool
    destructive: bool
    args_schema: dict[str, Any]
    example: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_doc(cls, d: dict[str, Any]) -> OpMeta:
        return cls(
            op=str(d["op"]),
            group=str(d.get("group") or d["op"].split(".", 1)[0]),
            description=str(d.get("description") or ""),
            permission=str(d.get("permission") or ""),
            preview=bool(d.get("preview", False)),
            destructive=bool(d.get("destructive", False)),
            args_schema=dict(d.get("args_schema") or {"type": "object"}),
            example=dict(d.get("example") or {}),
        )

    def to_doc(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "group": self.group,
            "description": self.description,
            "permission": self.permission,
            "preview": self.preview,
            "destructive": self.destructive,
            "args_schema": self.args_schema,
            "example": self.example,
        }

    @property
    def properties(self) -> dict[str, Any]:
        return dict(self.args_schema.get("properties", {}))

    @property
    def required(self) -> list[str]:
        return list(self.args_schema.get("required", []))


@dataclass(frozen=True)
class Issue:
    pointer: str
    message: str

    def __str__(self) -> str:
        return f"{self.pointer or '/'}: {self.message}"


class Catalogue:
    def __init__(self, ops: list[OpMeta], etag: str | None = None) -> None:
        self.ops = ops
        self.etag = etag
        self._by_name = {o.op: o for o in ops}

    @classmethod
    def from_doc(cls, doc: dict[str, Any], etag: str | None = None) -> Catalogue:
        return cls([OpMeta.from_doc(o) for o in doc.get("ops", [])], etag or doc.get("etag"))

    def to_doc(self) -> dict[str, Any]:
        return {"etag": self.etag, "ops": [o.to_doc() for o in self.ops]}

    def __iter__(self) -> Iterator[OpMeta]:
        return iter(self.ops)

    def __len__(self) -> int:
        return len(self.ops)

    def __contains__(self, op: object) -> bool:
        return op in self._by_name

    @property
    def names(self) -> list[str]:
        return [o.op for o in self.ops]

    def get(self, op: str) -> OpMeta | None:
        return self._by_name.get(op)

    def require(self, op: str) -> OpMeta:
        meta = self._by_name.get(op)
        if meta is None:
            raise KeyError(f"unknown op {op!r} (not in the catalogue)")
        return meta

    @property
    def groups(self) -> list[str]:
        seen: list[str] = []
        for o in self.ops:
            if o.group not in seen:
                seen.append(o.group)
        return seen

    def in_group(self, group: str) -> list[OpMeta]:
        return [o for o in self.ops if o.group == group]

    def validate(self, op: str, args: dict[str, Any]) -> list[Issue]:
        """Validate `args` (without `_meta`) against the op's schema. Unknown op -> one issue."""
        meta = self._by_name.get(op)
        if meta is None:
            return [Issue("/op", f"unknown op {op!r}")]
        clean = {k: v for k, v in args.items() if k != "_meta"}
        validator = Draft202012Validator(meta.args_schema)
        out: list[Issue] = []
        for err in sorted(validator.iter_errors(clean), key=lambda e: list(map(str, e.absolute_path))):
            pointer = "/" + "/".join(str(p) for p in err.absolute_path)
            base = pointer if pointer != "/" else ""
            if err.validator == "required" and isinstance(err.instance, dict):
                for (
                    name
                ) in err.validator_value:  # point at the missing field, so a form can mark the right input
                    if name not in err.instance:
                        out.append(Issue(f"{base}/{name}", f"'{name}' is a required property"))
                continue
            out.append(Issue(base, err.message))
        return out


class CatalogueCache:
    """Disk cache with ETag; on network failure the last copy keeps serving (forms work offline)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[Catalogue | None, str | None, float]:
        try:
            raw = json.loads(self.path.read_text())
            cat = Catalogue.from_doc(raw["catalogue"], raw.get("etag"))
        except (OSError, ValueError, KeyError):
            return None, None, 0.0
        return cat, raw.get("etag"), float(raw.get("fetched_at", 0))

    def save(self, doc: dict[str, Any], etag: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"etag": etag, "fetched_at": time.time(), "catalogue": doc}))

    def is_stale(self, now: float | None = None) -> bool:
        _, _, fetched = self.load()
        return (now or time.time()) - fetched > REFRESH_SECONDS

    def refresh(self, api: Any, *, force: bool = False) -> Catalogue | None:
        from tt_client.api import ApiError, Unreachable

        cat, etag, fetched = self.load()
        if cat is not None and not force and (time.time() - fetched) <= REFRESH_SECONDS:
            return cat
        try:
            doc, new_etag = api.catalogue(etag if cat is not None else None)
        except (Unreachable, ApiError):
            return cat
        if doc is None and cat is not None:  # 304: unchanged, bump the clock
            self.save(cat.to_doc(), etag)
            return cat
        assert doc is not None  # noqa: S101
        self.save(doc, new_etag)
        return Catalogue.from_doc(doc, new_etag)

    def import_file(self, path: Path) -> Catalogue:
        doc = json.loads(path.read_text())
        if "ops" not in doc:
            raise ValueError("not a command catalogue file")
        self.save(doc, doc.get("etag"))
        return Catalogue.from_doc(doc)
