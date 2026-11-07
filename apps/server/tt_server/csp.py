"""Content-Security-Policy builders for the HTML shells the API serves (deck, page mode, share links)."""

import re
import secrets

_SCRIPT_TAG = re.compile(r"<script(?=[\s>])")


def new_nonce() -> str:
    return secrets.token_urlsafe(16)


def html_csp(nonce: str | None = None, *, frame_ancestors: str = "'none'") -> str:
    """No remote origins and no `'unsafe-inline'` for scripts: inline scripts must carry the per-response nonce.

    `style-src` keeps `'unsafe-inline'` because the bundle styles elements through style attributes (ADR-0041).
    """
    script = f"script-src 'self' 'nonce-{nonce}'" if nonce else "script-src 'self'"
    return (
        f"default-src 'none'; {script}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        f"font-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        f"frame-ancestors {frame_ancestors}"
    )


def add_nonce(html: str, nonce: str) -> str:
    """Stamp every `<script>` start tag of `html` with `nonce`."""
    return _SCRIPT_TAG.sub(f'<script nonce="{nonce}"', html)
