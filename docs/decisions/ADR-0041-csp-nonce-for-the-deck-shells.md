# ADR-0041: Deck shells use a per-response CSP nonce, and the builder may frame its own preview
Date: 2026-10-25   Status: accepted (Phase 13 R1.2, R3.7; review T13, UX 2)

## Context
The deck page is a single-file bundle with inline script, served by `/p/{ws}/{id}` and `/p/t/{token}`. Its CSP carried
`script-src 'self' 'unsafe-inline'` (any injected markup could run) and `frame-ancestors 'none'`, which also blocked the
builder's own live-preview iframe.

## Decision
* `tt_server/csp.py` builds the policy. Every shell response gets a fresh `nonce` (`secrets.token_urlsafe`); every
  `<script>` start tag of the bundle and of the boot snippet is stamped with it; `script-src 'self' 'nonce-…'`, no
  `'unsafe-inline'` for scripts.
* `style-src` keeps `'unsafe-inline'`: the renderers set `style=` attributes (bar widths, heat-map colours). Removing it
  means rewriting those as classes or CSSOM calls; recorded as a follow-up, not a regression.
* The session shell (`/p/{ws}/{id}`) sends `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN` so the builder can
  preview; the share-link shell (`/p/t/{token}`) stays `'none'`/`DENY`.
* The exported HTML file is opened from disk, outside any server policy; it is unchanged.

## Consequences
Tests assert a nonce on every script tag, no `unsafe-inline` in `script-src`, a different nonce per response, and
`'none'` for share links. The bundle must keep `<script` only as tag openers (a test guards it).
