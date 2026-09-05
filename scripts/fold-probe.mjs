import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8428";
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1470, height: 820 } });
await page.goto(BASE + "/?view=backtests", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

const heights = () => page.evaluate(() => {
  const g = (s) => { const e = document.querySelector(s); if (!e) return 0;
    const r = e.getBoundingClientRect(); return Math.round(r.height); };
  const stage = document.querySelector(".stage").getBoundingClientRect().height;
  const view = g(".view"), bt = g(".btwrap"), log = g(".logwrap");
  return { stage: Math.round(stage), view, bt, log, gap: Math.round(stage - view - bt - log) };
});

for (const results of [true, false]) {
  if (!results) await page.locator("#btn-bt").click();
  await page.waitForTimeout(500);
  const open = await heights();
  await page.locator("#fold-graph").click();
  await page.waitForTimeout(600);
  const folded = await heights();
  console.log(`results ${results ? "open " : "shut "} | unfolded gap ${open.gap}  folded gap ${folded.gap}` +
    `  ${folded.gap <= 2 ? "no dead space" : "! DEAD SPACE " + folded.gap + "px"}`);
  await page.locator("#fold-graph").click();
  await page.waitForTimeout(400);
}
await b.close();
