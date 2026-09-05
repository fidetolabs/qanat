import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8443";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(1500);
const box = await p.locator("#dagcv").boundingBox();
const drags = [[4000, 3000], [-9000, -6000], [9000, -6000], [-9000, 6000]];
let bad = 0;
for (const [dx, dy] of drags) {
  await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await p.mouse.down();
  await p.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy, { steps: 6 });
  await p.mouse.up();
  await p.waitForTimeout(200);
  const seen = await p.evaluate(() => {
    const d = window.QANAT.dag, w = d.cv.clientWidth, h = d.cv.clientHeight;
    return d.nodes.filter((n) => !n.dummy).filter((n) => {
      const a = d.at(n), W = 196 * d.view.k, H = 78 * d.view.k;
      return a.x + W > 0 && a.x < w && a.y + H > 0 && a.y < h;
    }).length;
  });
  console.log(`drag ${String(dx).padStart(6)},${String(dy).padStart(6)} -> tables on screen: ${seen}`);
  if (!seen) bad++;
}
console.log(bad ? `\n! the graph could be dragged out of sight ${bad}x` : "\nsomething always stays on screen");
await b.close();
process.exit(bad ? 1 : 0);
