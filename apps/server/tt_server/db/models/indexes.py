"""Lookup indexes for the capture and read hot paths (review P0–2b M4); mirrored by migration 0009."""

from __future__ import annotations

import sqlalchemy as sa

from tt_server.db.models.capture import Activity, Evidence, TimeEntry
from tt_server.db.models.items import TagLink, WorkItem
from tt_server.db.models.projects import Iteration, Milestone

sa.Index("ix_activities_work_item_id", Activity.work_item_id)
sa.Index("ix_activities_person_id", Activity.person_id)
sa.Index("ix_time_entries_person_date", TimeEntry.person_id, TimeEntry.date)
sa.Index("ix_time_entries_work_item_id", TimeEntry.work_item_id)
sa.Index("ix_evidence_work_item_id", Evidence.work_item_id)
sa.Index("ix_tag_links_target", TagLink.target_type, TagLink.target_id)
sa.Index("ix_work_items_assignee_id", WorkItem.assignee_id)
sa.Index("ix_work_items_reporter_id", WorkItem.reporter_id)
sa.Index("ix_work_items_state_id", WorkItem.state_id)
sa.Index("ix_milestones_project_id", Milestone.project_id)
sa.Index("ix_iterations_project_id", Iteration.project_id)
