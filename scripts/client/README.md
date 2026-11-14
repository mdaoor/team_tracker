# Team Tracker client kit

Installs the `tt` command line on a user's computer. Works offline: the `wheelhouse/` folder holds every package for
Linux, macOS and Windows (Python 3.12).

| System | Install | Sign in |
| --- | --- | --- |
| Linux / macOS | `./install.sh install --server https://tracker.example.com --workspace acme` | `./install.sh login --browser` |
| Windows (PowerShell) | `.\install.ps1 install -Server https://tracker.example.com -Workspace acme` | `.\install.ps1 login -Browser` |

* Internal CA: put it in `ca/ca.pem` before installing (or `--ca FILE` / `-Ca FILE`).
* Platforms in `wheelhouse/`: Linux x86_64, macOS on Apple silicon (arm64), Windows x64 (see `PLATFORMS`). Intel Macs
  are not covered offline: the locked `cryptography` release ships macOS wheels for arm64 only (online install works).
* Needs Python 3.12+ (or uv). `status` and `uninstall` do what they say; `-v` / `-Verbose` shows every step.
* Without network access to the server's sign-in page, ask your admin for an API token and use `--token`.
* Importing a ContextPack from a file (no server connection): `tt pack --import context-pack.json`.

Full guide: `docs/guide/08-clients.md`.
