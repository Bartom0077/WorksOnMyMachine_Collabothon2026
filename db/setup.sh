#!/usr/bin/env bash
# Deploys PostgreSQL ("amalfi") to OpenShift, creates the schema and imports the bank statement CSV.
# Prereq: `oc login ...` done. Run from repo root:  bash db/setup.sh
set -euo pipefail

NS="${NS:-autonomous-cfo}"
CSV="${CSV:-Challenge/pizzeria-amalfi-bank-statement-2025-v6.csv}"

oc get project "$NS" >/dev/null 2>&1 || oc new-project "$NS"

echo "==> Deploying PostgreSQL"
oc apply -n "$NS" -f deploy/postgres.yaml
oc rollout status -n "$NS" deployment/amalfi-db --timeout=300s

POD=$(oc get pod -n "$NS" -l app=amalfi-db -o jsonpath='{.items[0].metadata.name}')
echo "==> Waiting for pod $POD to accept connections"
until oc exec -n "$NS" "$POD" -- pg_isready -q -h localhost; do sleep 2; done

echo "==> Copying files into pod"
# Stream via stdin instead of `oc cp` (avoids Git Bash path mangling of "pod:/path" on Windows)
oc exec -i -n "$NS" "$POD" -- sh -c 'cat > /tmp/statement.csv' < "$CSV"
oc exec -i -n "$NS" "$POD" -- sh -c 'cat > /tmp/schema.sql' < db/schema.sql
oc exec -i -n "$NS" "$POD" -- sh -c 'cat > /tmp/import_transactions.sql' < db/import_transactions.sql

echo "==> Creating schema and importing transactions"
# The app user owns the DB, so run as it (env vars come from the Secret).
oc exec -n "$NS" "$POD" -- bash -c \
  'PGPASSWORD="$POSTGRESQL_PASSWORD" psql -h localhost -U "$POSTGRESQL_USER" -d "$POSTGRESQL_DATABASE" -v ON_ERROR_STOP=1 -f /tmp/schema.sql -f /tmp/import_transactions.sql'

echo "==> Done. Connect locally with:"
echo "    oc port-forward -n $NS svc/amalfi-db 5432:5432"
echo "    psql postgresql://amalfi:<password>@localhost:5432/amalfi"
