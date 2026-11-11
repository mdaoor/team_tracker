# Helm chart (`deploy/helm/team-tracker`)

Components: `server` (API, 2 replicas, readiness `/readyz`), `worker` (Procrastinate), `web` (nginx + built SPA), optional `gateway` (LLM), a pre-install/upgrade
**migrate Job** (`tt-admin migrate`), PVC for attachments, Ingress, optional ServiceMonitor. The presentation viewer is part of the server image.

```
kubectl create secret generic team-tracker-secrets \
  --from-literal=TT_DATABASE_URL='postgresql+psycopg://tt:***@pg:5432/tt?sslmode=verify-full' \
  --from-literal=TT_SESSION_SECRET=$(openssl rand -hex 32) --from-literal=TT_SECRET_KEY=$(openssl rand -hex 32) \
  --from-literal=TT_METRICS_TOKEN=$(openssl rand -hex 24)
helm upgrade --install tt deploy/helm/team-tracker -f my-values.yaml
```
Notes: secrets only by reference; pods run as non-root with a read-only root filesystem; TLS is terminated at the ingress by default (`TT_FORCE_HTTPS` +
`TT_TRUST_FORWARDED_PROTO`) or mount a PEM secret (`tls.secretName`) and run `tt-admin serve`; `ReadWriteMany` is required for attachments with more than one server replica
(or use one replica / an object-store adapter). Blue/green: install the new release under another name against the same database after `migrate` (migrations are additive),
switch the ingress, then remove the old release.

**Status:** authored without `helm`/`kind` in the build environment. The CI `helm` job runs `helm lint` and a server-side dry run on a kind cluster; until it is green the chart is unverified.
