#!/usr/bin/env node
/**
 * Console smoke test implementation (Chrome via Playwright).
 * Invoked by scripts/ui-smoke.mjs — do not run with sh.
 */
import { chromium } from "playwright";

const BASE = process.env.BASE || "http://127.0.0.1:8423";
const errors = [];
const checks = [];

function pass(name) {
  checks.push({ name, ok: true });
  console.log(`  ✓ ${name}`);
}

function fail(name, detail) {
  checks.push({ name, ok: false, detail });
  errors.push(`${name}: ${detail}`);
  console.error(`  ✗ ${name} — ${detail}`);
}

const browser = await chromium.launch({
  headless: true,
  channel: "chrome",
});
const page = await browser.newPage();

page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
});

try {
  const res = await page.goto(BASE, { waitUntil: "networkidle", timeout: 30_000 });
  if (!res?.ok()) fail("GET /", `status ${res?.status()}`);
  else pass("GET /");

  const title = await page.title();
  if (!/qanat/i.test(title)) fail("page title", title);
  else pass("page title");

  await page.waitForSelector("#dagcv", { state: "attached", timeout: 15_000 });
  pass("job graph canvas");

  const brand = await page.locator(".topbar .brand b").textContent();
  if (!/Qanat/i.test(brand || "")) fail("brand", brand || "(empty)");
  else pass("brand");

  // the left rail is the strategy book: every alpha that has produced a result
  await page.waitForSelector("#book", { timeout: 15_000 });
  const book = await page.locator("#book .sitem").count();
  if (book >= 1) pass(`strategy book (${book} alpha${book === 1 ? "" : "s"})`);
  else pass("strategy book (empty — nothing backtested in this project)");

  // the node is a table now, and the step that makes it rides on the arrow
  const box = await page.evaluate(() => {
    const d = window.QANAT && window.QANAT.dag;
    if (!d || !d.nodes.length) return null;
    const n = d.nodes.find((x) => x.maker && x.layer !== "raw") || d.nodes[0];
    const at = d.at(n);
    const r = document.getElementById("dagcv").getBoundingClientRect();
    return { x: r.left + at.x + 40, y: r.top + at.y + 20, id: n.id, layer: n.layer };
  });
  if (!box) fail("graph nodes", "none laid out");
  else {
    pass(`table nodes (clicking ${box.id}, layer ${box.layer})`);
    await page.mouse.click(box.x, box.y);
    await page.waitForTimeout(1200);
    const head = await page.locator("#sel-head").textContent();
    if (head && head.trim() === box.id) pass(`table panel (${head.trim()})`);
    else fail("table panel", head || "(empty)");
  }

  // a source table has no step behind it, so it shows where its rows come from
  const src = await page.evaluate(() => {
    const d = window.QANAT && window.QANAT.dag;
    const n = d && d.nodes.find((x) => x.layer === "raw");
    if (!n) return null;
    const at = d.at(n);
    const r = document.getElementById("dagcv").getBoundingClientRect();
    return { x: r.left + at.x + 40, y: r.top + at.y + 20, id: n.id };
  });
  if (src) {
    await page.mouse.click(src.x, src.y);
    await page.waitForTimeout(1000);
    const txt = await page.locator("#sel-body").textContent();
    if (/where these rows come from/i.test(txt || "")) pass("source shows its connection");
    else fail("source connection", "no connection block");
  }

  const dataRows = await page.locator("table.data tbody tr").count();
  if (dataRows >= 1) pass(`data table (${dataRows} rows)`);
  else fail("data table", "no rows rendered");

  const sortable = await page.locator("table.data th.sortable").count();
  if (sortable >= 1) pass(`sortable columns (${sortable})`);
  else fail("sortable columns", "none");

  await page.locator("#btn-bt").click();
  await page.waitForTimeout(600);
  const btOpen = await page.locator('#btwrap[data-open="1"]').count();
  if (btOpen) pass("PnL section opens");
  else fail("PnL section", "did not open");

  // Editing has no overlay any more: each thing is changed where it is drawn.
  // A stage opens from the rail behind the graph.
  const band = await page.evaluate(() => {
    const d = window.QANAT.dag, b = (d.bands || []).filter((x) => x.id)[0];
    if (!b) return null;
    d.onStage(b);
    return b.id;
  });
  await page.waitForTimeout(400);
  if (band && (await page.locator("#sel-head").textContent()) === band) pass(`stage panel (${band})`);
  else fail("stage panel", `rail click did not open ${band}`);

  const closed = await page.evaluate(() => {
    const d = window.QANAT.dag, b = (d.bands || []).filter((x) => x.id)[0];
    d.onStage(b);
    return !document.getElementById("detail").classList.contains("open");
  });
  if (closed) pass("stage unclicks");
  else fail("stage unclicks", "clicking the same stage again left it open");

  await page.locator("#b-add-src").click();
  await page.waitForTimeout(300);
  if (await page.locator("#sc-kind").count()) pass("new data source form");
  else fail("new data source", "form did not open");
  await page.locator("#sel-close").click();

  const graphRes = await page.evaluate(async (base) => {
    const r = await fetch(`${base}/api/graph`);
    const g = await r.json();
    return {
      status: r.status,
      tables: g.tables?.length ?? 0,
      jobs: g.jobs?.length ?? 0,
    };
  }, BASE);
  if (graphRes.status === 200 && graphRes.tables >= 5 && graphRes.jobs >= 5)
    pass(`API /api/graph (${graphRes.tables} tables, ${graphRes.jobs} jobs)`);
  else fail("API /api/graph", JSON.stringify(graphRes));

  const eventsRes = await page.evaluate(async (base) => {
    const r = await fetch(`${base}/api/events?limit=5`);
    return r.status;
  }, BASE);
  if (eventsRes === 200) pass("API /api/events");
  else fail("API /api/events", String(eventsRes));
} catch (err) {
  fail("unexpected", err.message);
} finally {
  await browser.close();
}

console.log("\n--- summary ---");
console.log(`checks: ${checks.filter((c) => c.ok).length}/${checks.length}`);
if (errors.length) {
  console.error("errors:");
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log("UI smoke passed.");
