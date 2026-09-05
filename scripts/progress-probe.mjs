/* Does each node's progress bar track the real passes?
 *
 * A source table is landed once and never recomputed, so it must show no bar at
 * all. A computed table must go 0 -> 1 once per rebalance, as many times as there
 * are rebalances.
 */
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8429";
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1470, height: 820 } });
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForSelector("#book .sitem", { timeout: 15000 });
await page.waitForTimeout(1200);

const snap = () => page.evaluate(() => {
  const d = window.QANAT.dag;
  const o = {};
  d.nodes.filter(n => !n.dummy).forEach(n => {
    o[n.id] = { computed: !!n.computed, prog: +(n.prog || 0).toFixed(2), state: n.state };
  });
  return { replaying: !!d.replaying, stop: d.stopNo, of: d.stopsTotal, nodes: o };
});

const before = await snap();
console.log("idle: replaying =", before.replaying,
  "| bars drawn for", Object.values(before.nodes).filter(n => n.computed).length, "computed tables");
console.log("sources (never recomputed):",
  Object.entries(before.nodes).filter(([, n]) => !n.computed).map(([k]) => k).join(", "));

await page.evaluate(() => fetch("/api/backtest", {
  method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ alpha: "alpha_momentum", from: "2025-08-01", to: "2026-08-01",
                         rebalance: "2d", seed: 1 }),
}));

const seen = {}, resets = {}; let stops = new Set(); let sourceBar = 0;
for (let i = 0; i < 45; i++) {
  const s = await snap();
  if (s.replaying) {
    if (s.stop) stops.add(s.stop);
    for (const [id, n] of Object.entries(s.nodes)) {
      if (!n.computed && n.prog > 0) sourceBar++;
      if (n.computed) {
        (seen[id] = seen[id] || new Set()).add(n.prog);
        resets[id] = (resets[id] || 0) + (n.prog === 0 ? 1 : 0);
      }
    }
  }
  await page.waitForTimeout(120);
}
console.log("passes observed:", [...stops].sort((a, b) => a - b).join(","));
console.log("source tables that showed progress:", sourceBar, sourceBar === 0 ? "(correct)" : "! WRONG");
for (const [id, vals] of Object.entries(seen)) {
  console.log(`  ${id.padEnd(26)} fill values ${vals.size} · caught at empty ${resets[id] || 0} times`);
}
await b.close();
