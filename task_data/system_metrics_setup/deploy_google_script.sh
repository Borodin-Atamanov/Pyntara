#!/usr/bin/env bash
set -euo pipefail

# Deploy the System Metrics Google Drive web app and keep its URL stable.
# The repository JS file is rendered into a temporary build directory
# together with the Apps Script manifest and the clasp project file, then
# pushed to the existing script project and the existing deployment is
# updated, so the web app URL does not change. The temporary directory
# guarantees that push replaces the cloud project only with these two
# files.
#
# secrets/read_google_script_credentials.py, run with the project
# interpreter, renders the web app file and prints the two identifiers. It
# reads both vault databases: the production vault supplies the script ID
# (the username field of the google_script_key entry) and the deployment ID
# (extracted from the url field), and every vault supplies an auth key (its
# password field). Every key lands in the rendered file, so the deployed app
# accepts the telemetry of the machines provisioned from either vault. The
# keys are never printed: the helper writes them into the rendered file and
# reports only the identifiers.
#
# Both vaults must open, each with the password of PYNTARA_VAULT_PASSWORD
# when that value opens it and otherwise with the .password file next to it.
#
# Usage:
#   deploy_google_script.sh [SCRIPT_ID DEPLOYMENT_ID]
#
# The two identifiers can also be passed as the positional arguments or
# through the GOOGLE_SCRIPT_ID and GOOGLE_DEPLOYMENT_ID environment
# variables; the auth keys always come from the vaults, because a deploy that
# accepted one key would drop the machines of the other vault.
#
# One-time setup: npm install -g @google/clasp, enable the Apps Script API
# at script.google.com/home/usersettings, run clasp login once, and fill
# the google_script_key entry of both vault databases (username: script ID,
# url: web app URL, password: auth key). The project dependencies must be
# installed (uv sync) because the vault reader runs on pykeepass.

SCRIPT_FILE="$(cd "$(dirname "$0")" && pwd)/google_drive_script.js"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CREDENTIALS_HELPER="$REPO_ROOT/secrets/read_google_script_credentials.py"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

script_id=""
deployment_id=""

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

[[ -f "$SCRIPT_FILE" ]] \
  || fail "script file not found: $SCRIPT_FILE"
[[ -x "$VENV_PYTHON" ]] \
  || fail "venv python not found at $VENV_PYTHON; run: uv sync"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# The helper renders the web app file from the repository template with the
# auth key of every vault and prints the two identifiers; the keys themselves
# never reach this script.
if ! credentials="$("$VENV_PYTHON" "$CREDENTIALS_HELPER" "$SCRIPT_FILE" "$workdir/Code.gs")"; then
  fail "cannot prepare the deploy from the vaults; see the helper output above"
fi
vault_script_id="$(printf '%s\n' "$credentials" | sed -n 's/^script_id=//p')"
vault_deployment_id="$(printf '%s\n' "$credentials" | sed -n 's/^deployment_id=//p')"
if [[ -z "$vault_script_id" || -z "$vault_deployment_id" ]]; then
  fail "the helper returned incomplete credentials"
fi

script_id="${1:-${GOOGLE_SCRIPT_ID:-$vault_script_id}}"
deployment_id="${2:-${GOOGLE_DEPLOYMENT_ID:-$vault_deployment_id}}"
[[ -n "$script_id" ]] \
  || fail "missing script ID; pass it as the first argument, set GOOGLE_SCRIPT_ID, or fill the google_script_key username in the production vault"
[[ -n "$deployment_id" ]] \
  || fail "missing deployment ID; pass it as the second argument, set GOOGLE_DEPLOYMENT_ID, or fill the google_script_key url in the production vault"

cat > "$workdir/appsscript.json" <<'EOF'
{
  "runtimeVersion": "V8",
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
