#!/usr/bin/env bash
# Pull the latest server image and restart if it changed.
# Usage: server-update.sh <compose dir>
set -euo pipefail

dir="${1:?usage: $0 <compose dir>}"
cd "$dir"
docker compose pull
docker compose up -d
docker image prune -f
