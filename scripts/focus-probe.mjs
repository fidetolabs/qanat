// Picking an alphaset: the card says which one, the graph frames its lineage
// once, and nothing lit ends up under the side panel.
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8540";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
const errs = [];
p.on("pageerror", (e) => errs.push(String(e)));
await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(2000);
let bad = 0;
const ok = (m) => console.log("  ok   " + m);
const no = (m) => { console.log("  FAIL " + m); bad++; };

const names = await p.$$eval("#book .sitem:not(.add) .code", (e) => e.map((x) => x.textContent.trim()));
for (const name of names) {
  // record every frame of the view while the click settles
  const r = await p.evaluate(async (want) => {
    const d = window.QANAT.dag;
    const frames = [];
    let stop = false;
    const rec = () => { if (stop) return; frames.push([+d.view.k.toFixed(4), Math.round(d.view.x), Math.round(d.view.y)]); requestAnimationFrame(rec); };
    requestAnimationFrame(rec);
    const card = [...document.querySelectorAll("#book .sitem:not(.add)")]
      .find((c) => c.querySelector(".code").textContent.trim() === want);
    card.click();
    await new Promise((r2) => setTimeout(r2, 3000));
    stop = true;
    const now = [...document.querySelectorAll("#book .sitem:not(.add)")]
      .find((c) => c.querySelector(".code").textContent.trim() === want);
    const cvr = d.cv.getBoundingClientRect();
    const lit = d.nodes.filter((n) => !n.dummy && !n.dim);
    const off = lit.filter((n) => {
      const a = d.at(n), W = 196 * d.view.k, H = 78 * d.view.k;
      return a.x < -2 || a.x + W > cvr.width + 2 || a.y < -2 || a.y + H > cvr.height + 2;
    }).map((n) => n.id);
    // how many times the view changed direction: a settled move goes one way
    let turns = 0;
    for (let i = 2; i < frames.length; i++) {
      const d1 = frames[i][0] - frames[i - 1][0], d0 = frames[i - 1][0] - frames[i - 2][0];
      if (d0 !== 0 && d1 !== 0 && Math.sign(d1) !== Math.sign(d0)) turns++;
    }
    return { on: now ? now.classList.contains("on") : false, off, turns, frames: frames.length };
  }, name);

  r.on ? ok(`${name}: the card is marked`) : no(`${name}: the card is not marked`);
  r.off.length ? no(`${name}: lit but off screen — ${r.off.join(", ")}`)
               : ok(`${name}: everything lit is on screen`);
  r.turns <= 2 ? ok(`${name}: the view settles (${r.turns} direction change(s))`)
               : no(`${name}: the view wobbles — ${r.turns} direction changes`);
}
if (errs.length) { console.log("\nconsole errors:"); errs.forEach((e) => console.log("  " + e)); bad += errs.length; }
console.log(bad ? `\n${bad} problem(s)` : "\npicking an alphaset is one clean move");
await b.close();
process.exit(bad ? 1 : 0);
