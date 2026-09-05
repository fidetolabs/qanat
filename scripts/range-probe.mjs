import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8428";
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1470, height: 820 } });
await page.goto(BASE + "/?view=backtests", { waitUntil: "networkidle" });
await page.waitForSelector("#bt-detail .bt-panel", { timeout: 15000 });
await page.waitForTimeout(1500);

const head = () => page.locator("#bt-inline-sum").textContent();
console.log("before:", (await head()).trim());

const CHART = process.env.CHART || "Net per rebalance";
// find the chart by its heading, not by guessing a y
const box = await page.evaluate((name) => {
  const panels = [...document.querySelectorAll("#bt-detail .bt-panel")];
  const p = panels.find(x => new RegExp(name, "i").test(x.querySelector("h4")?.textContent || ""));
  if (!p) return null;
  const cb = p.querySelector(".chartbox");
  if (!cb) return null;
  cb.scrollIntoView({ block: "center" });
  const r = cb.getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height };
}, CHART);
if (!box) { console.log("! chart not found:", CHART); await b.close(); process.exit(1); }
await page.waitForTimeout(400);
const box2 = await page.evaluate((name) => {
  const panels = [...document.querySelectorAll("#bt-detail .bt-panel")];
  const p = panels.find(x => new RegExp(name, "i").test(x.querySelector("h4")?.textContent || ""));
  const r = p.querySelector(".chartbox").getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height };
}, CHART);
console.log(`${CHART} at ${Math.round(box2.x)},${Math.round(box2.y)} ${Math.round(box2.w)}x${Math.round(box2.h)}`);

const y = box2.y + box2.h / 2;
await page.mouse.move(box2.x + box2.w * 0.30, y);
await page.mouse.down();
for (let i = 1; i <= 12; i++) {
  await page.mouse.move(box2.x + box2.w * (0.30 + 0.35 * i / 12), y);
  await page.waitForTimeout(15);
}
await page.mouse.up();
await page.waitForTimeout(900);

const after = (await head()).trim();
console.log("after :", after);
const band = await page.locator(".rangebar").count();
console.log(band ? "range bar shown" : "! no range bar");
if (band) console.log("  " + (await page.locator(".rangebar").textContent()).replace(/\s+/g, " ").trim());
await b.close();
