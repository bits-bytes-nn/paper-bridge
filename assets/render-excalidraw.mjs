#!/usr/bin/env node
// Render an .excalidraw scene to PNG while preserving the hand-drawn (Virgil) font.
//
// Why this exists: the standard `excalidraw-to-png` CLI rasterizes via resvg,
// which cannot parse the WOFF2 `@font-face` Excalidraw embeds in its SVG — so text
// silently falls back to a generic sans-serif. Here we strip that broken
// `@font-face` block and hand resvg a real TTF (decompressed from the Virgil
// WOFF2 once into assets/virgil.ttf), forcing every text family to "Virgil".
//
// Usage: node assets/render-excalidraw.mjs <in.excalidraw> <out.png> [scale]
//
// Portable: playwright + @resvg are resolved from the excalidraw-to-png tool's
// node_modules via createRequire, so this script needs no deps of its own.
import { readFileSync, writeFileSync } from "fs";
import { resolve, dirname, join } from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { createRequire } from "module";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TOOL_DIR = join(process.env.HOME, "Projects", "tools", "excalidraw-to-png");
const require = createRequire(join(TOOL_DIR, "package.json"));
const BUNDLE_PATH = join(TOOL_DIR, "dist", "excalidraw-bundle.js");
const VIRGIL_TTF = join(__dirname, "virgil.ttf");

const [input, output, scaleArg] = process.argv.slice(2);
if (!input || !output) {
  console.error("Usage: render-excalidraw.mjs <in.excalidraw> <out.png> [scale]");
  process.exit(1);
}
const scale = Number(scaleArg) || 2;

const { chromium } = require("playwright");
const { Resvg } = require("@resvg/resvg-js");

async function excalidrawToSvg(jsonStr) {
  const bundleJs = readFileSync(BUNDLE_PATH, "utf-8");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
</head><body><div id="root"></div></body></html>`;
  await page.setContent(html, { waitUntil: "networkidle", timeout: 30000 });
  await page.addScriptTag({ content: bundleJs });
  await page.waitForFunction(() => window.__moduleReady === true, { timeout: 10000 });
  const svgStr = await page.evaluate(async (sceneJson) => {
    const scene = JSON.parse(sceneJson);
    const svg = await window.__exportToSvg({
      elements: scene.elements || [],
      appState: { ...(scene.appState || {}), exportWithDarkMode: false, theme: "light" },
      files: scene.files || {},
    });
    return svg.outerHTML;
  }, jsonStr);
  await browser.close();
  return svgStr;
}

function stripEmbeddedFontFace(svg) {
  // Remove the WOFF2 @font-face blocks resvg can't read; force Virgil everywhere.
  return svg
    .replace(/@font-face\s*{[^}]*}/g, "")
    .replace(/font-family\s*:\s*[^;"]+/g, 'font-family: Virgil')
    .replace(/font-family="[^"]*"/g, 'font-family="Virgil"');
}

async function main() {
  const jsonStr = readFileSync(resolve(input), "utf-8");
  console.error(`Rendering ${input} ...`);
  let svg = await excalidrawToSvg(jsonStr);
  svg = stripEmbeddedFontFace(svg);
  const resvg = new Resvg(svg, {
    fitTo: { mode: "zoom", value: scale },
    font: { fontFiles: [VIRGIL_TTF], defaultFontFamily: "Virgil", loadSystemFonts: true },
  });
  const png = resvg.render().asPng();
  writeFileSync(resolve(output), png);
  console.error(`PNG saved to ${output} (${png.length} bytes)`);
}

main().catch((e) => {
  console.error(`Error: ${e.stack || e.message}`);
  process.exit(1);
});
