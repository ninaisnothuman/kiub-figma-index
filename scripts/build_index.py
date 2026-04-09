#!/usr/bin/env python3
"""
build_index.py — Merge curated overlays + Figma API state into a flat index.json
that the find.py CLI (and a future Slack bot) can query.

Source data:
  - Curated overlays:   files/*.yaml          (hand-written: purpose, tags, status)
  - Live API state:     fetched fresh from api.figma.com via FIGMA_TOKEN
                        (file_name, last_modified, page_id list, frame_count,
                         a deeplink-able first-frame node id per page)

Output:
  - index.json          flat list of pages across all curated files, with
                        merged metadata, ranked-friendly text fields, and
                        Figma deep links.
"""

import json
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi
import yaml

ROOT = Path(__file__).resolve().parent.parent
FILES_DIR = ROOT / "files"
INDEX_PATH = ROOT / "index.json"
SECRETS = Path.home() / ".openclaw" / "secrets.json"
_SSL = ssl.create_default_context(cafile=certifi.where())


def figma_get(path):
    token = json.loads(SECRETS.read_text())["FIGMA_TOKEN"]
    req = urllib.request.Request(
        f"https://api.figma.com/v1/{path}",
        headers={"X-Figma-Token": token},
    )
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
        return json.loads(r.read().decode())


def fetch_file_state(file_id):
    """Returns {name, last_modified, pages: {page_id: {name, section, frame_count,
    first_frame_id, frame_names}}}."""
    data = figma_get(f"files/{file_id}?depth=2")
    pages = {}
    for canvas in data["document"]["children"]:
        if canvas.get("type") != "CANVAS":
            continue
        children = canvas.get("children", [])
        # SECTIONs may contain frames; flatten one level for naming purposes
        frame_names = []
        first_frame_id = None
        section_name = None
        for child in children:
            if child.get("type") == "SECTION":
                section_name = child.get("name")
                for sub in child.get("children", []):
                    frame_names.append(sub.get("name", ""))
                    if first_frame_id is None and sub.get("type") == "FRAME":
                        first_frame_id = sub.get("id")
            elif child.get("type") in ("FRAME", "COMPONENT", "INSTANCE"):
                frame_names.append(child.get("name", ""))
                if first_frame_id is None:
                    first_frame_id = child.get("id")
        pages[canvas["id"]] = {
            "name": canvas.get("name", ""),
            "section": section_name,
            "frame_count": len(frame_names),
            "first_frame_id": first_frame_id,
            "frame_names": frame_names,
        }
    return {
        "name": data.get("name", ""),
        "last_modified": data.get("lastModified", ""),
        "pages": pages,
    }


def deeplink(file_id, file_name, node_id):
    slug = urllib.parse.quote(file_name.replace(" ", "-"))
    if node_id:
        return f"https://www.figma.com/design/{file_id}/{slug}?node-id={node_id.replace(':', '-')}"
    return f"https://www.figma.com/design/{file_id}/{slug}"


def merge():
    flat_pages = []
    files_summary = []
    for yaml_path in sorted(FILES_DIR.glob("*.yaml")):
        overlay = yaml.safe_load(yaml_path.read_text())
        file_id = overlay["file_id"]
        try:
            live = fetch_file_state(file_id)
        except Exception as e:
            print(f"!! could not fetch {file_id}: {e}", file=sys.stderr)
            live = {"name": overlay["file_name"], "last_modified": "", "pages": {}}

        files_summary.append({
            "file_id": file_id,
            "file_name": overlay["file_name"],
            "title": overlay.get("title", overlay["file_name"]),
            "purpose": overlay.get("purpose", ""),
            "url": overlay.get("url"),
            "status": overlay.get("status", "active"),
            "last_modified": live["last_modified"],
            "page_count": len(live["pages"]),
            "tags": overlay.get("tags", []),
        })

        # Build per-page records, merging overlay + live state
        for page in overlay.get("pages", []):
            pid = page["page_id"]
            live_page = live["pages"].get(pid, {})
            node_id = live_page.get("first_frame_id") or pid
            record = {
                "file_id": file_id,
                "file_name": overlay["file_name"],
                "file_title": overlay.get("title", overlay["file_name"]),
                "file_status": overlay.get("status", "active"),
                "file_tags": overlay.get("tags", []),
                "page_id": pid,
                "page_name": page["name"],
                "section": page.get("section") or live_page.get("section"),
                "purpose": page.get("purpose", ""),
                "covers": page.get("covers", []),
                "tags": page.get("tags", []),
                "states": page.get("states", []),
                "platforms": page.get("platforms", overlay.get("platforms", [])),
                "themes": page.get("themes", overlay.get("themes", [])),
                "status": page.get("status", "active"),
                "frame_count": live_page.get("frame_count", 0),
                "frame_names": live_page.get("frame_names", []),
                "deeplink": deeplink(file_id, overlay["file_name"], node_id),
                "last_modified": live["last_modified"],
            }
            # Concatenated search blob (BM25-friendly)
            blob_parts = [
                overlay.get("title", ""),
                overlay.get("purpose", ""),
                page["name"],
                page.get("section") or "",
                page.get("purpose", ""),
                " ".join(page.get("covers", [])),
                " ".join(page.get("tags", [])),
                " ".join(overlay.get("tags", [])),
                " ".join(page.get("states", [])),
                " ".join(live_page.get("frame_names", [])),
            ]
            record["search_blob"] = " \n ".join(p for p in blob_parts if p)
            flat_pages.append(record)

    out = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "files": files_summary,
        "pages": flat_pages,
    }
    INDEX_PATH.write_text(json.dumps(out, indent=2))
    print(f"Wrote {INDEX_PATH} — {len(files_summary)} files, {len(flat_pages)} pages")
    return out


if __name__ == "__main__":
    merge()
