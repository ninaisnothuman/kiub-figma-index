#!/usr/bin/env bash
# Daily refresh + drift detection for the Kiub figma index.
# Add to crontab with: `crontab -e` then:
#
#   0 9 * * * /Users/francisotuogbai/.openclaw/workspace/kiub-figma-index/scripts/cron.sh >> /tmp/kiub-figma-index.log 2>&1
#
# What it does:
#   1. Pulls latest overlay edits from GitHub
#   2. Refreshes the lookup index from live Figma state
#   3. Regenerates README descriptors (.json + .js + plugin manifest)
#   4. Commits and pushes any regenerated files (so the plugin sees fresh data)
#   5. Runs drift detection and posts to Slack if drift exists
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

# Drift report (exits non-zero on drift; --slack posts to the channel in secrets.json)
python3 scripts/sync.py drift --slack
