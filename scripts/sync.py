#!/usr/bin/env python3
"""
sync.py — Drift detection and README regeneration for the Kiub figma registry.

Subcommands:
  drift             Compare each overlay YAML against the live Figma file and
                    print a report (new pages in Figma, stale entries in YAML,
                    in-Figma renames). Exits non-zero if drift exists. Cron-safe.

  gen               Re-generate the per-file README JavaScript snippets in
                    generated/<file_name>_readme.js. The JS rebuilds the
                    README page in Figma from the overlay YAML.

  show <file>       Print the JS for a single overlay (e.g. `sync.py show v0`).
                    Pipe into the Figma MCP `use_figma` tool to apply.

Workflow for cron:
  1. `sync.py drift` daily — alerts when drift exists
  2. When an overlay YAML changes, `sync.py gen` regenerates the JS
  3. To apply README changes to live Figma: ask Claude to run the
     generated JS for the changed file via the Figma MCP `use_figma` tool.
     (Direct Python → Figma write is not supported by Figma's REST API;
     writes go through the Plugin API which requires Claude/MCP.)
"""

import argparse
import json
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi
import yaml

ROOT = Path(__file__).resolve().parent.parent
FILES_DIR = ROOT / "files"
GENERATED_DIR = ROOT / "generated"
DRIFT_REPORT = ROOT / "drift.json"
SECRETS = Path.home() / ".openclaw" / "secrets.json"
_SSL = ssl.create_default_context(cafile=certifi.where())

# Pages we treat as "structural" — visual separators, section header labels,
# the README itself. These don't need an overlay entry.
def is_structural_page(name):
    if not name:
        return True
    if name.strip() == "-":
        return True
    if name.startswith("📄"):  # README page
        return True
    # Header label pages: ALL CAPS + no '/' (e.g. "EXPERIMENT", "SETTINGS",
    # "HOME", "BROWSER HISTORY", "ONBOARDING", "LTD", "ARCHIVE", "PAGE TITLE")
    return name.upper() == name and "/" not in name and len(name) <= 32


def figma_get(path):
    token = json.loads(SECRETS.read_text())["FIGMA_TOKEN"]
    req = urllib.request.Request(
        f"https://api.figma.com/v1/{path}",
        headers={"X-Figma-Token": token},
    )
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
        return json.loads(r.read().decode())


def fetch_pages(file_id):
    """Returns list of (page_id, page_name) for every CANVAS in the file."""
    data = figma_get(f"files/{file_id}?depth=1")
    return [
        (c["id"], c.get("name", ""))
        for c in data["document"]["children"]
        if c.get("type") == "CANVAS"
    ]


def load_overlays():
    out = []
    for path in sorted(FILES_DIR.glob("*.yaml")):
        out.append((path, yaml.safe_load(path.read_text())))
    return out


# ---------------------------- DRIFT DETECTION -----------------------------

def detect_drift():
    report = {"files": []}
    has_drift = False

    for path, overlay in load_overlays():
        file_id = overlay["file_id"]
        try:
            live = fetch_pages(file_id)
        except Exception as e:
            report["files"].append({"file": overlay["file_name"], "error": str(e)})
            has_drift = True
            continue

        live_ids = {pid for pid, _ in live if not is_structural_page(_)}
        live_names = {pid: name for pid, name in live}
        expected = {p["page_id"]: p for p in overlay.get("pages", [])}

        new_in_figma = [
            {"page_id": pid, "name": live_names[pid]}
            for pid in live_ids
            if pid not in expected
        ]
        stale_in_overlay = [
            {"page_id": pid, "name": expected[pid]["name"]}
            for pid in expected
            if pid not in live_ids and not any(p == pid for p, _ in live)
        ]
        renamed_in_figma = [
            {
                "page_id": pid,
                "overlay_name": expected[pid]["name"],
                "figma_name": live_names[pid],
            }
            for pid in expected
            if pid in live_names and live_names[pid] != expected[pid]["name"]
            # Renames are expected — overlay names are canonical, Figma may
            # carry the OLD name. Only flag if Figma name doesn't END with the
            # last segment of the overlay name (heuristic: catches genuine drift).
            and not live_names[pid].endswith(expected[pid]["name"].split(" / ")[-1])
        ]

        file_report = {
            "file_id": file_id,
            "file": overlay["file_name"],
            "overlay_path": str(path.relative_to(ROOT)),
            "new_in_figma": new_in_figma,
            "stale_in_overlay": stale_in_overlay,
            "renamed_in_figma": renamed_in_figma,
        }
        if new_in_figma or stale_in_overlay or renamed_in_figma:
            has_drift = True
        report["files"].append(file_report)

    DRIFT_REPORT.write_text(json.dumps(report, indent=2))
    return report, has_drift


def print_drift_report(report):
    any_drift = False
    for f in report["files"]:
        if "error" in f:
            print(f"!! {f['file']}: ERROR {f['error']}")
            any_drift = True
            continue
        n, s, r = f["new_in_figma"], f["stale_in_overlay"], f["renamed_in_figma"]
        if not (n or s or r):
            print(f"OK {f['file']} — no drift")
            continue
        any_drift = True
        print(f"\n## {f['file']} ({f['overlay_path']})")
        if n:
            print(f"  NEW pages in Figma not in overlay ({len(n)}):")
            for p in n:
                print(f"    + {p['page_id']}  {p['name']}")
        if s:
            print(f"  STALE overlay entries (not in Figma anymore) ({len(s)}):")
            for p in s:
                print(f"    - {p['page_id']}  {p['name']}")
        if r:
            print(f"  RENAMED in Figma vs overlay ({len(r)}):")
            for p in r:
                print(f"    ~ {p['page_id']}")
                print(f"      overlay:  {p['overlay_name']}")
                print(f"      figma:    {p['figma_name']}")
    return any_drift


# -------------------------- README JS GENERATOR ---------------------------

JS_TEMPLATE = r"""// Auto-generated by sync.py — do not edit by hand.
// Source overlay: {overlay_path}
//
// Run via Figma MCP `use_figma` with fileKey={file_id} to (re)build the
// README page in this Figma file. Idempotent: clears existing README
// children and redraws from this data.

const readme_data = {readme_json};

await figma.loadFontAsync({{family: "Inter", style: "Regular"}});
await figma.loadFontAsync({{family: "Inter", style: "Semi Bold"}});
await figma.loadFontAsync({{family: "Inter", style: "Bold"}});

let readme = figma.root.children.find(p => p.name === "📄 README");
if (readme) {{
  for (const c of [...readme.children]) c.remove();
}} else {{
  readme = figma.createPage();
  readme.name = "📄 README";
}}
figma.root.insertChild(0, readme);
await figma.setCurrentPageAsync(readme);

const frame = figma.createFrame();
frame.name = "README";
frame.x = 0; frame.y = 0;
frame.resize(880, 100);
frame.fills = [{{type: "SOLID", color: {{r: 1, g: 1, b: 1}}}}];
frame.cornerRadius = 16;
frame.strokes = [{{type: "SOLID", color: {{r: 0.9, g: 0.9, b: 0.9}}}}];
frame.strokeWeight = 1;
frame.layoutMode = "VERTICAL";
frame.primaryAxisSizingMode = "AUTO";
frame.counterAxisSizingMode = "FIXED";
frame.paddingTop = 56; frame.paddingBottom = 56;
frame.paddingLeft = 64; frame.paddingRight = 64;
frame.itemSpacing = 18;
readme.appendChild(frame);

function txt(content, size, weight, color) {{
  const t = figma.createText();
  t.fontName = {{family: "Inter", style: weight || "Regular"}};
  t.characters = content;
  t.fontSize = size;
  if (color) t.fills = [{{type: "SOLID", color}}];
  t.layoutAlign = "STRETCH";
  t.textAutoResize = "HEIGHT";
  t.lineHeight = {{value: 150, unit: "PERCENT"}};
  frame.appendChild(t);
  return t;
}}

txt(readme_data.title, 32, "Bold");
txt(`Status: ${{readme_data.status}}     Owner: ${{readme_data.owner}}     Last updated: ${{readme_data.last_updated}}`,
    13, "Regular", {{r: 0.45, g: 0.45, b: 0.45}});
txt(readme_data.purpose, 15, "Regular");

for (const section of readme_data.sections) {{
  txt(section.heading, 20, "Semi Bold");
  if (section.subtitle) {{
    txt(section.subtitle, 13, "Regular", {{r: 0.45, g: 0.45, b: 0.45}});
  }}
  for (const item of section.items) {{
    const t = figma.createText();
    t.fontName = {{family: "Inter", style: "Regular"}};
    t.characters = `•  ${{item[0]}}\n    ${{item[1]}}`;
    t.fontSize = 13;
    t.lineHeight = {{value: 150, unit: "PERCENT"}};
    t.layoutAlign = "STRETCH";
    t.textAutoResize = "HEIGHT";
    frame.appendChild(t);
  }}
}}

if (readme_data.not_in_file && readme_data.not_in_file.length) {{
  txt("What's NOT in this file", 20, "Semi Bold");
  txt(readme_data.not_in_file.map(x => `•  ${{x}}`).join("\n"), 13, "Regular");
}}

txt(readme_data.footer, 12, "Regular", {{r: 0.45, g: 0.45, b: 0.45}});

figma.root.setSharedPluginData("kiub_index", "last_sync", JSON.stringify({{
  at: new Date().toISOString(),
  source: "sync.py",
  file_id: "{file_id}",
}}));
"""


def status_label(s, name=""):
    # Suppress label when the page name already carries a visual prefix
    # (🧪 for experiment, 🗑️ for junk).
    if "🧪" in name or "🗑️" in name:
        return ""
    if s == "active":
        return ""
    return f" ({s})"


def build_readme_data(overlay):
    """Convert an overlay dict into the structured readme_data the JS expects."""
    file_name = overlay["file_name"]
    pages = overlay.get("pages", [])

    # Default: one big "Pages in this file" section. Files can override by
    # setting `readme_sections` in the overlay.
    if "readme_sections" in overlay:
        sections = overlay["readme_sections"]
    else:
        items = []
        for p in pages:
            name = p["name"]
            desc = (p.get("purpose") or "").strip().replace("\n", " ")
            label = status_label(p.get("status", "active"), name)
            items.append([name + label, desc])
        sections = [{"heading": "Pages in this file", "items": items}]

    # "What's NOT in this file" pointers — derived from related-file links if
    # present, else from a hand-written `not_in_file` list in the overlay.
    not_in_file = overlay.get("not_in_file", [])

    return {
        "title": overlay.get("title", file_name),
        "status": overlay.get("status", "active"),
        "owner": overlay.get("owner", ""),
        "last_updated": overlay.get("last_updated", ""),
        "purpose": (overlay.get("purpose") or "").strip().replace("\n", " "),
        "sections": sections,
        "not_in_file": not_in_file,
        "footer": (
            "Discoverable from Slack via the kiub-figma-index registry. "
            "Run: python3 ~/.openclaw/workspace/kiub-figma-index/scripts/find.py \"your query\""
        ),
    }


def generate_js(overlay, overlay_path):
    data = build_readme_data(overlay)
    return JS_TEMPLATE.format(
        overlay_path=overlay_path.relative_to(ROOT),
        file_id=overlay["file_id"],
        readme_json=json.dumps(data, indent=2, ensure_ascii=False),
    )


def cmd_gen():
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    # Also build a manifest mapping file name → readme data URL, used by the
    # Figma plugin to dispatch on figma.root.name.
    manifest = {}
    for path, overlay in load_overlays():
        # 1. JS snippet (for one-off Claude/MCP applies)
        js = generate_js(overlay, path)
        out_js = GENERATED_DIR / f"{path.stem}_readme.js"
        out_js.write_text(js)
        # 2. JSON data (what the Figma plugin fetches)
        data = build_readme_data(overlay)
        out_json = GENERATED_DIR / f"{path.stem}_readme.json"
        out_json.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        manifest[overlay["file_name"]] = {
            "file_id": overlay["file_id"],
            "data_path": f"generated/{path.stem}_readme.json",
        }
        print(f"wrote {out_js.relative_to(ROOT)} + {out_json.name}")
    (GENERATED_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"wrote {GENERATED_DIR.relative_to(ROOT)}/manifest.json ({len(manifest)} files)")


def cmd_show(name):
    for path, overlay in load_overlays():
        if path.stem == name or overlay["file_name"] == name:
            print(generate_js(overlay, path))
            return
    print(f"!! no overlay matched '{name}'", file=sys.stderr)
    sys.exit(2)


def cmd_drift():
    report, has_drift = detect_drift()
    print_drift_report(report)
    print(f"\nFull report: {DRIFT_REPORT}")
    sys.exit(1 if has_drift else 0)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("drift")
    sub.add_parser("gen")
    s = sub.add_parser("show")
    s.add_argument("name")
    args = ap.parse_args()
    if args.cmd == "drift":
        cmd_drift()
    elif args.cmd == "gen":
        cmd_gen()
    elif args.cmd == "show":
        cmd_show(args.name)


if __name__ == "__main__":
    main()
