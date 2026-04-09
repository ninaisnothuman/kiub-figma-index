#!/usr/bin/env bash
# Refresh + drift + staleness check for the Kiub figma index.
#
# Recommended cron — every 30 min so the staleness watcher can catch agent
# edits within ~30 minutes:
#
#   */30 * * * * /Users/francisotuogbai/.openclaw/workspace/kiub-figma-index/scripts/cron.sh >> /tmp/kiub-figma-index.log 2>&1
#
# Or daily if you only care about overlay drift, not real-time staleness:
#
#   0 9 * * * /Users/francisotuogbai/.openclaw/workspace/kiub-figma-index/scripts/cron.sh >> /tmp/kiub-figma-index.log 2>&1
#
# What it does:
#   1. Pulls latest overlay edits from GitHub
#   2. Refreshes the lookup index from live Figma state
#   3. Regenerates README descriptors (.json + .js + plugin manifest)
#   4. Commits and pushes any regenerated files (so the plugin sees fresh data)
#   5. Runs drift detection (alerts when YAML overlay diverges from Figma pages)
#   6. Runs the stale-README watcher (DMs Francis when a Figma file has been
#      edited since the README was last synced — catches agent edits)
#
# Designers see Slack alerts when overlays drift from Figma. To apply README
# updates after editing a YAML, they open the file in Figma and run
# Plugins → Kiub README Sync (one click).

set -e
cd "$(dirname "$0")/.."

git pull --quiet --rebase --autostash || true

python3 scripts/build_index.py
python3 scripts/sync.py gen

# Commit + push regenerated artifacts only if they changed
if ! git diff --quiet generated/ index.json 2>/dev/null; then
  git add generated/ index.json
  git commit -q -m "auto: refresh generated/ + index.json [cron]" \
    --author="kiub-figma-index cron <noreply@kiub.ai>" || true
  git push --quiet origin main || echo "!! push failed"
fi

# Drift report (alerts when YAML overlay diverges from Figma pages)
python3 scripts/sync.py drift --slack || true

# Stale-README watcher (DMs Francis when Figma file has been edited since
# README was last synced — catches agent edits, with per-file dedupe)
python3 scripts/sync.py watch --slack
