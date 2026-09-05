import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8428";
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1470, height: 820 } });
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForSelector("#book .sitem", { timeout: 15000 });
await page.waitForTimeout(1200);

const st = () => page.evaluate(() => {
  const d = window.QANAT.dag;
  return { focus: d.focusKey, dim: d.nodes.filter(n => n.dim).length, k: +d.view.k.toFixed(3) };
});
console.log("before run:", JSON.stringify(await st()));

await page.locator("#btn-run").click();
await page.waitForSelector("#f-go", { timeout: 10000 });
await page.selectOption("#f-alpha", { index: 1 });
const picked = await page.locator("#f-alpha").inputValue();
await page.fill("#f-from", "2026-01-05");
await page.fill("#f-to", "2026-05-01");
await page.locator("#f-go").click();
await page.waitForTimeout(1200);
const after = await st();
console.log("during run:", JSON.stringify(after), "| alpha:", picked);
console.log(after.focus && after.focus.includes(picked) ? "graph focused on the alpha being run"
                                                        : "! graph not focused");
console.log(after.dim > 0 ? "lineage lit, rest dimmed" : "! nothing dimmed");
await b.close();
