/* Confirms the view moves over several frames rather than jumping. */
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8428";
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1470, height: 820 } });
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForSelector("#book .sitem", { timeout: 15000 });
await page.waitForTimeout(1200);

const v = () => page.evaluate(() => {
  const d = window.QANAT.dag;
  return { k: +d.view.k.toFixed(4), x: Math.round(d.view.x), gliding: !!d.glide };
});

console.log("start :", JSON.stringify(await v()));
await page.locator("#book .sitem").nth(2).click();
const frames = [];
for (let i = 0; i < 14; i++) {
  frames.push(await v());
  await page.waitForTimeout(45);
}
const ks = [...new Set(frames.map(f => f.k))];
const glided = frames.filter(f => f.gliding).length;
console.log("frames:", frames.map(f => f.k).join(" "));
console.log(`distinct k values: ${ks.length}  frames mid-glide: ${glided}`);
console.log(ks.length >= 4 ? "moves over several frames — smooth" : "! jumps");
console.log("end   :", JSON.stringify(await v()));
await b.close();

