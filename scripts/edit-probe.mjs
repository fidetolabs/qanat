// Everything you can change from the console, changed from the console.
//
// There is no edit mode and no settings overlay any more, so each affordance has
// to be reachable where the thing it changes is drawn. This clicks every one of
// them and checks the project on disk actually moved.
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8460";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
const errs = [];
p.on("pageerror", (e) => errs.push(String(e)));
await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(1500);
let bad = 0;
const ok = (m) => console.log("  ok   " + m);
const no = (m) => { console.log("  FAIL " + m); bad++; };

// --- 1. an alpha opens from its own card, and carries how it wants to be run
await p.locator("#book .sitem:not(.add)").first().hover();
const editable = await p.locator("#book .sitem [data-edit]").first();
if (await editable.count()) {
  await editable.click();
  await p.waitForTimeout(600);
  const has = await p.locator("#ae-reb").count() && await p.locator("#ae-decay").count();
  has ? ok("alpha editor has rebalance and decay") : no("alpha editor is missing the run fields");
  await p.locator("#ae-reb").fill("7d");
  await p.locator("#ae-decay").fill("2");
  await p.locator("#ae-save").click();
  await p.waitForTimeout(1200);
  const conds = await (await fetch(BASE + "/api/backtest/conditions")).json();
  const one = conds.alphas.find((a) => a.rebalance === "7d" && a.decay === 2);
  const said = (await p.locator("#ae-say").textContent()) || "";
  one ? ok(`alpha keeps its own gap (${one.name}: 7d, decay 2)`)
      : no(`the alpha did not keep it — the form said: ${said.trim() || "(nothing)"}`);
} else no("no edit affordance on an alpha card");

// --- 2. the form follows the alpha you tick
await p.locator("#btn-bt-run, #btn-run").first().click().catch(() => {});
await p.evaluate(() => document.getElementById("btn-run")?.click() || window.openRunner?.());
await p.waitForTimeout(900);
if (await p.locator(".f-alpha").count()) {
  const boxes = p.locator(".f-alpha");
  const n = await boxes.count();
  for (let i = 0; i < n; i++) await boxes.nth(i).setChecked(false);
  // tick the one that now wants 7d
  const conds = await (await fetch(BASE + "/api/backtest/conditions")).json();
  const want = conds.alphas.find((a) => a.rebalance === "7d") || conds.alphas[0];
  await p.locator(`.f-alpha[value="${want.id}"]`).check();
  await p.waitForTimeout(300);
  const reb = await p.locator("#f-reb").inputValue();
  reb === "7d" ? ok("ticking an alpha fills the gap it asks for")
               : no(`the form said ${reb}, the alpha asks 7d`);
  // costs are askable
  const costs = await p.locator("#f-fee").count() && await p.locator("#f-slip").count() &&
    await p.locator("#f-emb").count() && await p.locator("#f-purge").count();
  costs ? ok("costs, purge and embargo are run variables") : no("costs are not askable");

  // two alphas -> shares appear
  const other = conds.alphas.find((a) => a.id !== want.id);
  await p.locator(`.f-alpha[value="${other.id}"]`).check();
  await p.waitForTimeout(300);
  const shares = await p.locator(".f-share:visible").count();
  shares >= 2 ? ok("a second alpha brings out the shares") : no("shares did not appear");
} else no("the run form has no alpha picker");
await p.locator("#runner .btn-head, #runner button:has-text('close')").first().click().catch(() => {});

// --- 3. a data source can be added, and an existing one edited
await p.evaluate(() => document.getElementById("b-add-src").click());
await p.waitForTimeout(500);
if (await p.locator("#sc-kind").count()) {
  ok("＋ data source opens a connection form");
  await p.locator("#sc-name").fill("probe_feed");
  await p.selectOption("#sc-kind", "synthetic");
  await p.waitForTimeout(200);
  await p.locator(".sc-opt").first().fill("prices");
  await p.locator("#sc-save").click();
  await p.waitForTimeout(1200);
  const g = await (await fetch(BASE + "/api/graph")).json();
  g.tables.some((t) => t.ref === "raw.probe_feed")
    ? ok("the new source is in the graph") : no("the new source never landed");
} else no("＋ data source did not open");

// --- 4. a source shows its connection, and offers to change it
// close whatever is open and refit: the panel narrows the canvas, so a node's
// coordinates from before it opened are not where it is now
await p.evaluate(() => { window.QANAT.closeDetail(); window.QANAT.dag.fit(true); });
await p.waitForTimeout(900);
const src = await p.evaluate(() => {
  const d = window.QANAT.dag;
  const n = d.nodes.find((x) => x.layer === "raw" && !x.dummy);
  const at = d.at(n), r = d.cv.getBoundingClientRect();
  return { x: r.left + at.x + 40, y: r.top + at.y + 20, id: n.id,
           onScreen: at.x > 0 && at.x < d.cv.clientWidth - 60 && at.y > 0 };
});
if (!src.onScreen) no(`${src.id} is not on screen to click`);
await p.mouse.click(src.x, src.y);
await p.waitForTimeout(900);
const body = (await p.locator("#sel-body").textContent()) || "";
const head = (await p.locator("#sel-head").textContent()) || "";
/where these rows come from/i.test(body)
  ? ok(`a source shows its connection (${src.id})`)
  : no(`a source hid its connection — clicked ${src.id}, panel shows "${head.trim()}"`);
if (await p.locator("#conn-edit").count()) {
  await p.locator("#conn-edit").click();
  await p.waitForTimeout(500);
  (await p.locator("#sc-kind").count()) ? ok("its connection opens for editing")
                                        : no("edit connection did nothing");
} else no("a source has no edit affordance");

// --- 5. a stage opens from the rail and closes on a second click
const rail = await p.evaluate(async () => {
  const d = window.QANAT.dag, out = [];
  for (const b of (d.bands || []).filter((x) => x.id)) {
    d.onStage(b);
    await new Promise((r) => setTimeout(r, 150));
    const open = document.getElementById("detail").classList.contains("open");
    const head = document.getElementById("sel-head").textContent;
    d.onStage(b);
    await new Promise((r) => setTimeout(r, 120));
    const shut = !document.getElementById("detail").classList.contains("open");
    out.push({ id: b.id, open, head, shut });
  }
  return out;
});
const railBad = rail.filter((r) => !r.open || r.head !== r.id || !r.shut);
railBad.length ? no(`stage rail: ${JSON.stringify(railBad)}`)
               : ok(`every stage opens and unclicks (${rail.map((r) => r.id).join(", ")})`);

// --- 6. nothing that was removed is still being asked for
for (const gone of ["#btn-edit", "#editor", "#book-edit"]) {
  (await p.locator(gone).count()) ? no(`${gone} is still in the page`) : ok(`${gone} is gone`);
}

if (errs.length) { console.log("\nconsole errors:"); errs.forEach((e) => console.log("  " + e)); bad += errs.length; }
console.log(bad ? `\n${bad} problem(s)` : "\nevery editing path works");
await b.close();
process.exit(bad ? 1 : 0);
