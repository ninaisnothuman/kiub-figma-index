// Kiub README Sync — Figma plugin
//
// On run, fetches the latest README descriptor for the current file from the
// kiub-figma-index GitHub repo and rewrites the "📄 README" page in this file.
// Idempotent: clears existing README children and redraws.
//
// Source repo: https://github.com/ninaisnothuman/kiub-figma-index
//
// The plugin uses figma.root.name to look up the right descriptor in
// generated/manifest.json. To add a new file to the registry, edit the YAML
// overlay in the repo and run `python3 scripts/sync.py gen`.

const REPO_BASE = "https://raw.githubusercontent.com/ninaisnothuman/kiub-figma-index/main";
const MANIFEST_URL = `${REPO_BASE}/generated/manifest.json`;

figma.showUI(__html__, { width: 400, height: 280 });

figma.ui.onmessage = async (msg) => {
  if (msg.type === "init") {
    figma.ui.postMessage({ type: "context", fileName: figma.root.name });
  } else if (msg.type === "sync") {
    try {
      const result = await sync();
      figma.ui.postMessage({ type: "done", message: result });
    } catch (e) {
      console.error(e);
      figma.ui.postMessage({ type: "error", message: String(e.message || e) });
    }
  } else if (msg.type === "close") {
    figma.closePlugin();
  }
};

async function sync() {
  const fileName = figma.root.name;

  // 1. Fetch manifest
  const mResp = await fetch(MANIFEST_URL, { cache: "no-store" });
  if (!mResp.ok) throw new Error(`manifest fetch ${mResp.status}`);
  const manifest = await mResp.json();

  const entry = manifest[fileName];
  if (!entry) {
    throw new Error(
      `No descriptor mapped for file name "${fileName}". ` +
      `Add an overlay YAML to the kiub-figma-index repo with file_name: "${fileName}".`
    );
  }

  // 2. Fetch the readme data for this file
  const dataUrl = `${REPO_BASE}/${entry.data_path}`;
  const dResp = await fetch(dataUrl, { cache: "no-store" });
  if (!dResp.ok) throw new Error(`data fetch ${dResp.status}`);
  const readme_data = await dResp.json();

  // 3. Render
  await figma.loadFontAsync({ family: "Inter", style: "Regular" });
  await figma.loadFontAsync({ family: "Inter", style: "Semi Bold" });
  await figma.loadFontAsync({ family: "Inter", style: "Bold" });

  let readme = (await figma.root.children).find(p => p.name === "📄 README");
  if (readme) {
    for (const c of [...readme.children]) c.remove();
  } else {
    readme = figma.createPage();
    readme.name = "📄 README";
  }
  figma.root.insertChild(0, readme);
  await figma.setCurrentPageAsync(readme);

  const frame = figma.createFrame();
  frame.name = "README";
  frame.x = 0; frame.y = 0;
  frame.resize(880, 100);
  frame.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
  frame.cornerRadius = 16;
  frame.strokes = [{ type: "SOLID", color: { r: 0.9, g: 0.9, b: 0.9 } }];
  frame.strokeWeight = 1;
  frame.layoutMode = "VERTICAL";
  frame.primaryAxisSizingMode = "AUTO";
  frame.counterAxisSizingMode = "FIXED";
  frame.paddingTop = 56; frame.paddingBottom = 56;
  frame.paddingLeft = 64; frame.paddingRight = 64;
  frame.itemSpacing = 18;
  readme.appendChild(frame);

  function txt(content, size, weight, color) {
    const t = figma.createText();
    t.fontName = { family: "Inter", style: weight || "Regular" };
    t.characters = content;
    t.fontSize = size;
    if (color) t.fills = [{ type: "SOLID", color }];
    t.layoutAlign = "STRETCH";
    t.textAutoResize = "HEIGHT";
    t.lineHeight = { value: 150, unit: "PERCENT" };
    frame.appendChild(t);
    return t;
  }

  txt(readme_data.title, 32, "Bold");
  txt(`Status: ${readme_data.status}     Owner: ${readme_data.owner}     Last updated: ${readme_data.last_updated}`,
      13, "Regular", { r: 0.45, g: 0.45, b: 0.45 });
  txt(readme_data.purpose, 15, "Regular");

  for (const section of readme_data.sections) {
    txt(section.heading, 20, "Semi Bold");
    if (section.subtitle) txt(section.subtitle, 13, "Regular", { r: 0.45, g: 0.45, b: 0.45 });
    for (const item of section.items) {
      const t = figma.createText();
      t.fontName = { family: "Inter", style: "Regular" };
      t.characters = `•  ${item[0]}\n    ${item[1]}`;
      t.fontSize = 13;
      t.lineHeight = { value: 150, unit: "PERCENT" };
      t.layoutAlign = "STRETCH";
      t.textAutoResize = "HEIGHT";
      frame.appendChild(t);
    }
  }

  if (readme_data.not_in_file && readme_data.not_in_file.length) {
    txt("What's NOT in this file", 20, "Semi Bold");
    txt(readme_data.not_in_file.map(x => `•  ${x}`).join("\n"), 13, "Regular");
  }

  txt(readme_data.footer, 12, "Regular", { r: 0.45, g: 0.45, b: 0.45 });

  figma.root.setSharedPluginData("kiub_index", "last_sync", JSON.stringify({
    at: new Date().toISOString(),
    source: "kiub-readme-sync-plugin",
    file_name: fileName,
  }));

  return `Synced "${fileName}" — ${readme_data.sections.reduce((n, s) => n + s.items.length, 0)} pages described.`;
}
