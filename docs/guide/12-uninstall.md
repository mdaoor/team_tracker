# 12. Uninstall

## Local / UAT install
| Remove | Command |
| --- | --- |
| stop everything | `scripts/ttctl stop --with-db` (Docker: `scripts/ttctl docker down`) |
| the database | `scripts/ttctl db drop` (Docker: `scripts/ttctl docker reset`) |
| build output, caches, logs | `scripts/ttctl clean` |
| also `.venv`, `node_modules` and `.tools/` | `scripts/ttctl clean --deps --tools` |
| the `tt` CLI | `scripts/ttctl client uninstall` (client kit: `./install.sh uninstall` / `.\install.ps1 uninstall`) |
| client data | `~/.config/tt` (sign-in), `~/.local/share/tt` (outbox, personal store) |
| everything | delete the checkout |

PostgreSQL itself stays installed (it is a system package); remove it with your package manager if nothing else uses it.

## Production
```bash
sudo scripts/ttctl prod stop
```
```bash
sudo scripts/ttctl prod disable
```
```bash
sudo rm -f /etc/systemd/system/tt-server.service /etc/systemd/system/tt-worker.service /etc/systemd/system/tt-llm-gateway.service /etc/nginx/conf.d/team-tracker.conf /etc/nginx/conf.d/team-tracker.proxy && sudo systemctl daemon-reload
```
Then, after a final backup (`scripts/ttctl db backup`): drop the database, delete `/etc/tt` (configuration and secrets),
`/var/lib/team-tracker` (uploaded files), `/opt/team-tracker` (code, releases), `/var/backups/tt`, and the `tt` user
(`sudo userdel tt`).
