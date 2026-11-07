from __future__ import annotations


class CelError(ValueError):
    """A policy expression or template problem, with enough position to point at in an editor."""

    def __init__(
        self, message: str, *, field: str | None = None, line: int | None = None, column: int | None = None
    ) -> None:
        super().__init__(message)
        self.message, self.field, self.line, self.column = message, field, line, column

    def as_dict(self) -> dict[str, object]:
        return {
            k: v
            for k, v in {
                "message": self.message,
                "field": self.field,
                "line": self.line,
                "column": self.column,
            }.items()
            if v is not None
        }
