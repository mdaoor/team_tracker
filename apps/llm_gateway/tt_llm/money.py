"""Money in the gateway is `Decimal` end to end (provider price -> completion -> run record -> `Numeric(18, 6)` column).

Floats from providers or tests are converted through their shortest repr, never summed as binary floats. On the wire (the JSON
response envelope) amounts stay JSON numbers so the published contract does not change."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema

_QUANT = Decimal("0.000001")  # the column scale


def dec(v: Decimal | float | int | str | None) -> Decimal:
    """`v` as a Decimal (None is zero)."""
    if v is None:
        return Decimal(0)
    return v if isinstance(v, Decimal) else Decimal(str(v))


def to_scale(v: Decimal | float | int | str | None) -> Decimal:
    """`v` rounded to the six decimals the run table stores."""
    return dec(v).quantize(_QUANT)


Money = Annotated[
    Decimal,
    PlainSerializer(lambda d: float(d), return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
