# ADR-0035: Nested Active Directory groups are expanded client-side
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
Group membership is expanded by breadth-first search over `memberOf` in the LDAP provider rather than `LDAP_MATCHING_RULE_IN_CHAIN`.

## Why
The matching-rule OID is AD-only and unavailable on the in-memory test directory and on OpenLDAP; the BFS is portable and testable.

## Consequences
More round trips on deep trees (bounded depth and visited set).
