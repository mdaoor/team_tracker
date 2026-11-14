# 3. Install on an air-gapped host

Two steps: build **one file** on a connected machine, copy it across, install it on the host without network.
Nothing on the offline host is downloaded: the bundle has the application, Python, every Python package (hash-pinned), the
**prebuilt** web console (no Node on the server), PostgreSQL 16 packages, Chromium and the client kit for users' computers.

## Step A - on a connected machine

Needs Docker, git, curl and python3 (any). The bundle is built inside a container of the **target OS**, so every binary
matches the offline host's C library and package versions.

```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker && git checkout v1.0.0
```
```bash
scripts/ttctl bundle create --target rhel8-x86_64 --with-db rpm --with-browsers
```

| Option | Adds |
| --- | --- |
| `--target` | `rhel8-x86_64`, `rhel9-x86_64`, `ubuntu22.04-amd64`, `ubuntu24.04-amd64` (required) |
| `--with-db rpm\|deb` | PostgreSQL 16 server + contrib + client packages with every dependency a minimal host lacks (rpm for RHEL, deb for Ubuntu) |
| `--with-db image` | the `postgres:16` Docker image instead (the host then runs `--db docker`) |
| `--with-browsers` | Chromium and the packages of its system libraries (PDF/PNG export) |
| `--with-llm MODEL` | Ollama and the model, for the LLM gateway in `local_only` mode (e.g. `--with-llm qwen2.5:7b-instruct`) |
| `--with-docker-images` | `docker save` tars of the Team Tracker images and `postgres:16` |
| `--with-dev` | Node, pnpm and an offline pnpm store, dev Python packages (rebuild and test on the host) |
| `--compress gz` | `.tar.gz` instead of `.tar.zst`, for hosts without `zstd` (RHEL 8 minimal installs) |
| `--sign KEY` | a detached GPG signature of `SHA256SUMS` |

Result: `dist/tt-bundle-<version>-<target>.tar.zst` and `....sha256`. Check what is inside:
```bash
scripts/ttctl bundle info dist/tt-bundle-1.0.0-rhel8-x86_64.tar.zst
```
```bash
scripts/ttctl bundle verify dist/tt-bundle-1.0.0-rhel8-x86_64.tar.zst
```

Expected output of `bundle create` (sizes vary):
```text
Bundle tt-bundle-1.0.0-ubuntu24.04-amd64
[1] source snapshot of 77669f8
[2] uv 0.11.32 from PyPI
[3] OS packages in a fresh ubuntu:24.04 (PostgreSQL 16 Chromium libraries)
[4] build inside ubuntu:24.04 (Python, wheelhouse, prebuilt web apps, Chromium, client kit)
[5] pack tt-bundle-1.0.0-ubuntu24.04-amd64.tar.zst
 ok bundle: dist/tt-bundle-1.0.0-ubuntu24.04-amd64.tar.zst (552M, unpacked 910M)
   app                         9.4 MB
   uv                         66.1 MB
   python                    106.2 MB
   wheelhouse                168.9 MB
   postgresql-deb             76.3 MB
   chromium                  410.3 MB
   ...
```

### Bundle sizes

Measured for `ubuntu24.04-amd64` (the CI job prints the exact numbers for every target in its summary):

| Bundle | Unpacked | `.tar.gz` | `.tar.zst` |
| --- | --- | --- | --- |
| base: app, uv (66 MB), Python 3.12 (106 MB), wheelhouse (169 MB), prebuilt web apps, client kit (31 MB) | 379 MB | 262 MB | - |
| `--with-db deb` adds | + 80 MB (PostgreSQL 16 packages and client programs) | | |
| `--with-browsers` adds | + 476 MB (Chromium 410 MB, library packages 65 MB) | | |
| base + `--with-db deb --with-browsers` | 910 MB | 585 MB | 552 MB |

RHEL targets are similar. `--with-llm` adds the model (a 7B model is 4–5 GB), `--with-docker-images` about 600 MB,
`--with-dev` about 450 MB (Node and the pnpm store). Unpacking needs the unpacked size free next to the file, and the
install as much again under the prefix.

Alternative without a bundle: point the host at internal mirrors (Python index, npm registry, rpm/apt mirror) and use
the [online install](02-install-online.md); see [FAQ](13-faq.md#internal-mirror).

## Step B - on the air-gapped host

Copy `tt-bundle-...tar.zst` (or `.tar.gz`) and its `.sha256` to the host, into an empty directory, then work there.
The lines below are what the CI job runs, word for word, in a container without any network.

Check the copy:
```bash runnable-offline
sha256sum -c tt-bundle-*.sha256
```
Unpack (`.tar.zst` needs `zstd`: `zstd -dc tt-bundle-*.tar.zst | tar -xf -`; a `--compress gz` bundle needs only tar):
```bash runnable-offline
tar -xzf tt-bundle-*.tar.gz
```
Install. As root this goes to `/opt/team-tracker`; as a normal user to `~/team-tracker` (`--prefix DIR` to choose).
`--with-db` installs the bundled PostgreSQL packages (root; it shows the command and asks; `--yes` answers):
```bash runnable-offline
cd tt-bundle-*/ && ./install.sh --with-db --yes
```
What `install.sh` does:
1. verifies every file against `SHA256SUMS` (and the signature, when there is one) - nothing runs before that;
2. checks the bundle was built for this OS (`--force` to override);
3. copies the application to `PREFIX/releases/<version>`, installs uv, Python and Chromium into its `.tools/`, creates
   `.venv` from the wheelhouse with hashes checked, and points `PREFIX/current` at the release;
4. writes `.ttctl.env` with `TT_OFFLINE=1` (plus `UV_OFFLINE=1`, `PIP_NO_INDEX=1`, `npm_config_offline=true`,
   `COREPACK_ENABLE_NETWORK=0`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`): from now on every ttctl action that would need the
   network stops with exit code 4 and names the bundle option that would have provided it;
5. creates `PREFIX/.env` (kept across upgrades) and installs PostgreSQL 16 from the bundle.

Then the normal commands work unchanged:
```bash runnable-offline
cd /opt/team-tracker/current
```
```bash runnable-offline
scripts/ttctl db setup
```
```bash runnable-offline
scripts/ttctl start
```
```bash runnable-offline
scripts/ttctl status
```
```bash runnable-offline
.venv/bin/python ci/ops/smoke.py
```
```bash runnable-offline
scripts/ttctl stop
```

Production on the same host: `sudo /opt/team-tracker/current/scripts/ttctl prod install --mode proxy --prefix /opt/team-tracker`
(or `./install.sh --with-db --mode prod`), see [Production](06-production.md). Upgrades come as a new bundle:
[Upgrade and rollback](07-upgrade-and-rollback.md#from-a-bundle).

## LLM gateway offline
Without `--with-llm`, no model is installed: the app uses the deterministic parser and says so after the install. With
it, the gateway runs in `privacy_mode: local_only` against the bundled Ollama (`scripts/ttctl start gateway`).

## Users' computers
The bundle's `client-kit/` folder installs the `tt` command line on Linux, macOS (Apple silicon) and Windows without
network: see [Clients](08-clients.md#client-kit).
