#!/bin/bash
# Runs `careeros scan` on a schedule via launchd.
#
# To use: create a LaunchAgent plist under ~/Library/LaunchAgents pointing at
# this script, with the schedule you want, and `launchctl load -w` it.
# Only fires while the Mac is powered on and logged in -- a per-user
# LaunchAgent does not run if the machine is off or logged out. A "runs even
# when off" setup would need a cloud-hosted agent with the DB made
# cloud-accessible, which this project doesn't attempt.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "=== Scan started: $(date) ==="
"$SCRIPT_DIR/.venv/bin/careeros" scan --notify
echo "=== Scan finished: $(date) ==="
echo ""
