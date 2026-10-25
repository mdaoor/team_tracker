# Runbook: backup and restore

* **Baseline:** `deploy/backup/backup.sh` nightly (custom-format dump + checksum, 14 days). **PITR:** archive WAL (pgBackRest, or the managed service's PITR).
* **Attachments** live in `TT_ATTACHMENT_DIR` (local disk) - include that volume in the backup; the database holds only opaque keys.
* **Drill (quarterly, target < 30 min):** `ADMIN_URL=... BACKUP=... deploy/backup/restore_drill.sh`. It verifies the checksum, restores into a scratch database, prints row counts and
  the migration head, runs `tt-admin verify-projection`, and drops the scratch database.
* **Real restore:** stop `tt-server`/`tt-worker`; create the target database; `pg_restore --no-owner --jobs 4 --dbname <url> <dump>` (or PITR to the chosen time);
  `tt-admin migrate` (a restore of an older dump is brought to head); start the worker, then the server; check `/readyz`, `tt-admin verify-projection <ws>`.
* After a restore the job queue may contain jobs for events that no longer exist; they fail harmlessly and can be cleared (`delete from procrastinate_jobs where status='failed'`).
