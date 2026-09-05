/* UX probe — clicks every alpha, every table, and the combinations, and reports
 * anything that moves when it should not.
 *
 * The graph is what this guards. A poll that refits it, or a panel that rescales
 * it, makes the pipeline impossible to look at closely — bugs that are invisible
 * in a screenshot and obvious here.
 *
 *   qanat serve --port 8428 &
 *   BASE=http://127.0.0.1:8428 node scripts/ux-probe.mjs
 */
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8428";
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1470, height: 820 } });
const notes = [];
const say = (t) => { notes.push(t); console.log(t); };

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForSelector("#book .sitem:not(.add)", { timeout: 15000 });
await page.waitForTimeout(1200);

const view = () => page.evaluate(() => {
  const d = window.QANAT.dag;
  return { k: +d.view.k.toFixed(4), x: +d.view.x.toFixed(1), y: +d.view.y.toFixed(1),
           nodes: d.nodes.length, dim: d.nodes.filter(n => n.dim).length,
           sel: d.selected, focus: d.focusKey };
});

// 1 — does the graph hold still on its own?
const a = await view();
await page.waitForTimeout(3200);          // longer than the 2.5s poll
const b2 = await view();
say(`idle 3.2s : k ${a.k}->${b2.k}  x ${a.x}->${b2.x}  ${a.k===b2.k&&a.x===b2.x ? "steady" : "MOVED"}`);

// 2 — does a pan survive a poll?
await page.mouse.move(700, 300); await page.mouse.down();
await page.mouse.move(560, 300, { steps: 8 }); await page.mouse.up();
const panned = await view();
await page.waitForTimeout(3200);
const after = await view();
say(`pan then wait : x ${panned.x}->${after.x}  ${Math.abs(panned.x-after.x)<1 ? "kept" : "SNAPPED BACK"}`);

// 3 — does a zoom survive a poll?
await page.mouse.move(700, 300);
await page.mouse.wheel(0, -300);
const zoomed = await view();
await page.waitForTimeout(3200);
const after2 = await view();
say(`zoom then wait: k ${zoomed.k}->${after2.k}  ${Math.abs(zoomed.k-after2.k)<0.001 ? "kept" : "SNAPPED BACK"}`);

await page.locator("#b-fit").click();   // hand the view back before the click tests
await page.waitForTimeout(500);

// 4 — every alpha
const alphas = await page.locator("#book .sitem:not(.add)").count();
for (let i = 0; i < alphas; i++) {
  await page.locator("#book .sitem:not(.add)").nth(i).click();
  await page.waitForTimeout(900);
  const v = await view();
  const name = (await page.locator("#book .sitem:not(.add)").nth(i).locator(".code").textContent()).trim();
  say(`alpha ${name.padEnd(18)} nodes ${v.nodes} dim ${v.dim} focus ${v.focus ? "yes" : "no"} k ${v.k}`);
  if (v.dim === 0) say(`  ! ${name}: nothing dimmed — focus did not apply`);
  if (v.dim === v.nodes) say(`  ! ${name}: everything dimmed`);
}

// 5 — every node, with an alpha still focused
// waypoints are routing artefacts, not boxes: they are not drawn and not clickable
const ids = await page.evaluate(() =>
  window.QANAT.dag.nodes.filter(n => !n.dummy).map(n => n.id));
for (const id of ids) {
  const hit = await page.evaluate((ref) => {
    const d = window.QANAT.dag, n = d.byId[ref];
    // the node list changes as focus changes -- a result that does not exist yet
    // is only drawn while its alphaset is picked
    if (!n) return { gone: true };
    const at = d.at(n), r = document.getElementById("dagcv").getBoundingClientRect();
    const x = r.left + at.x + 40, y = r.top + at.y + 20;
    // a box scrolled out of the band cannot be clicked, and clicking where it
    // would have been hits whatever is underneath — not a fault, but not a test
    const inside = at.x > 0 && at.x < r.width - 20 && at.y > 0 && at.y < r.height - 20;
    return { x, y, inside };
  }, id);
  if (hit.gone) { say(`node ${id.padEnd(26)} not drawn now — skipped`); continue; }
  if (!hit.inside) { say(`node ${id.padEnd(26)} off view — skipped`); continue; }
  const before = await view();
  await page.mouse.click(hit.x, hit.y);
  await page.waitForTimeout(700);
  const head = (await page.locator("#sel-head").textContent()).trim();
  const open = await page.locator("#detail.open").count();
  const v = await view();
  const moved = Math.abs(before.k - v.k) > 0.001;
  say(`node ${id.padEnd(26)} panel ${open ? "open " : "shut "} head ${head.padEnd(26)}` +
      `${head === id ? "" : " ! head mismatch"}${moved ? ` ! graph rescaled ${before.k}->${v.k}` : ""}`);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(600);   // longer than the graph's settle, or a click misses
}

// 6 — alpha then node then alpha
await page.locator("#book .sitem:not(.add)").nth(0).click(); await page.waitForTimeout(600);
const f1 = (await view()).focus;
// pick a table that IS part of the alpha now focused: clicking inside the
// lineage must keep the pick, clicking outside it must let the pick go. Both
// were asked for, and they are different rules -- the probe has to say which
// one it is testing.
const inLineage = await page.evaluate(() => {
  const d = window.QANAT.dag;
  const n = d.nodes.find((x) => !x.dummy && !x.dim && x.layer !== "pnl");
  return n ? n.id : null;
});
const outside = await page.evaluate(() => {
  const d = window.QANAT.dag;
  const n = d.nodes.find((x) => !x.dummy && x.dim);
  return n ? n.id : null;
});
const first = inLineage || ids[2];
const hit = first ? await page.evaluate((ref) => {
  const d = window.QANAT.dag, n = d.byId[ref];
  if (!n) return null;
  const at = d.at(n), r = document.getElementById("dagcv").getBoundingClientRect();
  return { x: r.left + at.x + 40, y: r.top + at.y + 20 };
}, first) : null;
if (!hit) say("  ! no lit table to click — skipping the focus check");
if (hit) { await page.mouse.click(hit.x, hit.y); await page.waitForTimeout(700); }
const f2 = hit ? (await view()).focus : f1;
await page.locator("#book .sitem:not(.add)").nth(1).click(); await page.waitForTimeout(700);
const f3 = await view();
say(`focus across clicks: ${f1} -> after node in lineage (${first}) ${f2} -> after 2nd alpha ${f3.focus}`);
say(`  ${f1 === f2 ? "a table inside the lineage kept the pick" : "! a table inside the lineage cleared the pick"}`);

// and a table outside it must let the pick go
if (outside) {
  await page.locator("#book .sitem:not(.add)").nth(0).click();
  await page.waitForTimeout(600);
  const hit2 = await page.evaluate((ref) => {
    const d = window.QANAT.dag, n = d.byId[ref];
    const at = d.at(n), r = document.getElementById("dagcv").getBoundingClientRect();
    return { x: r.left + at.x + 40, y: r.top + at.y + 20 };
  }, outside);
  await page.mouse.click(hit2.x, hit2.y);
  await page.waitForTimeout(700);
  const f4 = (await view()).focus;
  say(`  ${f4 ? "! a table outside the lineage kept the pick lit" : `a table outside it (${outside}) let the pick go`}`);
}

await b.close();
