# ADR-0022: Active Directory via LDAPS/OIDC with group -> role/team mapping; HTTPS from PEM everywhere
Date: 2026-10-23   Status: accepted

## Context
Product requirement: the server runs on Linux over HTTPS from a PEM certificate, and people sign in with their Windows NT
(Active Directory) accounts, with AD groups driving access.

## Decision
* Two identity providers, usable together per server: **LDAPS** (`ldap3`; service-account search, password check by a simple
  bind as the user, CA from `TT_LDAP_CA_BUNDLE`, empty passwords refused because AD treats them as anonymous binds) and
  **OIDC** (authorization-code + PKCE, ID token verified against the JWKS: signature, `iss`, `aud`, `exp`, `nonce`).
* Group membership is read at login and by a nightly job (`idp.sync_directory`, 02:30). Nested groups are expanded
  breadth-first over `memberOf` instead of AD's `LDAP_MATCHING_RULE_IN_CHAIN`: it works on any LDAP server, is testable
  against an in-memory directory, and is capped at 2 000 groups.
* `idp_group_mappings` map a group to a role (any scope) and/or a team. Everything derived from groups is stamped
  `source="idp"` on `RoleAssignment` / `Membership`; sync adds and removes only those rows, so a manual grant is never
  touched, and a manual grant equal to a mapped one is left as manual.
* Provisioning: an identity may sign in when it is already a member (an `invited` one is activated), is in a mapped group, or
  its e-mail domain is on `workspace.settings.auth.allowed_domains`; otherwise an `access_request` review row is left and the
  login gets 403. `@sAMAccountName` is the default handle; collisions get a numeric suffix and a `handle_collision` row.
* Sessions: `itsdangerous` signed cookie (HttpOnly, SameSite=Lax, `Secure` over HTTPS), 12 h sliding, double-submit CSRF
  for cookie-authenticated writes; bearer tokens are exempt. Failed LDAP logins are throttled (5 per 5 min per account).
* Transport: `tt-admin serve` runs uvicorn with `--ssl-certfile/--ssl-keyfile` from PEM; an optional second listener answers
  plain HTTP with `308`; `TT_FORCE_HTTPS` redirects and always sets HSTS on HTTPS responses. Clients verify the chain with
  system roots plus `TT_CA_BUNDLE`; `tt login` refuses to continue on a failed chain and has no "insecure" switch.

## Consequences
Kerberos/SPNEGO single sign-on, certificate hot-reload on SIGHUP and Postgres `verify-full` are specified in the plan but
not built in this phase (see `docs/security/active-directory.md` and `tls.md`, "Not yet"). The LDAPS path is tested against
ldap3's in-memory server and the TLS path against a throw-away CA, not against a real domain controller.
