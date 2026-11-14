# Operations Scripts Specification (OSS-1)

A project-agnostic standard for the `scripts/` folder of any application: one control command, modular parts, a development mode that needs no external services, separate **download** and **install** phases, and the same scripts for a laptop, an air-gapped server and a cloud deployment.

Version 1.0. Copy this file into any project. Nothing in it names a product.

The words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are used as in RFC 2119.

---

## 1. What this standard gives a project

| Need | How the standard meets it |
|---|---|
| A new person runs the app in minutes | `appctl dev` works on a fresh clone with no database server, no Docker and no root (§7) |
| A server with no internet can be installed | Download and install are two phases; install never touches the network (§6, §10) |
| The same scripts deploy to cloud | Deployment targets are plug-ins behind one `deploy` verb (§11) |
| Scripts fail with useful messages | One error format, verbose and trace modes, a log file for every run, stable exit codes (§8) |
| Scripts are reusable across projects | Generic kit (`lib/`, `commands/`) plus one declarative manifest per project (§3, §4) |

`appctl` is a placeholder. Each project names its control command (`ttctl`, `shopctl`, …) and picks an upper-case prefix for environment variables, written `APP_` below.

## 2. Principles

1. **One entry point.** Everything is reachable as `scripts/appctl <verb>`. Individual files are implementation detail.
2. **Declarative app, generic kit.** What differs between projects lives in one manifest. The kit contains no application names.
3. **Fetch then install.** Anything that needs the network is done by `fetch` and stored with a checksum. `install` reads only from that store. Online setup is simply `fetch` followed by `install`; air-gapped setup inserts a file transfer between them. There is one install path, so the air-gapped path is always tested.
4. **No external services in development.** The default development profile starts every dependency it needs from the project folder.
5. **User space first.** Nothing requires root unless the operating system makes it unavoidable, and then the script shows the exact commands and asks.
6. **Idempotent and reversible.** Every verb can be run twice. Whatever `install` creates, `uninstall` removes.
7. **Honest output.** A script says what it did, what failed, why, and what to run next, and names the command the user typed.
8. **Same artifact everywhere.** What is tested is what is shipped: one build output, configured per environment.

## 3. Folder layout

```
scripts/
├── appctl                  entry point: parses global flags, dispatches a verb. No logic.
├── manifest.sh             THE project description (§4). The only file every project must write.
├── tools.lock              pinned external tools: name version os arch url sha256 (§6.2)
├── KIT_VERSION             version of this standard the kit implements
├── lib/                    generic helpers. No application names. Replaceable as a unit.
│   ├── core.sh             strict mode, paths, global flags, step runner, traps
│   ├── ui.sh               output levels, colour, prompts, tables, JSON output
│   ├── errors.sh           error catalogue, WHAT/WHY/FIX/DOCS printer, exit codes
│   ├── os.sh               OS, architecture, package manager and privilege detection
│   ├── net.sh              the ONLY place that performs network access; honours APP_OFFLINE
│   ├── proc.sh             start, stop, PID files, process groups, ports, health waits
│   ├── config.sh           profiles, env files, precedence, masking of secrets
│   ├── fetch.sh            download, checksum, cache layout
│   └── bundle.sh           create, verify, unpack bundles
├── commands/               one file per verb (§5). Thin: validate arguments, call lib and hooks.
├── providers/              how to run a backing service locally, one file per service and mode (§7)
│   ├── postgres.local.sh   user-space binaries managed by the scripts
│   ├── postgres.container.sh
│   └── sqlite.embedded.sh  …
├── targets/                where the app is deployed, one file per target (§11)
│   ├── local.sh  systemd.sh  compose.sh  kubernetes.sh  <cloud>.sh
├── hooks/                  optional project-specific steps: migrate.sh seed.sh pre-start.sh post-install.sh smoke.sh
├── win/                    PowerShell twins of the client-side verbs, when users run Windows (§14)
└── tests/                  bats tests and container test drivers (§15)
```

Working directories, all git-ignored, all under the project root unless a prefix is given:

| Path | Content | Removed by |
|---|---|---|
| `.cache/fetch/` | Everything downloaded, with checksums | `clean --cache` |
| `.tools/` | User-space tools installed for this project | `clean --deps` |
| `.run/` | PID files, logs, local service data, sockets | `clean` (data only with `--data`) |
| `dist/` | Build outputs and bundles | `clean --dist` |

Rules:

- `lib/` and `commands/` MUST be identical across projects that use the same `KIT_VERSION`. Upgrading the kit means replacing those two folders.
- Only `lib/net.sh` MAY call `curl`, `wget`, a package registry, or any command that reaches the network. A lint rule enforces this (§15).
- Every file starts with a header comment that is also its `--help` text.

## 4. The manifest

`scripts/manifest.sh` is declarative shell: assignments and arrays only, no commands, no conditionals. `appctl manifest --json` prints it as JSON for other tools. Projects that prefer YAML or TOML MAY use it if the parser is itself a pinned tool; the keys stay the same.

```bash
APP_NAME="exampleapp"            # lower-case, used in paths and service names
APP_PREFIX="EXAMPLE"             # environment variable prefix
APP_VERSION_CMD="git describe --tags --always"
APP_CTL="examplectl"             # name shown in help and messages

# Components: long-running processes the scripts manage.
COMPONENTS=(api worker web)
COMPONENT_api=(   cmd="uv run example-server"  port=8000 health="http://127.0.0.1:8000/healthz" needs="db"      )
COMPONENT_worker=(cmd="uv run example-worker"                                                     needs="db api"  )
COMPONENT_web=(   cmd="pnpm --filter web dev"  port=5173 health="http://127.0.0.1:5173/"          profile="dev"   )
OPTIONAL_COMPONENTS=(gateway)

# Backing services and how each profile gets one (§7).
SERVICES=(db)
SERVICE_db=(kind="postgres" version="16" extensions="pg_trgm ltree" \
            dev="local" test="local" prod="external" url_var="EXAMPLE_DATABASE_URL")

# Tools the app needs to build and run, with accepted versions.
TOOLS=("python>=3.12" "uv>=0.4" "node>=22" "pnpm=9.15.0")
TOOLS_RUNTIME=("python>=3.12" "uv>=0.4")       # subset needed on a server that receives prebuilt artifacts

# Dependency sets fetched and installed, each driven by a lockfile.
DEPS=(python node)
DEPS_python=(lock="uv.lock"        fetch="hooks/fetch-python.sh" install="hooks/install-python.sh")
DEPS_node=(  lock="pnpm-lock.yaml" fetch="hooks/fetch-node.sh"   install="hooks/install-node.sh"  build_only=1)

# Build outputs that make up a release.
ARTIFACTS=(wheels web_dist image_api image_worker)

# Supported operating targets for bundles and tests.
TARGETS_OS=(rhel8-x86_64 rhel9-x86_64 ubuntu22.04-amd64 ubuntu24.04-amd64)

# Deployment targets enabled for this project (§11).
DEPLOY_TARGETS=(local systemd compose kubernetes)

# Hooks, all optional.
HOOK_MIGRATE="hooks/migrate.sh"; HOOK_SEED="hooks/seed.sh"; HOOK_SMOKE="hooks/smoke.sh"
```

Requirements:

- The manifest MUST NOT contain secrets.
- Every component MUST declare how to tell it is healthy (an HTTP URL, a TCP port, or a command) unless it has no observable readiness, in which case it declares `health="none"` and a `ready_after` delay.
- `needs=` defines start order; stop order is the reverse.
- `build_only=1` marks a dependency set that is not required on a host receiving prebuilt artifacts. A release SHOULD prebuild front-end assets so servers need no Node toolchain.

## 5. Verbs

Every verb accepts the global flags in §8.1 and `--help`. "Level" refers to §16.

| Verb | Purpose | Network | Level |
|---|---|---|---|
| `help [VERB]`, `version` | Usage; kit, app and manifest versions | no | 1 |
| `doctor [--fix]` | Check tools, configuration, services, ports, health; `--fix` applies safe repairs | no | 1 |
| `dev` | Fetch if needed, install, start everything in the dev profile, print URLs (§7) | first run only | 1 |
| `start [COMPONENT…]`, `stop […]`, `restart […]` | Background processes in dependency order | no | 1 |
| `run COMPONENT` | One component in the foreground | no | 1 |
| `status [--json]`, `logs [-f] [COMPONENT…]` | What runs, PIDs, ports, health; log viewing | no | 1 |
| `config init\|show\|check` | Write, print (masked) and validate configuration | no | 1 |
| `data start\|stop\|status\|create\|migrate\|seed\|reset\|backup\|restore\|shell` | Backing services of the active profile | no | 1 |
| `build [ARTIFACT…]`, `check [lint\|test\|e2e\|all]` | Build outputs; quality gates | no | 1 |
| `clean [--deps] [--cache] [--data] [--dist]` | Remove generated state | no | 1 |
| `prereqs check\|install [TOOL…]` | Detect and install required tools (§9) | via cache | 2 |
| `fetch [--target OS] [--with …]` | Download everything `install` will need (§6) | **yes, only here** | 2 |
| `install [--prefix DIR] [--profile P] [--with-data]` | Install from the cache or a bundle | **never** | 2 |
| `setup` | Convenience: `fetch` + `install` + `config init` | via fetch | 2 |
| `bundle create\|info\|verify\|install` | Portable archive for air-gapped hosts (§10) | create only | 2 |
| `uninstall [--purge]` | Remove what install created; `--purge` also data and config | no | 2 |
| `image build\|push\|save\|load` | Container images; `save`/`load` move them without a registry | push only | 3 |
| `deploy TARGET ENV plan\|apply\|status\|rollback\|destroy` | Deploy to a target (§11) | target-dependent | 3 |
| `upgrade [--from-bundle FILE]`, `rollback` | New release with health gate; return to the previous one | no with bundle | 3 |
| `support-bundle` | Archive of logs, doctor output, versions and masked config | no | 3 |

Rules:

- A project MUST NOT repurpose a verb. It MAY add verbs.
- `dev`, `setup`, `install`, `start`, `stop`, `data migrate`, `deploy … apply` MUST be idempotent.
- `stop` with no arguments stops everything the scripts started and nothing else. It MUST NOT stop an external service.
- `status --json` output is a stable interface: `{component, state, pid, port, healthy, since, log}`.

## 6. Fetch and install: the two-phase rule

### 6.1 The rule

- `fetch` MAY use the network. It MUST NOT modify anything outside `.cache/fetch/` (or the bundle staging directory).
- `install` MUST NOT use the network. It reads only from `.cache/fetch/` or from an unpacked bundle. An attempted network call during `install` is a defect and is caught by a test that runs with networking disabled (§15).
- `setup` and `dev` call `fetch` only when the cache is missing or older than the lockfiles, then `install`. With `APP_OFFLINE=1` they skip `fetch` and fail with exit code 4 if the cache is incomplete, naming the missing item.

### 6.2 What fetch collects

Everything is pinned and verified.

| Item | Pinned by | Notes |
|---|---|---|
| External tools (runtime, package manager, local service binaries, browser for exports) | `scripts/tools.lock`: name, version, os, arch, url, sha256 | One row per supported OS and architecture |
| Language dependencies | The project's lockfiles with hashes | Fetched for the **target** OS and architecture, not the host's |
| System packages that have no user-space form | Package name and version per OS in `tools.lock`, resolved with all dependencies | Collected as `.rpm` / `.deb` files |
| Container images | Digest | Only with `--with images` |
| Optional large assets (models, datasets) | URL and sha256 | Only with an explicit `--with` flag |

- `fetch --target <os-arch>` MUST produce a cache valid for that target. When the target differs from the host, fetch runs inside a container of the target OS so native packages match.
- Every downloaded file is checked against its sha256 before it enters the cache. A mismatch is exit code 1 with both hashes printed.
- `curl | sh` and unpinned "latest" downloads are forbidden.
- The cache has an index file listing each item, its version, its hash and which lockfile required it. `fetch --check` reports missing items without downloading.

### 6.3 What install does

1. Verify the cache or bundle against its checksums.
2. Install required tools into `.tools/` (or `<prefix>/tools`), or system packages when unavoidable (§9).
3. Install each dependency set from the cache with the package manager's offline mode.
4. Build or unpack artifacts.
5. Write configuration if absent (`config init`); never overwrite an existing file.
6. With `--with-data`: start or create the backing services of the profile, migrate, optionally seed.
7. Run `doctor` and the smoke hook; print the next command.

Install sets these for every child process so that any tool misbehaving fails fast instead of hanging: `APP_OFFLINE=1`, `UV_OFFLINE=1`, `PIP_NO_INDEX=1`, `npm_config_offline=true`, `COREPACK_ENABLE_NETWORK=0`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, `GOFLAGS=-mod=mod` with `GOPROXY=off`, `CARGO_NET_OFFLINE=true`, and the equivalents of any other ecosystem the project uses.

## 7. Development mode without external services

`appctl dev` on a fresh clone MUST end with the application running and its URLs printed, having required no database server, no Docker daemon, no root access and no manual step other than answering yes to the download prompt.

### 7.1 Service providers

Each backing service declares one provider per profile. Providers are chosen in this order of preference for the dev and test profiles:

| Provider | Meaning | Use when |
|---|---|---|
| `embedded` | The service runs inside the application process (for example SQLite, an in-memory queue) | The application supports it without behaviour differences that matter |
| `local` | Real server binaries in `.tools/`, started and stopped by the scripts, data in `.run/data/<service>`, bound to localhost or a Unix socket on a project-specific port | The application depends on engine-specific features |
| `container` | The service runs in a container started by the scripts | A container runtime is present and `local` is not available for the OS |
| `external` | The service already exists; the scripts only read its URL | Staging and production |

Rules:

- The default provider for `dev` and `test` MUST NOT be `external`, and SHOULD NOT be `container`.
- A `local` provider MUST: choose a free port and record it in `.run/`; initialise its data directory on first start; create the application database, user and required extensions; stop cleanly with `stop`; leave no process behind after `stop` (verified by a test).
- `data reset` recreates the service data from nothing, then migrates and seeds. It MUST refuse to run against an `external` provider unless `--force-external` and a typed confirmation are given.
- Seeds are deterministic, so screenshots, tests and demos agree.
- When a dev provider differs from production (embedded versus a real server), `doctor` says so once, and the test suite MUST also run against the production engine in CI.

### 7.2 Profiles

A profile selects providers, components and configuration defaults. Required profiles: `dev`, `test`, `prod`. `--profile` or `APP_PROFILE` selects one; `dev` is the default in a git checkout, `prod` in an installed prefix.

| | dev | test | prod |
|---|---|---|---|
| Services | embedded or local | embedded or local, isolated per run | external (or installed on the same host by `install --with-data`) |
| Components | all, including hot-reload front ends | only what tests need | runtime components only |
| Secrets | generated on first run, stored in `.run/` | fixed test values | provided by the operator or a secret store; never generated silently |
| Sign-in shortcuts, debug endpoints | allowed | allowed | MUST be off; `doctor` fails otherwise |

## 8. Behaviour every script shares

### 8.1 Global flags

Accepted before or after the verb, and passed to child scripts through the environment.

| Flag | Env | Effect |
|---|---|---|
| `-v`, `--verbose` | `APP_VERBOSE=1` | Print every command before it runs, every probe with path and raw output, effective configuration with secrets masked |
| `-vv`, `--trace` | `APP_VERBOSE=2` | As above plus shell tracing with file and line |
| `-q`, `--quiet` | `APP_QUIET=1` | Errors and the final result only |
| `--dry-run` | `APP_DRY_RUN=1` | Print what would be done; change nothing |
| `-y`, `--yes` | `APP_YES=1` | Assume yes for prompts; required for non-interactive use |
| `--json` | — | Machine-readable result on stdout for verbs that report state |
| `--no-color` | `NO_COLOR=1` | Plain text; also automatic when stdout is not a terminal |
| `--profile P` | `APP_PROFILE` | Select the profile |
| `--offline` | `APP_OFFLINE=1` | Forbid network access everywhere |
| `--log-file PATH` | `APP_LOG_FILE` | Override the per-run log location |

### 8.2 Output and logs

- Work is shown as numbered steps: `[2/6] Installing Python dependencies`.
- In normal mode a step's output is captured; on failure the last 40 lines are printed. In verbose mode it streams.
- Every run writes a full-detail log to `.run/logs/appctl-<verb>-<timestamp>.log` regardless of verbosity, and prints the path when it fails.
- Secrets are masked in all output and logs: any variable whose name matches `*SECRET*|*TOKEN*|*PASSWORD*|*KEY*` and the password part of URLs.
- Human messages go to stderr when `--json` is set, so stdout stays parseable.

### 8.3 Errors

Every error is printed in four parts and carries a stable code `<APP>-E<nnn>`:

```
error EXAMPLE-E012  pnpm 9.15.0 is not installed
  why    looked in PATH and .tools/bin; `pnpm --version` not found
  fix    examplectl prereqs install pnpm          (offline: included in the bundle; run `examplectl install`)
  docs   docs/guide/troubleshooting.md#example-e012
  log    .run/logs/appctl-setup-20260101-101500.log
```

- The `fix` line MUST be a command that works on this host in the current mode. In offline mode it MUST NOT suggest a download.
- Messages name the command the user typed (`examplectl setup`), never an internal file.
- A warning states its consequence: "psql 10 is older than the server (16): `data backup` and `data shell` will use the bundled client instead."
- A script that stops MUST say which step stopped and what was completed before it.

### 8.4 Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime failure |
| 2 | Usage error |
| 3 | A prerequisite is missing |
| 4 | Blocked because offline |
| 5 | Configuration invalid or missing |
| 6 | Not healthy after the timeout |
| 7 | Refused: confirmation required and not given |

### 8.5 Help

`--help` on every verb and sub-verb prints: one-line purpose, synopsis, options, at least two examples, exit codes, and the documentation page. Help MUST work without any prerequisite installed and without a manifest error stopping it.

### 8.6 Prompts and privilege

- A prompt appears only when a terminal is attached. Without a terminal and without `--yes`, the script exits 7 and prints the exact command to re-run.
- Scripts MUST NOT invoke `sudo` silently. When privilege is needed they print the commands, explain why, and ask. `--dry-run` prints them and stops.
- Destructive actions (`data reset`, `uninstall --purge`, `deploy … destroy`, `rollback`) require typing the target name, or `--yes --force`.

## 9. Prerequisites

- `prereqs check` compares installed tools with the manifest and prints, for each: found path, version, required range, verdict. `--json` is available.
- `prereqs install` installs what is missing from the fetch cache or bundle. With network allowed and an incomplete cache it runs `fetch` for the missing tools first.
- Tools that can run from a directory are installed in `.tools/` and found through a PATH that the scripts set themselves; the user's shell profile is not edited.
- System packages are used only for what cannot run from user space. The script detects the OS from `/etc/os-release`, supports each entry of `TARGETS_OS`, and for an unsupported OS prints the list of needed tools and versions and exits 3.
- Checks test what matters. For a database, check the **server** version and required extensions through a connection, not the version of a client binary on PATH.
- `setup` and `dev` offer to install missing prerequisites; they do not merely report them.

## 10. Bundles for air-gapped hosts

### 10.1 Two steps

**Step A, on a connected machine:**

```
appctl bundle create --target rhel8-x86_64 [--with data-packages] [--with images] [--with browser] [--with dev]
→ dist/<app>-<version>-<target>.tar.zst  +  .sha256  [+ .sig]
```

**Step B, on the isolated host:** copy the file, unpack, run `./install.sh` (a three-line wrapper around `scripts/appctl bundle install`). No other command and no network are needed.

### 10.2 Bundle layout

```
<app>-<version>-<target>/
├── install.sh                 wrapper: verify, then appctl install --from-bundle .
├── manifest.json              app, version, commit, target, kit version, components, options included, sizes
├── SHA256SUMS                 every file
├── sbom.json  LICENSES/       software bill of materials and third-party licences
├── scripts/                   the kit and this project's manifest and hooks
├── app/                       source snapshot or built packages of the tagged commit
├── artifacts/                 prebuilt outputs (for example front-end dist, wheels, binaries)
├── cache/                     the fetch cache for this target: tools, language packages, system packages
├── images/                    optional `image save` archives
├── docs/                      the install and operations guide
└── client/                    optional kit for end-user machines (§14)
```

### 10.3 Requirements

- A bundle is complete for its options: `bundle verify` proves that everything `install` needs for the chosen profile is inside, without installing.
- A bundle built for one target MUST refuse to install on another, naming both, unless `--force-target`.
- `bundle info FILE` prints the manifest in readable form, including what was *not* included and the flag that would include it.
- Bundles are reproducible enough to compare: same commit and same lockfiles give the same file list and hashes, apart from timestamps.
- Signing is optional but, when a public key is configured, `bundle install` MUST verify the signature and refuse on failure.
- `upgrade --from-bundle FILE` installs the new release beside the current one, migrates, runs the health gate, switches, and keeps the previous release for `rollback`.
- An internal mirror (package index, registry) is an allowed alternative to bundles; it is configured through environment variables and does not change the two-phase rule.

### 10.4 Installed layout on servers

```
<prefix>/                      default /opt/<app>
├── releases/<version>/        immutable
├── current -> releases/<version>
├── shared/config/             environment file(s), mode 600
├── shared/data/  shared/logs/
└── tools/
```

## 11. Deployment targets

`appctl deploy TARGET ENV ACTION` gives every target the same five actions. A target is one file in `scripts/targets/` implementing them.

| Action | Contract |
|---|---|
| `plan` | Show what would change. Never changes anything. Exit 0 when there is nothing to do |
| `apply` | Make the environment match the release. Idempotent. Runs migrations as a separate, gated step before switching traffic. Waits for health; on failure reports and leaves the previous release serving |
| `status` | Release version, health and endpoints per component, as text or `--json` |
| `rollback` | Return to the previous release recorded by `apply` |
| `destroy` | Remove the deployment. Requires typed confirmation |

Standard targets:

| Target | What it manages | Air-gapped use |
|---|---|---|
| `local` | Background processes in the checkout (the `start`/`stop` verbs) | yes |
| `systemd` | Units generated from the manifest, one per component, running as an unprivileged user from `<prefix>/current` | yes, from a bundle |
| `compose` | A compose project generated from or checked against the manifest | yes, with `image load` and `pull_policy: never` |
| `kubernetes` | A chart or manifests with values per environment | yes, with images pushed to an internal registry via `image load` + `image push --registry` |
| `<cloud>` | A thin module that calls the provider's tooling or the project's infrastructure-as-code | no |

Rules:

- Scripts orchestrate; they do not replace infrastructure-as-code. A cloud target calls the IaC tool with pinned versions and passes through its plan output.
- The release artifact is built once (`build`, `image build`) and promoted unchanged through environments. Configuration is the only thing that differs.
- Per-environment settings live in `deploy/<target>/<env>.env` (non-secret) and in a secret store reference. Secrets are never written to the repository, the manifest, a bundle or a log.
- Every `apply` records release version, commit, who, when and result in a small history file or the target's own metadata, which is what `status` and `rollback` read.
- All actions run non-interactively with `--yes`, so CI uses exactly the commands people use.
- Registry, cluster and credentials come from the environment; a target MUST print which ones it will use during `plan`.

## 12. Configuration

- One schema is the source of truth for settings: name, type, default, whether secret, which profiles require it. `config check` validates against it; the documentation reference is generated from it.
- Precedence, highest first: command-line flag, process environment, environment file of the profile, schema default.
- `config init` writes a commented environment file for the profile with safe defaults. For `prod` it leaves required secrets empty and lists them; it MUST NOT invent production secrets without saying so and showing where they were written.
- `config show` prints the effective configuration and where each value came from, with secrets masked.
- The application MUST refuse to start in `prod` with development defaults; `doctor` reports the same condition first.

## 13. Process management

- `start` launches each component in its own process group, writes `.run/<component>.pid`, redirects output to `.run/logs/<component>.log`, and waits for the health check up to a timeout (exit 6 on timeout, with the log tail).
- `start` on a running component is a no-op that reports the PID. A stale PID file is detected and removed.
- A port already in use is reported with the process that holds it.
- `stop` sends a termination signal to the group, waits a grace period, then kills, and verifies nothing remains. Order is the reverse of start.
- `status` exits 0 when everything expected is healthy, 6 otherwise.
- On servers, process supervision belongs to the target (systemd, the orchestrator). The `local` target is for development and single-host trials.

## 14. Portability and client machines

- Shell: Bash 4.2 or later. Scripts use only utilities present on every OS in `TARGETS_OS`, or a pinned tool from `tools.lock`. GNU-only options are wrapped in `lib/os.sh`.
- macOS is supported for development when the project lists it; the kit avoids `readlink -f`, `sed -i` without suffix and other divergent calls outside `lib/os.sh`.
- When end users install a client on Windows, `scripts/win/` provides PowerShell scripts for the client-side verbs (`install`, `uninstall`, `status`, `configure`) with the same flags (`-Verbose`, `-Offline`, `-WhatIf`) and the same error format. Server-side verbs are not required on Windows.
- A client kit in a bundle contains installers or packages for each supported client OS, an offline install script, and the trust configuration (server address, CA certificate).

## 15. Tests and continuous integration

A project conforms only if these run in CI:

| Test | Proves |
|---|---|
| Shell lint on every script, plus a rule that network commands appear only in `lib/net.sh` | Basic correctness and the network boundary |
| Unit tests of `lib/` (flags, step runner, error printer, exit codes, masking, offline guard) | The shared behaviour of §8 |
| `--help` on every verb exits 0 and mentions every option the verb parses | Help is complete |
| Fresh-clone `dev` in a clean container, then `status`, `stop`, and a check that no process remains | §7 |
| Per `TARGETS_OS`: `bundle create`, then in a container **with networking disabled**: `install.sh`, `data migrate`, `start`, health, smoke hook, `stop`, `uninstall` | §6 and §10; any network attempt fails the job |
| `setup` run twice gives the same result; `uninstall` leaves no files outside the data directory | Idempotence and reversibility |
| Each deployment target: `plan` on an applied release reports no changes; `apply` then `rollback` restores the previous version | §11 |
| Every error code used in scripts has a troubleshooting section, and the reverse | Messages and documentation agree |
| Commands shown in the guide are executed | Documentation is true |

## 16. Conformance levels

| Level | Name | Required sections | Typical use |
|---|---|---|---|
| 1 | Runnable | §3, §4, §5 (level 1 verbs), §7, §8, §12, §13 | Any project from its first week |
| 2 | Installable offline | Level 1 plus §6, §9, §10, §14 | Anything delivered to a customer site or isolated network |
| 3 | Deployable | Level 2 plus §11 and the level 3 verbs | Services operated in cloud or on a cluster |

A project states its level in its README: `Operations scripts: OSS-1 level 2`. The tests of §15 that apply to the level are the proof.

## 17. Documentation each project provides

Generated where possible so it cannot drift:

1. Requirements and support matrix (from the manifest).
2. Quick start for development: `appctl dev`.
3. Install online; install air-gapped in two steps, with expected output.
4. Run, stop, status, logs.
5. Data: migrate, seed, backup, restore, reset.
6. Deploy per enabled target; upgrade and rollback.
7. Configuration reference (generated from the schema).
8. Scripts reference (generated from `--help`).
9. Troubleshooting, one section per error code.
10. Uninstall.

## 18. Security requirements

- Everything fetched is pinned by version and sha256; lockfiles are committed.
- No script pipes a download into a shell.
- Bundles carry checksums for every file, a bill of materials and licences; signatures are verified when a key is configured.
- Secrets never appear in the manifest, bundles, logs, `status`, `support-bundle` or error messages.
- Installed services run as an unprivileged user; configuration files holding secrets are mode 600 and owned by that user.
- Local dev services bind to localhost or a Unix socket only.
- `support-bundle` lists what it includes and asks before writing.

---

## Appendix A. Minimal adoption path for an existing project

1. Copy `scripts/lib/`, `scripts/commands/`, `scripts/appctl` and `KIT_VERSION` from the kit. Rename `appctl`.
2. Write `scripts/manifest.sh`: components with commands, ports and health checks; services with a provider per profile; tools with versions; dependency sets and their lockfiles.
3. Choose the dev provider for each service (§7.1) and add its file under `providers/`. Add `hooks/migrate.sh` and `hooks/seed.sh`.
4. Fill `scripts/tools.lock` for each supported OS.
5. Run `appctl dev` in a clean container until it passes. That is level 1.
6. Write the fetch and install hooks per dependency set; run the networking-disabled test until it passes. That is level 2.
7. Enable deployment targets and their tests. That is level 3.

## Appendix B. Conformance checklist

- [ ] `appctl dev` works on a fresh clone with no external service, no Docker, no root
- [ ] `appctl stop` leaves no process; `appctl status` exits 0 only when healthy
- [ ] Only `lib/net.sh` reaches the network; `install` passes with networking disabled
- [ ] `fetch --target` produces a cache for another OS; `bundle verify` proves completeness
- [ ] Every verb has `--help`, `-v`, `-vv`, `--dry-run`, `--yes`; every run writes a log
- [ ] Every error prints what, why, fix, docs and a code; exit codes follow §8.4
- [ ] No silent `sudo`; destructive actions need confirmation
- [ ] Secrets are masked everywhere and absent from manifest and bundles
- [ ] `setup` twice is harmless; `uninstall` removes what `install` created
- [ ] Each deployment target implements plan, apply, status, rollback, destroy
- [ ] Documentation references are generated from the schema and `--help`
- [ ] The CI jobs of §15 for the claimed level are green

## Appendix C. Prompt for an implementing agent

```
Implement the Operations Scripts Specification OSS-1 (docs/standards/ops-scripts-spec.md) in this repository to level <1|2|3>.

1. Read the spec fully. Then inventory this project: components and how each is started, ports, health endpoints, backing services and engine-specific features used, language toolchains and lockfiles, build outputs, current scripts and Makefile targets, supported operating systems, how it is deployed today.
2. Write scripts/manifest.sh from that inventory. Show it to me as a table before going further if anything is ambiguous; otherwise continue and list assumptions in the final report.
3. Create the layout of §3. Keep lib/ and commands/ free of this project's names. Map every existing script or Makefile target to a verb of §5; keep old entry points as thin wrappers that call the new verb and print a deprecation note.
4. Level 1 first: dev mode without external services (§7) using the highest-preference provider that preserves behaviour, the shared behaviour of §8, configuration (§12), process management (§13). Prove it with the fresh-clone container test.
5. Level 2: tools.lock, fetch and install hooks per dependency set, prereqs, bundle create/verify/install for each TARGETS_OS, uninstall. Prove it with the networking-disabled container test for every target OS.
6. Level 3: image verbs, the enabled deployment targets with plan/apply/status/rollback/destroy, upgrade and rollback, support-bundle. Prove each target with plan-is-clean and apply-then-rollback tests.
7. Add the CI jobs of §15 for the level, and generate the documentation of §17.

Rules: never reach the network outside lib/net.sh; never call sudo silently; never write secrets to the manifest, a bundle or a log; do not change application behaviour; small commits, one verb or provider per commit.

Final report: the manifest; the verb mapping table (old → new); the conformance checklist of Appendix B with evidence per line; bundle size per target and option; what still needs root or network and why; deviations from the spec and the reason for each.
```
