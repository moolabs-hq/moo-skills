#!/usr/bin/env bash
# Thin wrapper — delegate to shared/install.sh. Lets customers run `./install.sh`
# at the suite root instead of needing to know it lives in shared/.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/shared/install.sh" "$@"
