# 7. Upgrade and rollback

Every upgrade: **back up** the database → install the new code **next to** the running one → **migrate** (migrations are
additive, so the old code still runs on the new schema) → **switch** and restart → **health check**. If the new version
does not answer `/readyz` within 60 s, the previous one is switched back and restarted automatically, and the command
exits with `TT-E301`.

## Online (git checkout)
```bash
sudo scripts/ttctl prod upgrade v1.1.0
```
Without a ref it pulls the current branch. It records the previous commit; `sudo scripts/ttctl prod rollback` checks it
out again and restarts.

## From a bundle
Bundle installs keep releases side by side: `/opt/team-tracker/releases/<version>/` and the symlinks `current` and
`previous`. On the connected machine build the new bundle for the same target, copy it over, then on the host:
```bash
sudo /opt/team-tracker/current/scripts/ttctl prod upgrade --from-bundle /media/usb/tt-bundle-1.1.0-rhel8-x86_64.tar.zst
```
It verifies the bundle, installs the release into `releases/1.1.0`, backs up the database, migrates with the new code,
points `current` at it (`previous` at the old one), restarts the units and checks health.
```bash
scripts/ttctl prod releases
```
```text
1.0.0  <- previous
1.1.0  <- current
```

## Rollback
```bash
sudo scripts/ttctl prod rollback
```
Bundle installs swap `current` and `previous` and restart; git installs check out the recorded commit. The database is
not rolled back (the old code works with the newer schema). To also return the data to the state before the upgrade,
restore the backup the upgrade made (`/var/backups/tt/tt-<time>.dump`) with `scripts/ttctl db restore FILE`.

## Dev / UAT installs
`git pull`, then `scripts/ttctl setup && scripts/ttctl db migrate && scripts/ttctl restart`. A dev install from a bundle:
`./install.sh` of the new bundle installs the new release beside the old one and switches `current`.

## Cleaning up old releases
Keep at least `current` and `previous`. Older ones can be deleted: `sudo rm -rf /opt/team-tracker/releases/<old-version>`.
