#!/usr/bin/env bash
# Deploys the working tree (not `main`) to the development namespace "autonomous-cfo-dev".
#
# The manifests in deploy/ are written for the namespace "autonomous-cfo". This script re-targets them on the
# fly (namespace, image paths, in-cluster service host names) and builds the three images from the local
# directories with binary builds, so nothing has to be pushed first. It only ever talks to
# "autonomous-cfo-dev": every `oc` call names that namespace explicitly.
#
# The development stack is complete and separate: its own PostgreSQL, MCP server, OGX server, backend and
# frontend. Only the model itself (vLLM "cfo-llm" in the namespace "cfo-models") is shared, read-only.
#
# Prereq: `oc login ...`. Run from the repository root:
#   bash deploy/deploy-dev.sh            # everything
#   bash deploy/deploy-dev.sh infra      # database, OGX server and manifests only (no image builds)
#   bash deploy/deploy-dev.sh images     # rebuild and roll out the three images only
set -euo pipefail

NS="autonomous-cfo-dev"
PROD_NS="autonomous-cfo"
STEP="${1:-all}"

cd "$(dirname "$0")/.."

# Re-target a production manifest to the development namespace.
retarget() {
  sed -e "s|namespace: ${PROD_NS}\$|namespace: ${NS}|" \
      -e "s|namespace: ${PROD_NS}\( *#\)|namespace: ${NS}\1|" \
      -e "s|svc:5000/${PROD_NS}/|svc:5000/${NS}/|" \
      -e "s|\.${PROD_NS}\.svc|.${NS}.svc|g" "$@"
}

apply() {
  retarget "$@" | oc apply -n "$NS" -f -
}

setup_database() {
  echo "==> Database (PostgreSQL, schema, bank statement, MCP role) in $NS"
  NS="$NS" bash db/setup.sh
  NS="$NS" bash db/setup_mcp_user.sh
}

setup_ogx() {
  echo "==> OGX server in $NS"
  apply deploy/ogx-base-config.yaml
  apply deploy/ogx.yaml
}

apply_manifests() {
  echo "==> MCP server, backend and frontend manifests in $NS"
  apply deploy/mcp-server.yaml
  apply deploy/backend.yaml
  apply deploy/frontend.yaml
}

build_images() {
  echo "==> Building the images from the working tree"
  local build_dir
  build_dir=$(mktemp -d)
  trap 'rm -rf "$build_dir"' RETURN
  rsync -a --exclude __pycache__ --exclude .pytest_cache --exclude tests backend mcp_server "$build_dir/"
  rsync -a --exclude node_modules --exclude dist --exclude test-results --exclude e2e frontend "$build_dir/"
  # The nginx proxy target is baked into the frontend image: point it at the backend of this namespace.
  retarget -i "$build_dir/frontend/nginx-default-cfg/api.conf"
  oc start-build amalfi-mcp -n "$NS" --from-dir="$build_dir" --follow --wait
  oc start-build autonomous-cfo-backend -n "$NS" --from-dir="$build_dir" --follow --wait
  oc start-build autonomous-cfo -n "$NS" --from-dir="$build_dir" --follow --wait
  for deployment in amalfi-mcp autonomous-cfo-backend autonomous-cfo; do
    oc rollout status -n "$NS" "deployment/$deployment" --timeout=300s
  done
}

oc get project "$NS" >/dev/null

case "$STEP" in
  all)
    setup_database
    setup_ogx
    apply_manifests
    build_images
    ;;
  infra)
    setup_database
    setup_ogx
    apply_manifests
    ;;
  images)
    build_images
    ;;
  *)
    echo "Usage: bash deploy/deploy-dev.sh [all|infra|images]" >&2
    exit 2
    ;;
esac

echo "==> Done: https://$(oc get route autonomous-cfo -n "$NS" -o jsonpath='{.spec.host}')"
