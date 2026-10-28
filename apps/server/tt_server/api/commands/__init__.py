"""Management commands from the client (Phase 14): registry, ops, executor and routes."""

from tt_server.api.commands import ops as ops  # noqa: F401  # registers every op
from tt_server.api.commands.registry import REGISTRY, catalogue

__all__ = ["REGISTRY", "catalogue"]
