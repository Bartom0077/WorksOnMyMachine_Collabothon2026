#!/usr/bin/env bash
# Creates the read-only PostgreSQL role used by the MCP data server (mcp_server/) and stores its
# credentials in the OpenShift secret "amalfi-mcp-db". Idempotent: reuses the password if the secret exists.
# The password is generated here and never written to the repo.
# Prereq: `oc login ...`, db/setup.sh already run. Run from repo root:  bash db/setup_mcp_readonly_user.sh
set -euo pipefail

NS="${NS:-autonomous-cfo}"
ROLE="${ROLE:-amalfi_mcp}"
SECRET="amalfi-mcp-db"

if oc get secret "$SECRET" -n "$NS" >/dev/null 2>&1; then
  echo "==> Reusing password from secret $SECRET"
  PASSWORD=$(oc get secret "$SECRET" -n "$NS" -o jsonpath='{.data.database-password}' | base64 -d)
else
  echo "==> Generating password and creating secret $SECRET"
  PASSWORD=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  oc create secret generic "$SECRET" -n "$NS" \
    --from-literal=database-user="$ROLE" \
    --from-literal=database-password="$PASSWORD" \
    --from-literal=database-name=amalfi
  oc label secret "$SECRET" -n "$NS" app=amalfi-mcp >/dev/null
fi

POD=$(oc get pod -n "$NS" -l app=amalfi-db -o jsonpath='{.items[0].metadata.name}')
echo "==> Creating/updating role $ROLE in pod $POD"
# SQL goes through stdin so the password never shows up in a process list.
oc exec -i -n "$NS" "$POD" -- psql -v ON_ERROR_STOP=1 -q -d amalfi <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${ROLE}') THEN
    CREATE ROLE ${ROLE} LOGIN;
  END IF;
END
\$\$;
ALTER ROLE ${ROLE} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '${PASSWORD}';
ALTER ROLE ${ROLE} SET default_transaction_read_only = on;
ALTER ROLE ${ROLE} SET statement_timeout = '10s';
GRANT CONNECT ON DATABASE amalfi TO ${ROLE};
GRANT USAGE ON SCHEMA public TO ${ROLE};
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ${ROLE};
GRANT SELECT ON bank_transactions, kpi_snapshots, fixed_obligations, account_balance_anchors TO ${ROLE};
SQL

echo "==> Done. Role $ROLE can only SELECT from the data tables (not agent_proposals)."
