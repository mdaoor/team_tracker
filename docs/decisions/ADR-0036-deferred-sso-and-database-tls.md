# ADR-0036: Kerberos SSO, Postgres `verify-full` and certificate hot-reload are scheduled work, not waived
Date: 2026-10-24   Status: accepted

## Decision
ADR-0022 deferred these three items. The product owner has not confirmed a waiver, and NT (Kerberos/SPNEGO) sign-in is a stated requirement, so they are **not** waived: they are built in review package I (`plans/` P4 §4.6) behind configuration:
* Kerberos/SPNEGO behind a reverse proxy that validates the ticket and forwards the principal in a trusted header (`TT_TRUSTED_PRINCIPAL_HEADER`, honoured only from configured proxy addresses), mapped to a member by UPN/e-mail like the LDAP path;
* Postgres `sslmode=verify-full` with `TT_DB_SSLROOTCERT`, and service-to-service TLS settings documented in `docs/security/tls.md`;
* certificate reload on SIGHUP and when the PEM files' mtime changes.

## Open question for the product owner
Is a proxy-terminated Kerberos flow acceptable, or is in-process GSSAPI (`python-gssapi`, keytab on the app host) required?
