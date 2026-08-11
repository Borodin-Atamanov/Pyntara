#!/usr/bin/env bash
set -euo pipefail

# Deploy the System Metrics Google Drive web app and keep its URL stable.
# The repository JS file is copied into a temporary build directory
# together with the Apps Script manifest and the clasp project file, then
# pushed to the existing script project (script ID) and the existing
# deployment is updated (deployment ID), so the web app URL does not
# change. The temporary directory guarantees that push replaces the cloud
# project only with these two files.
#
# Usage:
#   deploy_google_script.sh SCRIPT_ID DEPLOYMENT_ID
# or with environment variables:
#   GOOGLE_SCRIPT_ID=... GOOGLE_DEPLOYMENT_ID=... deploy_google_script.sh
#
# One-time setup: npm install -g @google/clasp, enable the Apps Script API
# at script.google.com/home/usersettings, run clasp login once. The script
# ID lives in the Apps Script editor under Project Settings, the deployment
# ID under Deployments; the deployment URL is stored in the google_script_key
# entry of the vault databases.

SCRIPT_FILE="$(cd "$(dirname "$0")" && pwd)/google_drive_script.js"

script_id="${1:-${GOOGLE_SCRIPT_ID:-}}"
deployment_id="${2:-${GOOGLE_DEPLOYMENT_ID:-}}"

fail() {
  echo "error: $*" >&2
  exit 1
}

if ! command -v clasp >/dev/null 2>&1; then
  fail "clasp is not installed; run: npm install -g @google/clasp"
fi

if [[ ! -f "$HOME/.clasprc.json" ]]; then
  fail "clasp is not logged in; run: clasp login"
fi

[[ -n "$script_id" ]] \
  || fail "missing script ID; pass it as the first argument or set GOOGLE_SCRIPT_ID"
[[ -n "$deployment_id" ]] \
  || fail "missing deployment ID; pass it as the second argument or set GOOGLE_DEPLOYMENT_ID"
[[ -f "$SCRIPT_FILE" ]] \
  || fail "script file not found: $SCRIPT_FILE"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

cp "$SCRIPT_FILE" "$workdir/Code.gs"
cat > "$workdir/appsscript.json" <<'EOF'
{
  "timeZone": "UTC+3",
  "dependencies": {},
  "webapp": {
    "access": "ANYONE_ANONYMOUS",
    "executeAs": "USER_DEPLOYING"
  },
  "exceptionLogging": "STACKDRIVER"
}
EOF
printf '{"scriptId":"%s"}\n' "$script_id" > "$workdir/.clasp.json"

(
  cd "$workdir"
  clasp push -f
  description="deploy $(date -u +%Y-%m-%d-%H-%M-%S)"
  # clasp 3.x renamed deploy to create-deployment; detect the available
  # command instead of guessing the installed major version.
  if clasp --help 2>&1 | grep -q "create-deployment"; then
    clasp create-deployment -d "$description" -i "$deployment_id"
  else
    clasp deploy -d "$description" -i "$deployment_id"
  fi
)

echo "deployed: code pushed and the existing deployment updated, URL unchanged"
