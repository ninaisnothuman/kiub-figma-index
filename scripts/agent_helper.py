#!/usr/bin/env python3
"""
agent_helper.py — Helpers for Claude / OpenClaw agents that edit Kiub Figma files.

The contract:
  Any agent that uses the Figma MCP `use_figma` tool to modify a Kiub Figma
  file MUST, as its FINAL action, call `use_figma` again with the JS produced
  by `print_sync_js <file_name>` to refresh the README from the latest YAML
  overlay. This keeps the in-Figma README current without waiting for the
  staleness watcher to nag a human.

Subcommands:
  print_sync_js <name>      Print the JS the agent should pass to use_figma.
                            <name> can be the file_name ("v0") or the overlay
                            stem ("v0", "landing-pages-v0", "cover-images").

  print_file_id <name>      Print the Figma fileKey for use_figma.

  print_instructions        Print the agent contract (paste into agent prompts).

Usage from inside an agent:
  1. agent does its Figma edit via use_figma fileKey=... code=...
  2. agent runs: python3 scripts/agent_helper.py print_sync_js v0
  3. agent passes the printed JS as the next use_figma call's `code` argument
     with fileKey from print_file_id

Example shell:
  FILE_ID=$(python3 scripts/agent_helper.py print_file_id v0)
  JS=$(python3 scripts/agent_helper.py print_sync_js v0)
  # then agent invokes use_figma with $FILE_ID and $JS
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FILES_DIR = ROOT / "files"
GENERATED_DIR = ROOT / "generated"


def find_overlay(name):
    candidates = []
    for path in sorted(FILES_DIR.glob("*.yaml")):
        overlay = yaml.safe_load(path.read_text())
        if path.stem == name or overlay["file_name"] == name or overlay["file_id"] == name:
            return path, overlay
        candidates.append(overlay["file_name"])
    print(f"!! no overlay matched '{name}'. Available: {', '.join(candidates)}", file=sys.stderr)
    sys.exit(2)


def cmd_print_sync_js(name):
    path, overlay = find_overlay(name)
    js_path = GENERATED_DIR / f"{path.stem}_readme.js"
    if not js_path.exists():
        print(f"!! generated JS missing: {js_path}. Run: python3 scripts/sync.py gen", file=sys.stderr)
        sys.exit(2)
    print(js_path.read_text())


def cmd_print_file_id(name):
    _, overlay = find_overlay(name)
    print(overlay["file_id"])


CONTRACT = """\
AGENT CONTRACT — Editing a Kiub Figma file

If your task involves modifying a Kiub Figma file via the use_figma MCP
tool, you MUST as your FINAL action also refresh the README page so it
stays in sync with the kiub-figma-index registry.

Steps:

  1. Do your Figma edit:
       use_figma(fileKey=..., code=<your edit JS>)

  2. As your final use_figma call, refresh the README:
       JS=$(python3 ~/.openclaw/workspace/kiub-figma-index/scripts/agent_helper.py print_sync_js <file_name>)
       FILE_ID=$(python3 ~/.openclaw/workspace/kiub-figma-index/scripts/agent_helper.py print_file_id <file_name>)
       use_figma(fileKey=$FILE_ID, code=$JS)

  3. If you ADDED new pages to the Figma file, ALSO open a small PR or
     ping #kiub-design noting the new page so the YAML overlay can be
     updated. Do not edit the YAML directly — let a human curate the
     descriptions for new pages.

If you skip step 2, the staleness watcher (scripts/sync.py watch) will
detect that the file's lastModified is newer than the README's last sync
and DM Francis on Slack within ~30 minutes. The watcher is the backstop;
self-syncing is the polite default.

Registered files (file_name → fileKey):
"""


def cmd_print_instructions():
    print(CONTRACT)
    for path in sorted(FILES_DIR.glob("*.yaml")):
        overlay = yaml.safe_load(path.read_text())
        print(f"  {overlay['file_name']:20s} → {overlay['file_id']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("print_sync_js")
    p1.add_argument("name")
    p2 = sub.add_parser("print_file_id")
    p2.add_argument("name")
    sub.add_parser("print_instructions")
    args = ap.parse_args()
    if args.cmd == "print_sync_js":
        cmd_print_sync_js(args.name)
    elif args.cmd == "print_file_id":
        cmd_print_file_id(args.name)
    elif args.cmd == "print_instructions":
        cmd_print_instructions()


if __name__ == "__main__":
    main()
