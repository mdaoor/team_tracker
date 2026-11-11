"""All tables of object model §3–§10.

Foreign keys that would make the DDL circular are left to the application:
`projects.default_item_type_id` (→ work_item_types) and `work_item_types.project_id` (→ projects).
Polymorphic references (target_*, entity_*, scope_*, subject_*, object_*) carry no FKs.
Keep this package free of business logic; the only place that may INSERT into `events` is `EventWriter`.
"""

from tt_server.db import conventions as _conventions  # noqa: E402
from tt_server.db.base import Base as _Base  # noqa: E402
from tt_server.db.models.acl import *  # noqa: F403
from tt_server.db.models.approvals import *  # noqa: F403
from tt_server.db.models.capture import *  # noqa: F403
from tt_server.db.models.dimensions import *  # noqa: F403
from tt_server.db.models.governance import *  # noqa: F403
from tt_server.db.models.governance_runtime import *  # noqa: F403
from tt_server.db.models.indexes import *  # noqa: F403
from tt_server.db.models.items import *  # noqa: F403
from tt_server.db.models.jira import *  # noqa: F403
from tt_server.db.models.presentations import *  # noqa: F403
from tt_server.db.models.process import *  # noqa: F403
from tt_server.db.models.products import *  # noqa: F403
from tt_server.db.models.projects import *  # noqa: F403
from tt_server.db.models.risks import *  # noqa: F403
from tt_server.db.models.system import *  # noqa: F403
from tt_server.db.models.tenancy import *  # noqa: F403
from tt_server.db.models.views import *  # noqa: F403

_conventions.apply(_Base.metadata)
