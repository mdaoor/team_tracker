# systemd install (no containers)

```
useradd --system --home /var/lib/team-tracker --shell /usr/sbin/nologin tt
install -d -o tt -g tt /var/lib/team-tracker /etc/tt /etc/tt/tls
# code: /opt/team-tracker with a virtualenv at .venv (uv sync --all-packages --no-dev) and the built web app behind your web server
cp deploy/systemd/*.service /etc/systemd/system/ && systemctl daemon-reload
install -m 600 -o tt /dev/null /etc/tt/tt.env   # then fill it from .env.example (TT_ENV=prod, TT_SESSION_SECRET, TT_SECRET_KEY, TT_DATABASE_URL, TLS paths)
systemctl enable --now tt-server tt-worker
```
Rotate the certificate by replacing the PEM files and `systemctl reload tt-server` (or just wait for the 30 s file check).
