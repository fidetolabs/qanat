// Does the drawing tell the truth about the replay?
//
// Every bar and every packet is supposed to be caused by a job that really ran.
// The recorder runs inside the page, one sample per animation frame, because
// round-tripping per sample is slower than a pass over the graph and would miss
// most of what it is trying to check.
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8439";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(1200);

const conds = await (await fetch(BASE + "/api/backtest/conditions")).json();
const alpha = conds.alphas[0].id;
const frm = conds.data.earliest, to = conds.data.latest;

await p.evaluate(() => {
  const d = window.QANAT.dag;
  window.__M = { stops: [], nodes: {}, fired: [], packets: {} };
  const M = window.__M;
  const raf = () => {
    if (d.replaying) {
      if (M.stops[M.stops.length - 1] !== d.replayStop) M.stops.push(d.replayStop);
      d.nodes.forEach((n) => {
        if (n.dummy) return;
        const r = (M.nodes[n.id] = M.nodes[n.id] ||
          { perPass: !!n.perPass, computed: !!n.computed, vals: [], resets: 0, high: 0 });
        const last = r.vals[r.vals.length - 1];
        if (last != null && n.prog + 0.05 < last) r.resets++;
        r.vals.push(n.prog || 0);
        r.high = Math.max(r.high, n.prog || 0);
      });
      d.packets.forEach((pk) => {
        const seg = pk.segs[pk.i];
        if (!seg) return;
        const key = seg.src + " -> " + seg.dst + "  (" + (seg.step && seg.step.id) + ")";
        M.packets[key] = (M.packets[key] || 0) + 1;
      });
    }
    requestAnimationFrame(raf);
  };
  requestAnimationFrame(raf);
  window.watchNow();
});

const kick = fetch(BASE + "/api/backtest", {
  method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ from: frm, to, rebalance: "5d", alpha, seed: 0 }),
});
const res = await kick;
if (!res.ok) { console.log("! POST", res.status, (await res.text()).slice(0, 200)); await b.close(); process.exit(1); }
const report = await res.json();
await p.waitForTimeout(1500);

const M = await p.evaluate(() => window.__M);
const ran = report.periods.length + 1;               // as-of dates the engine walked
console.log(`engine walked ${ran} as-of dates; the drawing showed ${M.stops.length}`);
let bad = 0;
if (M.stops.length < ran * 0.8) {
  console.log(`  ! the drawing skipped ${ran - M.stops.length} of them`); bad++;
}

console.log("nodes:");
for (const [id, r] of Object.entries(M.nodes).sort()) {
  const distinct = new Set(r.vals.map((v) => Math.round(v * 20))).size;
  const line = `  ${id.padEnd(30)} per-pass=${r.perPass ? "y" : "n"} ` +
    `resets=${String(r.resets).padStart(3)} distinct=${String(distinct).padStart(3)} ` +
    `high=${r.high.toFixed(2)}`;
  if (!r.perPass && r.high > 0) { console.log(line + "   ! drew a bar but has no pass"); bad++; }
  else if (r.perPass && r.resets < M.stops.length * 0.5) {
    console.log(line + `   ! filled ${r.resets + 1}x for ${M.stops.length} passes`); bad++;
  } else if (r.perPass && distinct < 3) {
    console.log(line + "   ! jumped instead of filling"); bad++;
  } else console.log(line);
}

console.log("edges that carried a packet:");
const seen = Object.entries(M.packets).sort();
for (const [k, n] of seen) console.log(`  ${k.padEnd(64)} frames=${n}`);
// only the steps this replay touches should have moved something: a backtest
// replays one alpha's lineage, and the other alphas genuinely did not run
const prog = await (await fetch(BASE + "/api/backtest/progress")).json();
const order = report.conditions.jobs_in_run ||
  (await (await fetch(BASE + "/api/graph")).json()).run_order;
for (const step of order) {
  if (!seen.some(([k]) => k.endsWith("(" + step + ")"))) {
    console.log(`  ! ${step} ran but nothing moved on its arrow`); bad++;
  }
}
console.log(bad ? `\n${bad} thing(s) the drawing got wrong` : "\nthe motion is the run");
await b.close();
process.exit(bad ? 1 : 0);
