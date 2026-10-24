"""Generate docs/ops/config-reference.md from `tt_server.settings.Settings` (the single source of truth for TT_* variables).

python tools/gen_config_reference.py            # rewrite the file
python tools/gen_config_reference.py --check    # exit 1 when the committed file is stale (CI)
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic_core import PydanticUndefined
from tt_server.settings import Features, Settings

OUT = Path(__file__).resolve().parents[1] / "docs" / "ops" / "config-reference.md"
SECRET = ("secret", "password", "token", "key")


def _default(f) -> str:  # type: ignore[no-untyped-def]
    if f.default is not PydanticUndefined and f.default is not None:
        return f"`{f.default}`"
    if f.default_factory is not None:
        return "(nested)"
    return "unset"


def render() -> str:
    rows = []
    for name, f in Settings.model_fields.items():
        if name == "features":
            continue
        env = "TT_" + name.upper()
        kind = "secret" if any(s in name for s in SECRET) and "ttl" not in name else ""
        rows.append(
            (
                env,
                str(f.annotation)
                .replace("typing.", "")
                .replace("<class '", "")
                .replace("'>", "")
                .replace("|", "\\|"),
                _default(f),
                (f.description or "").replace("|", "\\|"),
                kind,
            )
        )
    feat = [(f"TT_FEATURES__{n.upper()}", _default(f), "") for n, f in Features.model_fields.items()]
    out = [
        "# Configuration reference",
        "",
        "Generated from `tt_server/settings.py` by `tools/gen_config_reference.py` - do not edit by hand (CI fails when it drifts).",
        "Variables are read from the environment (prefix `TT_`) or a `.env` file. Rows marked **secret** must come from a secret store, never from git.",
        "",
        "| Variable | Type | Default | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for env, kind_t, default, desc, secret in rows:
        note = (desc + (" **secret**" if secret else "")).strip()
        out.append(f"| `{env}` | `{kind_t}` | {default} | {note} |")
    out += ["", "## Feature flags (`TT_FEATURES__<NAME>`)", "", "| Variable | Default |", "| --- | --- |"]
    out += [f"| `{e}` | {d} |" for e, d, _ in feat]
    out += [
        "",
        "## Environment matrix",
        "",
        "| Concern | dev | staging / prod |",
        "| --- | --- | --- |",
        "| `TT_ENV` | `dev` | `prod` (startup validates secrets) |",
        "| `TT_DEV_AUTH` | allowed | refused at startup |",
        "| `TT_SESSION_SECRET` | any | >= 32 random bytes, required |",
        "| `TT_SECRET_KEY` | optional | required (encrypts stored integration credentials) |",
        "| `TT_DB_SSLMODE` | unset | `verify-full` with `TT_DB_SSLROOTCERT` |",
        "| `TT_METRICS_TOKEN` | `/metrics` open | required to enable `/metrics` |",
        "| TLS | plain HTTP | PEM pair (`TT_TLS_*`) or terminate at the proxy with `TT_TRUST_FORWARDED_PROTO` |",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print("docs/ops/config-reference.md is stale: run python tools/gen_config_reference.py")  # noqa: T201
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
