"""The management ops (Phase 14), one module per group. Importing the package registers every op in `REGISTRY`.

Each op is a thin façade: it translates handles/keys into ids and calls the same router function or domain service the web
console uses, so validation, events and ACL checks are shared. Handlers run inside the executor's per-command transaction; the
executor has already enforced `permission` at the declared scope, and the router functions repeat their own checks where they
have them (defence in depth, never weaker).
"""

from tt_server.api.commands.ops import (  # noqa: F401  # each import registers its group's ops
    governance,
    items,
    iterations,
    personal,
    process,
    projects,
    tags,
    teams,
)
