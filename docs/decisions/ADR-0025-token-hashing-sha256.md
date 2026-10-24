# ADR-0025: API tokens are hashed with SHA-256, not a password hash
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
Tokens are 256-bit random secrets stored as `sha256(token)`; lookup is by hash.

## Why
Tokens are not human-chosen, so stretching adds nothing against guessing, while an argon2 verify on every request would cost tens of milliseconds on the capture hot path and prevent indexed lookup.

## Consequences
A database leak exposes hashes only; they cannot be reversed. Passwords (none are stored; sign-in is OIDC/LDAP) would need a password hash.
