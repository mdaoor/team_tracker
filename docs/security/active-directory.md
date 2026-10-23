# Active Directory and OIDC sign-in

Team Tracker signs people in with their Windows NT (Active Directory) account over LDAPS, or through any OIDC provider
(Entra ID, ADFS, ...), and lets AD groups grant roles and team membership. Design rationale: ADR-0022.

## What you need

| | |
|---|---|
| A service account | read-only; used to search for users and read `memberOf` |
| Domain controller reachable on `636` | or `389` with StartTLS (`TT_LDAP_START_TLS=true`) |
| The DCs' CA as a PEM bundle | `TT_LDAP_CA_BUNDLE=/etc/tt/ca.pem`; validation is always on (`CERT_REQUIRED`) |

```env
TT_LDAP_URL=ldaps://dc1.corp.example.com:636
TT_LDAP_CA_BUNDLE=/etc/tt/ca.pem
TT_LDAP_BIND_DN=CN=svc-tt,OU=Service,DC=corp,DC=example,DC=com
TT_LDAP_BIND_PASSWORD=...
TT_LDAP_BASE_DN=DC=corp,DC=example,DC=com
```

## Signing in

* **Web / API**: `POST /auth/ldap/login {workspace, username, password}` sets the session cookie. `username` may be
  `jdoe`, `CORP\jdoe` or `jdoe@corp.example.com` (matched on `sAMAccountName` or `userPrincipalName`).
* **CLI**: `tt login --url https://tracker.corp.example.com --workspace acme --ldap-user CORP\\jdoe` prompts for the password,
  exchanges it once at `POST /auth/ldap/token` for a `capture`+`read` API token and stores only the token.
  `tt login --browser` instead opens the web console, signs in there, and receives the token on a one-shot `127.0.0.1` listener.
* **OIDC**: `GET /auth/login?workspace=acme` -> provider -> `GET /auth/callback`. Set `TT_OIDC_ISSUER`, `TT_OIDC_CLIENT_ID`,
  `TT_OIDC_CLIENT_SECRET`, `TT_OIDC_REDIRECT_URI`; the `groups` claim (`TT_OIDC_GROUPS_CLAIM`) plays the role of `memberOf`.
  The ID token is verified (signature against the provider's JWKS, `iss`, `aud`, `exp`, `nonce`; PKCE S256).

Passwords are checked by a simple bind **as the user**; an empty password is refused before it reaches the directory (AD would treat
it as an anonymous bind). Five failed attempts for an account within five minutes answer `429`.

## Who may sign in

An identity is let in when it is (a) already a member of the workspace (an `invited` member is activated), (b) in at least one
mapped group, or (c) from an e-mail domain on `workspace.settings.auth.allowed_domains`. Anyone else gets `403 pending_approval`
and one open `access_request` row in the review queue for the admins. New members get `@<sAMAccountName>` (lower case); a clash
gets a numeric suffix and a `handle_collision` review row. People are matched across providers by verified e-mail.

## Mapping groups to roles and teams

```http
POST /api/v1/w/acme/idp/mappings
{"provider": "ldap", "group": "CN=TT-Platform-Leads,OU=Groups,DC=corp,DC=example,DC=com",
 "role": "manager", "scope_type": "team", "scope_id": "platform", "team": "platform"}
```

The group is compared case-insensitively against the user's `memberOf` **including nested groups** (expanded breadth-first,
capped at 2 000). A mapping may grant a role (workspace, project, product or team scope) and/or add the person to a team.
Anything created this way is stamped `source="idp"`.

`POST /idp/sync` (and the nightly `idp.sync_directory` job at 02:30) re-reads every member's groups: roles and team memberships
that no longer have a mapped group behind them are removed, new ones are added, and anyone who has left the directory loses all
idp-derived access. Manual roles and memberships are never touched. `GET /idp/mappings`, `POST /idp/mappings`,
`DELETE /idp/mappings/{id}` need `role.admin`.

## Not yet

* **Kerberos / SPNEGO single sign-on** (`TT_KERBEROS_KEYTAB`) is specified in the plan but not implemented; users type their
  password (or use `tt login --browser` with OIDC).
* No real domain controller or OpenLDAP container is exercised in CI. The LDAP code runs against ldap3's in-memory server
  (login, nested groups, mapping, sync, collisions, throttling); verify against your DC with a staging workspace before rollout.
* `LDAP_MATCHING_RULE_IN_CHAIN` is not used; nesting is expanded client-side, which costs one query per group on large trees.
* The Setup -> People & teams -> "Identity provider" screen arrives with Phase 5; until then use the endpoints above.
