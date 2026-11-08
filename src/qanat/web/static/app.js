// qanat's pages. Plain JS with nothing to compile: every view asks /api and draws the answer.
// No logic lives here beyond drawing -- the service layer decides everything.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const h = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let HEALTH = { can: {} };

// ---- the API -----------------------------------------------------------------
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = sessionStorage.getItem("qanat_token");
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) {
    const t = prompt("This server needs its token.");
    if (t) { sessionStorage.setItem("qanat_token", t); return api(path, opts); }
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status}`);
  return body;
}
const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b) });
const put = (p, b) => api(p, { method: "PUT", body: JSON.stringify(b) });
const del = (p) => api(p, { method: "DELETE" });

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = bad ? "toast bad" : "toast"; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 4000);
}
const fail = (e) => toast(e.message, true);
const num = (v, d = 4) => (v === null || v === undefined ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));
const mark = (on, label) => `<span class="mark ${on ? "yes" : "no"}">${h(label)}</span>`;
const lock = (what) => (HEALTH.can[what] ? "" : `disabled title="needs a wider --scope"`);

// ---- routing -------------------------------------------------------------------
const VIEWS = { "": home, filings, holdings, metrics, filters, tables };
async function route() {
  const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === name));
  const view = $("#view");
  view.innerHTML = `<p class="muted">Loading…</p>`;
  try { await (VIEWS[name] || home)(view, rest.map(decodeURIComponent)); }
  catch (e) { view.innerHTML = `<p class="err">${h(e.message)}</p>`; }
}

// ---- home ----------------------------------------------------------------------
async function home(view) {
  const [t, m, f, c] = await Promise.all([
    api("/api/tables"), api("/api/metrics").catch(() => ({ metrics: [] })),
    api("/api/filters").catch(() => ({ filters: [] })), api("/api/companies?limit=1").catch(() => ({ total: 0 })),
  ]);
  const rows = t.tables.reduce((n, x) => n + (x.rows || 0), 0);
  view.innerHTML = `
    <h1>${h(t.project)}</h1>
    <p class="lede">What this Qanat holds, what it processes, and what comes out.</p>
    <div class="grid">
      <a class="card stat" href="#/holdings"><b>${num(c.total, 0)}</b>companies held</a>
      <a class="card stat" href="#/tables"><b>${num(t.tables.length, 0)}</b>tables · ${num(rows, 0)} rows</a>
      <a class="card stat" href="#/metrics"><b>${num(m.metrics.length, 0)}</b>metrics</a>
      <a class="card stat" href="#/filters"><b>${num(f.filters.length, 0)}</b>saved filters</a>
    </div>
    <h2>The pipeline</h2>
    <div class="card"><table><tr><th>Stage</th><th>Table</th><th>Written by</th><th class="num">Rows</th></tr>
      ${t.tables.map((x) => `<tr><td>${h(x.stage)} <span class="muted">${h(x.stage_kind || "")}</span></td>
        <td class="mono"><a href="#/tables/${encodeURIComponent(x.ref)}">${h(x.ref)}</a></td>
        <td class="mono">${h(x.written_by.join(", "))}</td><td class="num">${x.written ? num(x.rows, 0) : "not yet"}</td></tr>`).join("")}
    </table></div>`;
}

// ---- find & ingest (T-02) --------------------------------------------------------
async function filings(view) {
  view.innerHTML = `
    <h1>Find &amp; ingest</h1>
    <p class="lede">See what EDGAR holds, then take the filings you pick. Every form is found; only 10-K and 10-Q can be ingested.</p>
    <form class="row card" id="f">
      <input name="companies" placeholder="AAPL, MSFT or a CIK" size="28" required>
      <select name="form"><option value="">any form</option><option>10-K</option><option>10-Q</option><option>8-K</option><option>10-K/A</option></select>
      <input name="from" type="date" title="from"> <input name="to" type="date" title="to">
      <button ${lock("find")}>Find</button>
    </form>
    <div id="out"></div>`;
  $("#f").onsubmit = async (ev) => {
    ev.preventDefault();
    const p = new URLSearchParams([...new FormData(ev.target)].filter(([, v]) => v));
    const out = $("#out");
    out.innerHTML = `<p class="muted">Asking EDGAR…</p>`;
    try { drawFound(out, await api(`/api/filings/find?${p}`)); } catch (e) { out.innerHTML = `<p class="err">${h(e.message)}</p>`; }
  };
}

function drawFound(out, r) {
  if (!r.filings.length) { out.innerHTML = `<p class="empty">Nothing filed for that in that period.</p>`; return; }
  out.innerHTML = `
    <div class="card">
      <div class="row"><b>${r.found} found</b><span class="muted">${h(r.ingestable_note || "")}</span></div>
      <table><tr><th></th><th>Company</th><th>Form</th><th>Filed</th><th>Period</th><th>Ingestable</th><th>Held</th></tr>
      ${r.filings.map((f, i) => {
        const can = f.ingestable === true || f.ingestable === "yes" || f.pile === 1;
        const held = f.ingested_here;
        return `<tr><td>${can && !held ? `<input type="checkbox" data-i="${i}">` : ""}</td>
          <td>${h(f.company)} <span class="muted">${h(f.company_name || "")}</span></td><td>${h(f.form)}</td>
          <td class="mono">${h(f.filed)}</td><td class="mono">${h(f.period_end || "—")}</td>
          <td>${mark(can, can ? "yes" : String(f.ingestable || "no"))}</td><td>${mark(held, held ? "held" : "—")}</td></tr>`;
      }).join("")}</table>
      <div class="row" style="margin-top:12px"><button id="go" ${lock("ingest")}>Ingest selected</button><span id="res" class="muted"></span></div>
    </div>`;
  $("#go").onclick = async () => {
    const picked = [...out.querySelectorAll("input[type=checkbox]:checked")].map((c) => r.filings[c.dataset.i]);
    if (!picked.length) return toast("Pick at least one filing.", true);
    $("#go").disabled = true; $("#res").textContent = `Ingesting ${picked.length}…`;
    try {
      const done = await post("/api/filings/ingest", { filings: picked.map((f) => ({ company: f.company, accession: f.accession })) });
      $("#res").innerHTML = `${done.landed} landed, ${done.refused} refused` +
        done.filings.filter((x) => !x.ingested).map((x) => `<div class="err">${h(x.accession)}: ${h(x.reason)}</div>`).join("");
      toast(`${done.landed} filing(s) landed`);
    } catch (e) { fail(e); } finally { $("#go").disabled = false; }
  };
}

// ---- what I hold (T-03) ------------------------------------------------------------
async function holdings(view, [filer]) {
  const c = await api("/api/companies?limit=200");
  view.innerHTML = `
    <h1>What I hold</h1>
    <p class="lede">Every company with filings here, and how far each filing has got: raw → facts → sections → metrics.</p>
    <div class="split">
      <div class="card list">${c.companies.length ? c.companies.map((x) => `<a href="#/holdings/${encodeURIComponent(x.cik)}" class="${x.cik === filer ? "on" : ""}">${h(x.ticker || x.cik)} <span class="muted">${x.filings}</span></a>`).join("") : `<p class="empty">Nothing held yet. <a href="#/filings">Find some filings</a>.</p>`}</div>
      <div id="one">${filer ? "" : `<p class="muted">Pick a company.</p>`}</div>
    </div>`;
  if (!filer) return;
  const p = await api(`/api/companies/${encodeURIComponent(filer)}/filings`);
  $("#one").innerHTML = `
    <div class="card">
      <h2 style="margin-top:0">${h(p.filer.name || p.filer.ticker || p.filer.cik)} <span class="muted mono">${h(p.filer.cik)}</span></h2>
      <table><tr><th>Filed</th><th>Form</th><th>Period</th><th>Raw</th><th>Facts</th><th>Sections</th><th>Metrics</th></tr>
      ${p.filings.map((f) => `<tr><td class="mono">${h(f.filed)}</td><td>${h(f.form)}</td><td class="mono">${h(f.period_end || "—")}</td>
        <td>${mark(f.raw, "raw")}</td><td>${mark(f.facts > 0, `${num(f.facts, 0)} facts`)}</td>
        <td>${f.sections === null ? `<span class="muted">n/a</span>` : mark(f.sections > 0, `${f.sections} sections`)}</td>
        <td>${mark(f.metrics.length > 0, `${f.metrics.length} of ${p.metrics.length}`)}
          <div class="muted mono">${h(f.metrics.join(", "))}</div></td></tr>`).join("")}
      </table>
    </div>`;
}

// ---- metrics (T-04) ------------------------------------------------------------------
async function metrics(view, [picked]) {
  const m = await api("/api/metrics");
  const one = m.metrics.find((x) => x.id === picked);
  if (one) one.sql = (await api(`/api/metrics/${encodeURIComponent(one.id)}/formula`).catch(() => ({}))).sql || "";
  view.innerHTML = `
    <h1>Metrics</h1>
    <p class="lede">Each metric is a table filled by its formula. Define one, and trace any value back to the facts under it.</p>
    <div class="split">
      <div class="card list">${m.metrics.map((x) => `<a href="#/metrics/${encodeURIComponent(x.id)}" class="${x.id === picked ? "on" : ""}">${h(x.id)}</a>`).join("") || `<p class="empty">None defined.</p>`}
        <a href="#/metrics" class="${picked ? "" : "on"}">+ new metric</a></div>
      <div>
        <form class="card" id="def">
          <div class="row"><input name="id" placeholder="id, e.g. pe" value="${h(one?.id || "")}" required ${one ? "readonly" : ""}>
            <input name="unit" placeholder="unit" value="${h(one?.unit || "")}" size="10">
            <input name="description" placeholder="what the number means" value="${h(one?.description || "")}" style="flex:1"></div>
          <p class="muted">SQL selecting <code>subject</code>, <code>date</code> and <code>value</code>. Name tables as <code>normalized__facts</code>.</p>
          <textarea name="sql" placeholder="SELECT subject, date, value FROM …" required>${h(one?.sql || "")}</textarea>
          <div class="row"><button ${lock("define_metric")}>${one ? "Replace formula" : "Define"}</button>
            ${one ? `<button type="button" class="danger" id="rm" ${lock("remove_metric")}>Remove</button>` : ""}</div>
        </form>
        ${one ? `<div class="card"><div class="row"><b>${h(one.table)}</b><span class="muted">reads ${h(one.reads.join(", ") || "—")}</span></div>
          <form class="row" id="tr" style="margin-top:8px"><input name="company" placeholder="ticker or CIK" required><input name="as_of" type="date" required><button class="ghost">Trace a value</button></form>
          <div id="chain" class="chain"></div></div>` : ""}
      </div>
    </div>`;
  $("#def").onsubmit = async (ev) => {
    ev.preventDefault();
    const b = Object.fromEntries(new FormData(ev.target));
    try { await post("/api/metrics", b); toast(`Metric ${b.id} saved and run`); location.hash = `#/metrics/${b.id}`; route(); } catch (e) { fail(e); }
  };
  if ($("#rm")) $("#rm").onclick = async () => {
    const used = (await api("/api/filters")).filters.filter((f) => f.clauses.some((c) => c.metric === one.id)).map((f) => f.id);
    const warn = used.length ? `\nThe saved filter(s) ${used.join(", ")} name it and will stop running.` : "";
    if (!confirm(`Remove ${one.id}? Its values stay readable.${warn}`)) return;
    try { await del(`/api/metrics/${encodeURIComponent(one.id)}?force=true`); toast(`Removed ${one.id}`); location.hash = "#/metrics"; } catch (e) { fail(e); }
  };
  if ($("#tr")) $("#tr").onsubmit = async (ev) => {
    ev.preventDefault();
    const p = new URLSearchParams(new FormData(ev.target));
    try {
      const t = await api(`/api/metrics/${encodeURIComponent(one.id)}/trace?${p}`);
      $("#chain").innerHTML = `
        <div><b>${num(t.value)}</b> <span class="muted">${h(t.metric)} for ${h(t.filer.ticker || t.filer.cik)} as of ${h(t.as_of)}</span></div>
        <div>computed from ${t.computed_from.map((u) => `<code>${h(u.from)}</code>`).join(", ") || "—"}</div>
        <div>${t.filing ? `the filing: ${h(t.filing.form)} filed ${h(t.filing.filed)} <span class="muted mono">${h(t.filing.document)}</span>` : "no filing at or before that date"}</div>
        ${(t.facts.facts || []).length ? `<table><tr><th>Concept</th><th class="num">Value</th><th>Unit</th><th>Period</th></tr>
          ${t.facts.facts.slice(0, 20).map((f) => `<tr><td class="mono">${h(f.concept)}</td><td class="num">${num(f.value)}</td><td>${h(f.unit || "")}</td><td class="mono">${h(f.period_end || f.period || "")}</td></tr>`).join("")}</table>` : ""}`;
    } catch (e) { fail(e); }
  };
}

// ---- filters (T-06) -------------------------------------------------------------------
async function filters(view, [picked]) {
  const [f, m, cov] = await Promise.all([api("/api/filters"), api("/api/metrics"), api("/api/coverage").catch(() => ({}))]);
  const one = f.filters.find((x) => x.id === picked);
  const ids = m.metrics.map((x) => x.id);
  const ops = [">=", ">", "<=", "<", "==", "!="];
  const clauseRow = (c = {}) => `<div class="clause">
      <select name="metric">${ids.map((i) => `<option ${i === c.metric ? "selected" : ""}>${h(i)}</option>`).join("")}</select>
      <select name="op">${ops.map((o) => `<option ${o === c.op ? "selected" : ""}>${h(o)}</option>`).join("")}</select>
      <input name="value" inputmode="decimal" pattern="-?[0-9]*[.]?[0-9]+([eE]-?[0-9]+)?" value="${h(c.value ?? "")}" required size="8">
      <button type="button" class="ghost" data-x>×</button></div>`;
  view.innerHTML = `
    <h1>Filters</h1>
    <p class="lede">Saved clauses in <code>filters.yaml</code>, run against the stored metric values. Run before you save to see the list.</p>
    <div class="split">
      <div class="card list">${f.filters.map((x) => `<a href="#/filters/${encodeURIComponent(x.id)}" class="${x.id === picked ? "on" : ""}">${h(x.id)}</a>`).join("") || `<p class="empty">None saved.</p>`}
        <a href="#/filters" class="${picked ? "" : "on"}">+ new filter</a></div>
      <div>
        <form class="card" id="ff">
          <div class="row"><input name="id" placeholder="id, e.g. quality_compounders" value="${h(one?.id || "")}" required ${one ? "readonly" : ""}>
            <input name="description" placeholder="what it is for" value="${h(one?.description || "")}" style="flex:1"></div>
          <h2>Clauses <span class="muted">— every one has to pass</span></h2>
          <div id="clauses">${(one?.clauses || [{}]).map(clauseRow).join("")}</div>
          <button type="button" class="ghost" id="add" ${ids.length ? "" : "disabled"}>+ clause</button>
          <h2>As of</h2>
          <div class="row"><input name="as_of" type="date" required min="${h(cov.from || "")}" max="${h(cov.to || "")}" value="${h(cov.to || "")}">
            <span class="muted">${cov.from ? `the corpus answers ${h(cov.from)} to ${h(cov.to)}` : ""}</span></div>
          <div class="row" style="margin-top:12px">
            <button type="button" class="ghost" id="try" ${lock("screen")}>Run before save</button>
            <button id="save" ${lock("save_filter")}>Save</button>
            ${one ? `<button type="button" id="runsaved" class="ghost" ${lock("screen")}>Run saved</button>
                     <button type="button" class="danger" id="rm" ${lock("remove_filter")}>Remove</button>` : ""}
          </div>
        </form>
        <div id="result"></div>
      </div>
    </div>`;
  if (!ids.length) $("#clauses").innerHTML = `<p class="empty">Define a metric first — a clause compares one.</p>`;
  const form = $("#ff");
  $("#add").onclick = () => $("#clauses").insertAdjacentHTML("beforeend", clauseRow());
  $("#clauses").onclick = (ev) => { if (ev.target.dataset.x !== undefined) ev.target.closest(".clause").remove(); };
  const clauses = () => [...form.querySelectorAll(".clause")].map((c) => ({
    metric: $("[name=metric]", c).value, op: $("[name=op]", c).value, value: Number($("[name=value]", c).value) }));
  const show = (r) => drawScreen($("#result"), r);
  $("#try").onclick = async () => {
    try { show(await post("/api/screen", { clauses: clauses(), as_of: form.as_of.value })); } catch (e) { fail(e); }
  };
  if ($("#runsaved")) $("#runsaved").onclick = async () => {
    try { show(await post("/api/screen", { filter: one.id, as_of: form.as_of.value })); } catch (e) { fail(e); }
  };
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      const out = await put(`/api/filters/${encodeURIComponent(form.id.value)}`, { clauses: clauses(), description: form.description.value });
      toast(`${out.replaced ? "Replaced" : "Saved"} ${out.filter.id} in filters.yaml`);
      location.hash = `#/filters/${out.filter.id}`;
    } catch (e) { fail(e); }
  };
  if ($("#rm")) $("#rm").onclick = async () => {
    if (!confirm(`Remove ${one.id} from filters.yaml?`)) return;
    try { await del(`/api/filters/${encodeURIComponent(one.id)}`); toast(`Removed ${one.id}`); location.hash = "#/filters"; } catch (e) { fail(e); }
  };
}

function drawScreen(el, r) {
  const count = r.counts[0] || {};
  const cols = r.applied.map((a) => a.metric + (a.metric_version ? `@${a.metric_version}` : ""));
  const rows = r.rows.filter((x) => x.passed);
  el.innerHTML = `<div class="card">
    <div class="row"><b>${count.passed ?? 0} passed</b>
      <span class="muted">of ${count.evaluated ?? 0} evaluated · ${count.dropped ?? 0} could not be judged · ${r.filter ? `filter ${h(r.filter)}` : "unsaved clauses"} · as of ${h(count.as_of || "")}</span></div>
    ${rows.length ? `<table><tr><th>Ticker</th><th>Name</th>${cols.map((c) => `<th class="num">${h(c)}</th>`).join("")}</tr>
      ${rows.map((x) => `<tr><td class="mono">${h(x.ticker || x.subject)}</td><td>${h(x.name || "")}</td>${cols.map((c) => `<td class="num">${num(x.values[c])}</td>`).join("")}</tr>`).join("")}
    </table>` : `<p class="empty">${count.nothing_evaluable ? "Nothing could be judged on that date." : "Nobody passed."}</p>`}
  </div>`;
}

// ---- tables (T-07) --------------------------------------------------------------------
async function tables(view, [ref]) {
  const t = await api("/api/tables");
  const byStage = {};
  t.tables.forEach((x) => (byStage[x.stage] = byStage[x.stage] || []).push(x));
  view.innerHTML = `
    <h1>Tables</h1>
    <p class="lede">Every table by stage. Pick one to see its columns and its rows, as they stood on a date if you give one.</p>
    <div class="split">
      <div class="card list">${Object.entries(byStage).map(([s, xs]) => `<div class="muted" style="margin-top:6px">${h(s)}</div>` +
        xs.map((x) => `<a href="#/tables/${encodeURIComponent(x.ref)}" class="${x.ref === ref ? "on" : ""}">${h(x.ref.split(".")[1])} <span class="muted">${x.written ? num(x.rows, 0) : "—"}</span></a>`).join("")).join("")}</div>
      <div id="one">${ref ? `<p class="muted">Loading…</p>` : `<p class="muted">Pick a table.</p>`}</div>
    </div>`;
  if (!ref) return;
  const draw = async (asOf) => {
    const one = $("#one");
    try {
      const d = await api(`/api/tables/${encodeURIComponent(ref)}?limit=50${asOf ? `&as_of=${asOf}` : ""}`);
      const rows = d.sample.sample;
      const cols = rows.length ? Object.keys(rows[0]) : d.table.columns.map((c) => c.name);
      one.innerHTML = `<div class="card">
        <div class="row"><b class="mono">${h(ref)}</b><span class="muted">${num(d.table.rows, 0)} rows · clock <code>${h(d.table.time_column || "none")}</code> · newest ${h(d.table.newest || "—")}</span></div>
        <form class="row" id="asof" style="margin:8px 0"><input name="as_of" type="date" value="${h(asOf || "")}"><button class="ghost">Rows as of</button>
          <a href="#/tables/${encodeURIComponent(ref)}">clear</a></form>
        <div style="overflow:auto"><table><tr>${cols.map((c) => `<th>${h(c)}</th>`).join("")}</tr>
          ${rows.map((r) => `<tr>${cols.map((c) => `<td class="mono">${h(typeof r[c] === "object" && r[c] !== null ? JSON.stringify(r[c]) : r[c])}</td>`).join("")}</tr>`).join("") || `<tr><td class="empty">No rows${asOf ? " existed then" : ""}.</td></tr>`}
        </table></div>
        <details style="margin-top:8px"><summary>Columns</summary>
          <table>${d.table.columns.map((c) => `<tr><td class="mono">${h(c.name)}</td><td class="muted">${h(c.type)}</td></tr>`).join("")}</table></details>
      </div>`;
      $("#asof").onsubmit = (ev) => { ev.preventDefault(); draw(ev.target.as_of.value); };
    } catch (e) { one.innerHTML = `<p class="err">${h(e.message)}</p>`; }
  };
  draw("");
}

// ---- start ------------------------------------------------------------------------
(async () => {
  try {
    HEALTH = await api("/api/health");
    $("#project").textContent = HEALTH.project;
    $("#scope").textContent = `scope ${HEALTH.scope}`;
  } catch (e) { fail(e); }
  window.addEventListener("hashchange", route);
  route();
})();
