// Re-captures every graph render, measurement and before/after screenshot from the
// fixtures. Run: `node spike/visual/capture.mjs` (after `bun install` in spike/visual).
// Writes out/<plan>.{mmd,svg,png}, out/<plan>-{before,after}.png and out/measurements.json.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { marked } from "marked";
import { graph } from "./graph.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "out");
mkdirSync(out, { recursive: true });
const PAGE = 880;
const VIEWPORT_H = 1000;
const TABLE_SHOWN = 800; // a tall section is cropped to the picture plus the table's start
const pptr = join(out, "puppeteer.json");
writeFileSync(pptr, JSON.stringify({ executablePath: chromium.executablePath(), args: ["--no-sandbox"] }));
const mmdc = join(here, "node_modules/.bin/mmdc");

const pngSize = (f) => { const b = readFileSync(f); return { width: b.readUInt32BE(16), height: b.readUInt32BE(20) }; };
// The design-doc template's `cell` macro: one line, no bare column delimiter.
const cell = (v) => String(v).replace(/\|/g, "\\|").replace(/\r\n|\n|\r/g, " ");

function section(plan, withGraph, name) {
  const title = new Map(plan.tasks.map((t) => [t.id, t.title]));
  const rows = plan.tasks.map((t, i) => `| ${cell(t.title)} | ${cell(`unit-${(i % 4) + 1}`)} | ${cell(`What task ${i + 1} delivers, in a sentence`)} | ${cell(t.depends_on.map((d) => title.get(d)).join(", ") || "none")} | ${cell("github.com/nickderobertis/ai-orchestrator")} |`);
  return ["## Planned tasks", "",
    ...(withGraph ? [`![Dependency graph of the plan's tasks](./${name}.png)`, ""] : []),
    "| Task | Unit | What it delivers | Depends on | Where it lives |", "| --- | --- | --- | --- | --- |", ...rows, ""].join("\n");
}

const css = `body{margin:0;background:#fff;font-family:-apple-system,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:#1f2328}
.markdown-body{box-sizing:content-box;width:${PAGE}px;padding:16px;border:1px solid #d1d9e0;border-radius:6px;margin:16px}
h2{font-size:1.5em;font-weight:600;padding-bottom:.3em;border-bottom:1px solid #d1d9e0;margin:0 0 16px}
img{max-width:100%;box-sizing:content-box}
table{display:block;width:max-content;max-width:100%;overflow:auto;border-collapse:collapse;margin-top:16px}
th,td{padding:6px 13px;border:1px solid #d1d9e0}th{font-weight:600}tr:nth-child(2n){background:#f6f8fa}`;

const browser = await chromium.launch({ args: ["--no-sandbox"] });
// Viewport: 880-px content + 2 x (16-px padding + 1-px border) + 2 x 16-px margin.
const page = await browser.newPage({ viewport: { width: PAGE + 2 * (16 + 1) + 2 * 16, height: VIEWPORT_H } });
const results = [];
for (const name of ["plan-10", "plan-48", "plan-100"]) {
  const plan = JSON.parse(readFileSync(join(here, "fixtures", `${name}.json`), "utf8"));
  const g = graph(plan);
  writeFileSync(join(out, `${name}.mmd`), g.text);
  const png = join(out, `${name}.png`), svg = join(out, `${name}.svg`);
  const t0 = performance.now();
  execFileSync(mmdc, ["-q", "-i", join(out, `${name}.mmd`), "-o", png, "-w", String(PAGE), "-b", "white", "-p", pptr]);
  const renderMs = Math.round(performance.now() - t0);
  execFileSync(mmdc, ["-q", "-i", join(out, `${name}.mmd`), "-o", svg, "-w", String(PAGE), "-p", pptr]);
  const svgText = readFileSync(svg, "utf8");
  const naturalWidth = Number(svgText.match(/viewBox="[-\d.]+ [-\d.]+ ([\d.]+) [\d.]+"/)[1]);
  const fontPx = Number(svgText.match(/font-size:\s*([\d.]+)px/)[1]);
  const { width, height } = pngSize(png);
  const other = g.direction === "LR" ? "TD" : "LR";
  writeFileSync(join(out, `${name}-${other}.mmd`), g.text.replace(/^flowchart \w+/, `flowchart ${other}`));
  execFileSync(mmdc, ["-q", "-i", join(out, `${name}-${other}.mmd`), "-o", join(out, `${name}-${other}.png`), "-w", String(PAGE), "-p", pptr]);
  execFileSync(mmdc, ["-q", "-i", join(out, `${name}-${other}.mmd`), "-o", join(out, `${name}-${other}.svg`), "-w", String(PAGE), "-p", pptr]);
  const otherNatural = Number(readFileSync(join(out, `${name}-${other}.svg`), "utf8").match(/viewBox="[-\d.]+ [-\d.]+ ([\d.]+) [\d.]+"/)[1]);
  const otherPng = pngSize(join(out, `${name}-${other}.png`));
  const displayed = Math.min(width, PAGE);
  const r = { plan: name, tasks: plan.tasks.length, direction: g.direction, png: { width, height, bytes: statSync(png).size },
    render_ms: renderMs, svg_natural_width: naturalWidth, label_font_px: fontPx,
    smallest_label_px: Math.round((fontPx * displayed / naturalWidth) * 10) / 10,
    other_direction: { direction: other, svg_natural_width: otherNatural, png: otherPng,
      smallest_label_px: Math.round((fontPx * Math.min(otherPng.width, PAGE) / otherNatural) * 10) / 10 } };
  results.push(r);

  for (const [kind, withGraph] of [["before", false], ["after", true]]) {
    const md = section(plan, withGraph, name);
    writeFileSync(join(out, `${name}-${kind}.md`), md);
    const html = `<!doctype html><meta charset="utf-8"><style>${css}</style><article class="markdown-body">${marked.parse(md)}</article>`;
    writeFileSync(join(out, `${name}-${kind}.html`), html);
    await page.goto(`file://${join(out, `${name}-${kind}.html`)}`);
    await page.waitForLoadState("load");
    const box = await page.locator("article").boundingBox();
    const table = await page.locator("table").boundingBox();
    const content = await page.locator("article").evaluate((a) => parseFloat(getComputedStyle(a).width));
    const img = withGraph ? await page.locator("article img").evaluate((i) => i.getBoundingClientRect().width) : null;
    await page.screenshot({ path: join(out, `${name}-${kind}.png`), fullPage: true,
      clip: { x: box.x, y: box.y, width: box.width, height: Math.min(box.height, table.y - box.y + TABLE_SHOWN) } });
    r[`${kind}_screenshot`] = { ...pngSize(join(out, `${name}-${kind}.png`)), section_height: Math.round(box.height), table_starts_at: Math.round(table.y - box.y), content_column_px: content, ...(img ? { graph_displayed_px: img } : {}) };
  }
  console.log(JSON.stringify(r));
}
await browser.close();
writeFileSync(join(out, "measurements.json"), JSON.stringify(results, null, 2) + "\n");
