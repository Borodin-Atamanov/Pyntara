#!/usr/bin/env bash
set -euo pipefail

# Deploy the System Metrics Google Drive web app and keep its URL stable.
# The repository JS file is copied into a temporary build directory
# together with the Apps Script manifest and the clasp project file, then
# pushed to the existing script project and the existing deployment is
# updated, so the web app URL does not change. The temporary directory
# guarantees that push replaces the cloud project only with these two
# files.
#
# The script ID and the deployment ID come from the first available
# source: the two positional arguments, then the GOOGLE_SCRIPT_ID and
# GOOGLE_DEPLOYMENT_ID environment variables, then the vault databases.
# The vault source is read by secrets/read_google_script_credentials.py
# with the project interpreter: the script ID lives in the username field
# of the google_script_key entry, the deployment ID is extracted from the
# url field; PYNTARA_VAULT_SOURCE (production or default) selects the
# vault, production is tried first by default.
#
# Usage:
#   deploy_google_script.sh [SCRIPT_ID DEPLOYMENT_ID]
#
# One-time setup: npm install -g @google/clasp, enable the Apps Script API
# at script.google.com/home/usersettings, run clasp login once, and fill
# the google_script_key entry of the vault databases (username: script ID,
# url: web app URL, password: shared auth key). The project dependencies
# must be installed (uv sync) because the vault reader runs on pykeepass.

SCRIPT_FILE="$(cd "$(dirname "$0")" && pwd)/google_drive_script.js"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CREDENTIALS_HELPER="$REPO_ROOT/secrets/read_google_script_credentials.py"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

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

if [[ -z "$script_id" || -z "$deployment_id" ]]; then
  # The arguments or the environment did not provide both IDs; read them
  # from the vault databases through the project interpreter.
  if [[ ! -x "$VENV_PYTHON" ]]; then
    fail "venv python not found at $VENV_PYTHON; run: uv sync"
  fi
  if ! credentials="$("$VENV_PYTHON" "$CREDENTIALS_HELPER")"; then
    fail "cannot read Google script credentials from the vault; see the reader output above"
  fi
  vault_script_id="$(printf '%s\n' "$credentials" | sed -n 's/^script_id=//p')"
  vault_deployment_id="$(printf '%s\n' "$credentials" | sed -n 's/^deployment_id=//p')"
  if [[ -z "$vault_script_id" || -z "$vault_deployment_id" ]]; then
    fail "the vault reader returned incomplete credentials"
  fi
  [[ -n "$script_id" ]] || script_id="$vault_script_id"
  [[ -n "$deployment_id" ]] || deployment_id="$vault_deployment_id"
fi

[[ -n "$script_id" ]] \
  || fail "missing script ID; pass it as the first argument, set GOOGLE_SCRIPT_ID, or fill the google_script_key username in the vault"
[[ -n "$deployment_id" ]] \
  || fail "missing deployment ID; pass it as the second argument, set GOOGLE_DEPLOYMENT_ID, or fill the google_script_key url in the vault"
[[ -f "$SCRIPT_FILE" ]] \
  || fail "script file not found: $SCRIPT_FILE"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

cp "$SCRIPT_FILE" "$workdir/Code.gs"
cat > "$workdir/appsscript.json" <<'EOF'
{
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
