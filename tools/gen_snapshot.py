"""Generate a sample snapshot JSON consistent with the v0.4 model, for the presentation page."""
import json, random
from datetime import date, timedelta

random.seed(7)
TODAY = date(2026, 10, 23)

people = [
    {"handle": "@mahmoud", "name": "Mahmoud A.", "team": "platform"},
    {"handle": "@sara", "name": "Sara H.", "team": "squad-b"},
    {"handle": "@ali", "name": "Ali K.", "team": "squad-b"},
    {"handle": "@nour", "name": "Nour S.", "team": "data"},
    {"handle": "@omar", "name": "Omar F.", "team": "data"},
    {"handle": "@lina", "name": "Lina M.", "team": "platform"},
    {"handle": "@youssef", "name": "Youssef R.", "team": "squad-a"},
    {"handle": "@dina", "name": "Dina T.", "team": "squad-a"},
]
teams = ["platform", "squad-a", "squad-b", "data"]
stages = ["idea", "discovery", "design", "build", "test", "staging", "production"]
stage_expected = {"idea": 14, "discovery": 10, "design": 7, "build": 10, "test": 5, "staging": 3, "production": 0}
priorities = ["low", "medium", "high", "critical"]
products = [
    {"key": "CUST", "name": "Customer Portal"},
    {"key": "DPLAT", "name": "Data Platform"},
]
projects = [
    {"key": "WEB", "name": "Website Refresh", "product": "CUST", "methodology": "agile", "team": "squad-b",
     "summary": "Sprint 14 is on track for the Beta release but four items have been sitting in test for over a week, all tied to the OAuth migration. Velocity is steady at ~28 points; the main risk is the identity provider deprecation in December."},
    {"key": "MOB", "name": "Mobile App v2", "product": "CUST", "methodology": "agile", "team": "squad-a",
     "summary": "Discovery is complete and build has started on the offline-sync epic. Estimates are consistently missing on new items, which is hiding the real remaining effort. No open critical risks."},
    {"key": "DATA", "name": "Ingestion Pipeline", "product": "DPLAT", "methodology": "waterfall", "team": "data",
     "summary": "Phase 2 (schema registry) is two days behind baseline because of the retry-logic rework. Production rollout gate is scheduled for 6 Nov and still needs sign-off from platform. Evidence logging on this project is the best in the workspace."},
    {"key": "GOV", "name": "Governance Rules", "product": "DPLAT", "methodology": "kanban", "team": "platform",
     "summary": "Steady flow of small rule-authoring items. WIP in build is above the limit (6 of 4). One item has been re-opened twice, which the rework habit detector has flagged."},
]
milestones = [
    {"name": "Beta release", "project": "WEB", "due": "2026-11-10"},
    {"name": "Offline sync demo", "project": "MOB", "due": "2026-11-20"},
    {"name": "Phase 2 gate", "project": "DATA", "due": "2026-11-06"},
    {"name": "Rules v1 published", "project": "GOV", "due": "2026-10-31"},
]
risks = [
    {"key": "WEB-R7", "title": "OAuth provider deprecates v1 API in Dec", "project": "WEB", "p": 4, "i": 5, "status": "mitigating"},
    {"key": "DATA-R3", "title": "Retry storm under upstream outage", "project": "DATA", "p": 3, "i": 4, "status": "monitoring"},
    {"key": "MOB-R1", "title": "App-store review delay", "project": "MOB", "p": 2, "i": 3, "status": "identified"},
    {"key": "GOV-R2", "title": "Rule engine performance at 10k rules", "project": "GOV", "p": 2, "i": 4, "status": "analysing"},
]
epics = [
    {"key": "WEB-120", "title": "Password reset & OAuth migration", "project": "WEB"},
    {"key": "WEB-130", "title": "Checkout redesign", "project": "WEB"},
    {"key": "MOB-40", "title": "Offline sync", "project": "MOB"},
    {"key": "MOB-55", "title": "Push notifications", "project": "MOB"},
    {"key": "DATA-80", "title": "Schema registry (Phase 2)", "project": "DATA"},
    {"key": "DATA-95", "title": "Backfill tooling (Phase 3)", "project": "DATA"},
    {"key": "GOV-10", "title": "Rule authoring UI", "project": "GOV"},
]
tags_pool = ["auth", "bug", "backend", "frontend", "tech-debt", "infra", "docs", "security", "incident", "ux"]

titles = {
    "WEB": ["fix the login redirect loop", "password reset email template", "rate-limit reset endpoint", "OAuth v2 token exchange",
            "migrate session store", "checkout address form", "cart totals rounding bug", "A/B test checkout CTA",
            "accessibility pass on checkout", "remove legacy auth cookie", "e2e tests for reset flow", "staging smoke tests"],
    "MOB": ["conflict resolution strategy", "local queue persistence", "sync status indicator", "background sync job",
            "push token registration", "notification deep links", "offline banner UX", "retry on network regain",
            "crash on empty queue", "telemetry for sync", "store listing update"],
    "DATA": ["retry logic rework", "schema registry API", "registry migration script", "compat checker", "backfill CLI skeleton",
             "partition pruning", "alerting on lag", "phase 2 runbook", "load test 10x volume", "platform sign-off package"],
    "GOV": ["rule editor autosave", "CEL validator in UI", "rule templates gallery", "bulk import rules", "rule versioning",
            "perf test 10k rules", "docs for rule authors", "permission matrix", "dry-run panel"],
}

items = []
n = 0
for proj in projects:
    team_people = [p for p in people if p["team"] == proj["team"]] + [people[0]]
    proj_epics = [e for e in epics if e["project"] == proj["key"]]
    for i, t in enumerate(titles[proj["key"]]):
        n += 1
        key = f"{proj['key']}-{100 + i * 3 + random.randint(0, 2)}"
        stage = random.choices(stages, weights=[1, 2, 2, 5, 3, 1, 3])[0]
        if proj["key"] == "WEB" and i < 4:
            stage = "test"
        if proj["key"] == "GOV" and i < 6:
            stage = "build"
        stage_age = random.randint(0, stage_expected[stage] * 2 + 1) if stage != "production" else 0
        if proj["key"] == "WEB" and i < 4:
            stage_age = random.randint(7, 12)
        assignee = random.choice(team_people)["handle"]
        est = random.choice([None, 2, 4, 8, 16, 24]) if not (proj["key"] == "MOB" and i % 2 == 0) else None
        planned_end = TODAY + timedelta(days=random.randint(-6, 18))
        done = stage == "production"
        actual_end = (planned_end + timedelta(days=random.randint(-2, 4))) if done else None
        hours = round(random.uniform(0, 30), 1)
        prio = random.choices(priorities, weights=[1, 4, 3, 1])[0]
        weight = {"low": 1, "medium": 1, "high": 2, "critical": 3}[prio]
        epic = random.choice(proj_epics)["key"] if proj_epics else None
        item_risks = []
        if proj["key"] == "WEB" and "auth" in t.lower() or "OAuth" in t or "reset" in t:
            item_risks = ["WEB-R7"]
        if proj["key"] == "DATA" and "retry" in t:
            item_risks = ["DATA-R3"]
        touched = TODAY - timedelta(days=random.randint(0, 20))
        items.append({
            "key": key, "title": t, "project": proj["key"], "product": proj["product"], "epic": epic,
            "type": random.choice(["task", "task", "story", "bug"]),
            "assignee": assignee, "team": next(p["team"] for p in people if p["handle"] == assignee),
            "stage": stage, "stage_age_days": stage_age,
            "state": "done" if done else ("active" if stage in ("build", "test", "staging") else "open"),
            "priority": prio, "weight": weight, "estimate_h": est, "hours_logged": hours,
            "planned_start": (planned_end - timedelta(days=random.randint(3, 12))).isoformat(),
            "planned_end": planned_end.isoformat(),
            "actual_end": actual_end.isoformat() if actual_end else None,
            "tags": random.sample(tags_pool, random.randint(1, 3)),
            "risks": item_risks, "last_touched": touched.isoformat(),
            "evidence_count": random.randint(0, 6),
        })

# violations
violations = []
for it in items:
    if it["stage"] != "production" and it["stage_age_days"] > stage_expected[it["stage"]]:
        violations.append({"policy": "stuck_in_stage", "category": "process", "severity": "warning",
                           "target": it["key"], "person": it["assignee"], "team": it["team"], "project": it["project"],
                           "detail": f"{it['stage']} for {it['stage_age_days']}d (expected ≤ {stage_expected[it['stage']]})"})
    if it["estimate_h"] is None and it["weight"] > 1 and it["state"] == "active":
        violations.append({"policy": "no_estimate_weighted", "category": "planning", "severity": "warning",
                           "target": it["key"], "person": it["assignee"], "team": it["team"], "project": it["project"],
                           "detail": f"weight {it['weight']}, no estimate"})
    if it["state"] == "active" and it["evidence_count"] == 0:
        violations.append({"policy": "evidence_gap", "category": "quality", "severity": "info",
                           "target": it["key"], "person": it["assignee"], "team": it["team"], "project": it["project"],
                           "detail": "no evidence while active"})
    if it["actual_end"] and it["actual_end"] > it["planned_end"]:
        violations.append({"policy": "overrun", "category": "time", "severity": "violation",
                           "target": it["key"], "person": it["assignee"], "team": it["team"], "project": it["project"],
                           "detail": f"finished {it['actual_end']} vs plan {it['planned_end']}"})
gov_build = [it for it in items if it["project"] == "GOV" and it["stage"] == "build"]
if len(gov_build) > 4:
    violations.append({"policy": "wip_breach", "category": "process", "severity": "violation", "target": "GOV/build",
                       "person": None, "team": "platform", "project": "GOV", "detail": f"WIP {len(gov_build)} > limit 4"})

habits = []
for p in people:
    mine = [v for v in violations if v["person"] == p["handle"]]
    late = [v for v in mine if v["policy"] == "overrun"]
    noest = [v for v in mine if v["policy"] == "no_estimate_weighted"]
    ev = [v for v in mine if v["policy"] == "evidence_gap"]
    if noest:
        habits.append({"subject": p["handle"], "key": "no_estimates", "polarity": "negative", "rate": round(len(noest) / 4, 2), "trend": random.choice(["worsening", "stable"])})
    if late:
        habits.append({"subject": p["handle"], "key": "late_finish", "polarity": "negative", "rate": round(len(late) / 3, 2), "trend": random.choice(["improving", "stable"])})
    if not ev:
        habits.append({"subject": p["handle"], "key": "evidence_consistency", "polarity": "positive", "rate": round(random.uniform(0.8, 1.0), 2), "trend": "stable"})
    if random.random() < 0.4:
        habits.append({"subject": p["handle"], "key": "log_consistency", "polarity": "positive", "rate": round(random.uniform(0.7, 1.0), 2), "trend": "improving"})
habits.append({"subject": "@lina", "key": "rework", "polarity": "negative", "rate": 0.33, "trend": "worsening"})

# activities now
now = []
for p in people[:6]:
    cand = [it for it in items if it["assignee"] == p["handle"] and it["state"] == "active"]
    if cand:
        it = random.choice(cand)
        now.append({"person": p["handle"], "item": it["key"], "title": it["title"], "project": it["project"],
                    "since_min": random.randint(5, 180), "kind": random.choice(["working", "working", "reviewing", "blocked"])})

snapshot = {
    "as_of": "2026-10-23T18:30:00+03:00", "workspace": "acme", "range": {"from": "2026-10-09", "to": "2026-10-23"},
    "weight_scheme": "default", "stages": stages, "stage_expected_days": stage_expected,
    "people": people, "teams": teams, "products": products, "projects": projects, "epics": epics,
    "milestones": milestones, "risks": risks, "items": items, "violations": violations, "habits": habits, "now": now,
}
json.dump(snapshot, open("snapshot.json", "w"), indent=1)
print(len(items), "items", len(violations), "violations", len(habits), "habits", len(now), "active")
