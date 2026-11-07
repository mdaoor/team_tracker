"""How a malformed `ClientBatch` is reported: the first violation, as message, JSON pointer and details."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

MAX_COMMANDS = 50


def describe_violation(exc: ValidationError) -> tuple[str, str, dict[str, Any]]:
    """(message, JSON pointer, details) for the first error of a failed batch validation.

    Too many `commands[]` get a message that points at `POST /commands`; an error inside `messages[]` carries its index."""
    first = exc.errors()[0]
    loc = [str(p) for p in first["loc"]]
    too_many = first["loc"][:1] == ("commands",) and first["type"] == "too_long"
    message = (
        f"a batch carries at most {MAX_COMMANDS} commands; send management commands with POST /commands in chunks of {MAX_COMMANDS}"
        if too_many
        else first["msg"]
    )
    index = first["loc"][1] if len(first["loc"]) > 1 and first["loc"][0] == "messages" else None
    return message, "/" + "/".join(loc), {"message_index": index}
