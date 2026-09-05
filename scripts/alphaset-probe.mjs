// One alphaset, one result.
//
// An alphaset is what the book lists: a single alpha, or several priced together.
// It has exactly one PnL table. A single alpha appears in every blend it is part
// of, so "light every result this alpha touches" lit three of them — this checks
// the rule that replaced it, and the rest of what a pick is supposed to do.
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8482";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
const errs = [];
p.on("pageerror", (e) => errs.push(String(e)));
await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(1800);
let bad = 0;
const ok = (m) => console.log("  ok   " + m);
const no = (m) => { console.log("  FAIL " + m); bad++; };

const book = await (await fetch(BASE + "/api/alphas")).json();
for (const row of book.alphas) {
  const state = await p.evaluate(async (alpha) => {
    const card = [...document.querySelectorAll("#book .sitem:not(.add)")]
      .find((x) => x.getAttribute("data-alpha") === alpha);
    if (!card) return { missing: true };
    card.click();
    await new Promise((r) => setTimeout(r, 2200));
    const d = window.QANAT.dag;
    const again = [...document.querySelectorAll("#book .sitem:not(.add)")]
      .find((x) => x.getAttribute("data-alpha") === alpha);
    const pnl = d.nodes.filter((n) => !n.dummy && n.layer === "pnl");
    return {
      cardOn: again ? again.classList.contains("on") : false,
      lit: pnl.filter((n) => !n.dim).map((n) => n.id),
      planned: pnl.filter((n) => n.planned).map((n) => n.id),
      litWeights: d.nodes.filter((n) => !n.dummy && n.layer === "weights" && !n.dim)
        .map((n) => n.id),
      sidebar: (document.getElementById("sel-head") || {}).textContent,
      sidebarOpen: document.getElementById("detail").classList.contains("open"),
      runner: !document.getElementById("runner").hidden,
      report: (document.querySelector("#bt-detail h3") || {}).textContent || "",
      empty: (document.querySelector("#bt-detail .bt-empty") || {}).textContent || "",
    };
  }, row.alpha);
  if (state.missing) { no(`${row.name}: no card in the book`); continue; }

  const want = row.pnl;
  const members = (row.reads_weights && row.reads_weights.length
    ? row.reads_weights : [row.writes]).filter(Boolean);

  if (!state.cardOn) no(`${row.name}: the card is not marked as picked`);
  if (state.lit.length !== 1 || state.lit[0] !== want) {
    no(`${row.name}: lit ${JSON.stringify(state.lit)}, should be exactly ["${want}"]`);
  } else ok(`${row.name}: exactly one result lit (${want})`);

  const missingW = members.filter((m) => !state.litWeights.includes(m));
  if (missingW.length) no(`${row.name}: its own weights not lit — ${missingW.join(", ")}`);

  if (row.runs > 0) {
    if (!state.report.includes(row.name)) no(`${row.name}: the report shows "${state.report}"`);
    else ok(`${row.name}: its own report is shown`);
    if (state.sidebar !== want) no(`${row.name}: sidebar shows "${state.sidebar}", want ${want}`);
    else ok(`${row.name}: its result table is open beside it`);
  } else {
    if (!state.planned.includes(want)) no(`${row.name}: no pre-node for ${want}`);
    else ok(`${row.name}: never run — ${want} drawn as a pre-node`);
    if (!state.runner) no(`${row.name}: never run, but no run form offered`);
    else ok(`${row.name}: the run form is offered`);
    if (!state.empty.includes(row.name)) {
      no(`${row.name}: shows another alphaset's numbers — "${(state.report || state.empty).slice(0, 60)}"`);
    } else ok(`${row.name}: no borrowed numbers`);
  }
}
if (errs.length) { console.log("\nconsole errors:"); errs.forEach((e) => console.log("  " + e)); bad += errs.length; }
console.log(bad ? `\n${bad} problem(s)` : "\none alphaset, one result — every card behaves");
await b.close();
process.exit(bad ? 1 : 0);
