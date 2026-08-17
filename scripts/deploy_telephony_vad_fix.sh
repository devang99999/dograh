#!/usr/bin/env bash
# Deploy Gemini Live telephony VAD fix to a self-hosted Dograh VM.
#
# Fixes missed Hindi replies on 8 kHz Plivo audio by disabling Gemini server VAD
# on telephony and driving activity windows from local Silero instead.
#
# Usage (on the VM, in the dograh install directory):
#   cd ~/dograh && bash scripts/deploy_telephony_vad_fix.sh
#
# Usage (from your laptop via SSH — needs key access to the VM):
#   bash scripts/deploy_telephony_vad_fix.sh root@34.14.214.236 ~/dograh
#
# Build-mode installs (docker-compose.override.yaml present): rebuilds api image.
# Prebuilt installs (official images only): hot-patches the running api container.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PATCH_FILES=(
  api/services/pipecat/service_factory.py
  api/services/pipecat/run_pipeline.py
)

log() { echo -e "${BLUE}$*${NC}"; }
ok() { echo -e "${GREEN}$*${NC}"; }
warn() { echo -e "${YELLOW}$*${NC}"; }
fail() { echo -e "${RED}$*${NC}" >&2; exit 1; }

remote_deploy() {
  local ssh_target=$1
  local remote_dir=$2
  log "Copying patched files to ${ssh_target}:${remote_dir} ..."
  for f in "${PATCH_FILES[@]}"; do
    scp "${REPO_ROOT}/${f}" "${ssh_target}:${remote_dir}/${f}"
  done
  scp "$0" "${ssh_target}:${remote_dir}/scripts/deploy_telephony_vad_fix.sh"
  log "Running deploy on remote ..."
  ssh "$ssh_target" "cd $(printf '%q' "$remote_dir") && bash scripts/deploy_telephony_vad_fix.sh --local"
}

local_deploy() {
  local dir=${1:-$REPO_ROOT}
  cd "$dir"

  [[ -f docker-compose.yaml ]] || fail "docker-compose.yaml not found in $(pwd)"

  for f in "${PATCH_FILES[@]}"; do
    [[ -f "$f" ]] || fail "Missing patch file: $f (run from repo root or scp files first)"
  done

  local compose=(docker compose --profile remote)
  if ! docker compose version >/dev/null 2>&1; then
    compose=(sudo docker compose --profile remote)
  fi

  if [[ -f docker-compose.override.yaml ]] && grep -q 'build:' docker-compose.override.yaml 2>/dev/null; then
    ok "Build-mode install detected — rebuilding api image ..."
    "${compose[@]}" build api
    "${compose[@]}" up -d api
  else
    warn "Prebuilt install — hot-patching running api container ..."
    local cid
    cid=$("${compose[@]}" ps -q api 2>/dev/null || true)
    [[ -n "$cid" ]] || fail "api container not running. Start stack first: ./remote_up.sh"

    for f in "${PATCH_FILES[@]}"; do
      docker cp "$f" "${cid}:/app/${f}"
      ok "  patched /app/${f}"
    done
    "${compose[@]}" restart api
  fi

  log "Waiting for api health ..."
  sleep 8
  if "${compose[@]}" ps api 2>/dev/null | grep -qE 'Up|running'; then
    ok "api container is up."
  else
    warn "Check logs: ${compose[*]} logs api --tail 80"
  fi

  ok "Telephony VAD fix deployed. Place a test call — Hindi replies after Neha asks a question should land without saying hello twice."
}

if [[ "${1:-}" == "--local" ]]; then
  local_deploy "$REPO_ROOT"
elif [[ $# -ge 1 && "$1" == *@* ]]; then
  remote_dir=${2:-~/dograh}
  remote_deploy "$1" "$remote_dir"
elif [[ -f "$REPO_ROOT/docker-compose.yaml" ]]; then
  local_deploy "$REPO_ROOT"
else
  cat <<EOF
Deploy Gemini Live telephony VAD fix to Dograh VM.

From laptop (SSH):
  bash scripts/deploy_telephony_vad_fix.sh USER@34.14.214.236 ~/dograh

On the VM directly:
  cd ~/dograh
  git pull    # if using build mode with this repo
  bash scripts/deploy_telephony_vad_fix.sh

Manual hot-patch (prebuilt image, no repo on VM):
  scp api/services/pipecat/service_factory.py USER@HOST:/tmp/
  scp api/services/pipecat/run_pipeline.py USER@HOST:/tmp/
  ssh USER@HOST
  cd ~/dograh
  CID=\$(docker compose --profile remote ps -q api)
  docker cp /tmp/service_factory.py \$CID:/app/api/services/pipecat/service_factory.py
  docker cp /tmp/run_pipeline.py \$CID:/app/api/services/pipecat/run_pipeline.py
  docker compose --profile remote restart api
EOF
  exit 1
fi
