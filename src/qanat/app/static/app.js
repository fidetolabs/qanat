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
  // No project selected and none to select: every page but Settings is empty,
  // so go there and say why.
  if (res.status === 409 && body.no_project) {
    if (!location.hash.startsWith("#/settings")) location.hash = "#/settings/empty";
    throw new Error(body.detail);
  }
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

// One pass of the transforms over what has landed: facts, sections, metrics.
async function runTransforms(btn, out) {
  btn.disabled = true; out.textContent = "Running the transforms…";
  try {
    const r = await post("/api/run", {});
    out.innerHTML = r.results.map((x) => `<div class="${x.status === "ok" ? "yes" : "no"}">${h(x.definition)}: ${h(x.status)} · ${num(x.rows, 0)} rows${x.error ? ` · ${h(x.error)}` : ""}</div>`).join("");
    toast(r.ok ? "Transforms ran" : "Some transforms failed", !r.ok);
  } catch (e) { fail(e); } finally { btn.disabled = false; }
}

// ---- the pipeline ---------------------------------------------------------------
//
// **What is running, what is meant to run, and what happened.** Every other page
// answers what came out. A definition and a schedule are two objects and stay two
// here -- the page puts them side by side, which is a display and not a model.
async function pipeline(view) {
  const [defs, scheds, execs] = await Promise.all([
    api("/api/definitions"), api("/api/schedules"), api("/api/executions?limit=40"),
  ]);
  // Each definition already carries the cadences pointing at it. `/api/schedules`
  // is read for one thing the project cannot know: whether a clock is attached in
  // *this* process. A schedule is declared in the project; a clock is a property
  // of the program that is running.
  const rows = [
    ...(defs.ingestion || []).map((d) => ({ ...d, kind: "ingestion" })),
    ...(defs.transforms || []).map((d) => ({ ...d, kind: "transform" })),
  ];
  const cadence = (d) => (d.schedules || []).length
    ? d.schedules.map((s) => `<span class="mono">${h(s.cron)}</span>${s.enabled === false ? ` <span class="muted">(off)</span>` : ""}`).join("<br>")
    : `<span class="muted">no schedule</span>`;
  // **`behind` is the one worth reading.** A stored table only knows the days
  // its last run covered, and a read past that quietly returns an older value --
  // so a definition whose inputs have moved past its output says so here.
  const filled = (d) => {
    if (!d.filled_to) return `<span class="muted">—</span>`;
    const to = `<span class="mono">${h(d.filled_to)}</span>`;
    return d.behind ? `${to} <span class="no">behind ${h(d.inputs_to)}</span>` : to;
  };
  view.innerHTML = `
    <h1>The pipeline</h1>
    <p class="lede">Every definition, the schedules pointing at it, and what has actually run.</p>
    <p class="${scheds.clock_attached ? "yes" : "muted"}">${h(scheds.note)}</p>
    <div class="card">
      <table><tr><th>Definition</th><th>Kind</th><th>Reads</th><th>Writes</th><th>Schedules</th><th>Filled</th></tr>
      ${rows.length ? rows.map((d) => `<tr>
        <td class="mono">${h(d.id)}</td><td>${h(d.kind)}</td>
        <td class="mono muted">${h((d.from || []).join(", ") || (d.connector ? `${d.connector} feed` : "—"))}</td>
        <td class="mono">${h((d.to || []).join(", "))}</td>
        <td>${cadence(d)}</td>
        <td>${filled(d)}</td></tr>`).join("")
        : `<tr><td colspan="6"><span class="empty">No definitions.</span></td></tr>`}
      </table>
    </div>
    <h2>What has run</h2>
    <div class="card">
      <table><tr><th>Kind</th><th>Definition</th><th>Attempt</th><th>Status</th><th>Rows</th><th>Ended</th></tr>
      ${(execs.executions || []).length ? execs.executions.map((e) => `<tr>
        <td>${h(e.kind || "")}</td><td class="mono">${h(e.definition_id || "")}</td>
        <td class="mono">${h(String(e.attempt ?? ""))}</td>
        <td class="${e.status === "ok" ? "yes" : "no"}">${h(e.status || "")}</td>
        <td class="mono">${num(e.rows_out, 0)}</td>
        <td class="mono muted">${h(e.ended_at || "")}</td></tr>
        ${e.error ? `<tr><td></td><td colspan="5"><details><summary class="no">${h(String(e.error).split("\n")[0].slice(0, 160))}</summary><pre class="trace">${h(e.error)}</pre></details></td></tr>` : ""}`).join("")
        : `<tr><td colspan="6"><span class="empty">Nothing has run yet.</span></td></tr>`}
      </table>
    </div>`;
}


// ---- settings (T-16) --------------------------------------------------------------
//
// **Projects live in one home folder, and the app works in one of them.** The
// list is the folders; the status is read live -- running and which job, idle,
// in use by another program, or closed. Delete asks for the name typed back.
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "never");
const statusMark = (p) => {
  const cls = p.status.replace(" ", "-");
  const jobs = (p.running || []).map((r) => r.job).join(", ");
  const who = p.held_by ? ` · pid ${h(p.held_by.pid)}${p.held_by.serving ? ` · ${h(p.held_by.serving)}` : ""}` : "";
  return `<span class="status ${cls}">${h(p.status)}</span>${jobs ? ` <span class="mono">${h(jobs)}</span>` : ""}<span class="muted mono">${who}</span>`;
};

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme || "fidetolabs-white";
}

async function settings(view, rest = []) {
  const [list, conf] = await Promise.all([api("/api/projects"), api("/api/settings")]);
  const manages = HEALTH.manages_projects !== false;
  const empty = !list.projects.length;
  const sel = list.selected;
  const ua = conf.user_agent || {};
  view.innerHTML = `
    <h1>Settings</h1>
    <p class="lede">${manages ? `Projects in <code>${h(list.home)}</code>, the one used last first.` : "This server serves one project and has no home folder."}</p>
    ${empty || rest[0] === "empty" && !sel ? `<div class="notice"><b>There is no project to open.</b> Create a new project to start.</div>` : ""}
    <h2>Projects</h2>
    <div class="card">
      <table><tr><th>Project</th><th>Status</th><th>Last used</th><th></th></tr>
      ${list.projects.map((p) => `<tr class="${p.name === sel ? "sel" : ""}">
        <td class="mono">${h(p.name)}${p.name === sel ? ` <span class="muted">· working here</span>` : ""}</td>
        <td>${statusMark(p)}</td>
        <td class="mono muted">${h(when(p.last_used))}</td>
        <td style="text-align:right">
          ${p.name === sel ? "" : `<button class="ghost" data-open="${h(p.name)}" ${p.status === "in use" ? "disabled" : ""}>Open</button>`}
          ${manages && p.name !== sel ? `<button class="danger" data-del="${h(p.name)}" ${lock("delete_project")}>Delete</button>` : ""}
        </td></tr>`).join("") || `<tr><td colspan="4"><span class="empty">No projects yet.</span></td></tr>`}
      </table>
      ${manages ? `<form class="row" id="new" style="margin-top:12px">
        <input name="name" placeholder="new-project-name" pattern="[a-z0-9][a-z0-9_\-]*" required size="28">
        <button ${lock("create_project")}>Create a new project</button>
        <span class="muted">lowercase letters, digits, - and _</span></form>` : ""}
      <div id="del"></div>
    </div>
    ${sel ? `<h2>SEC contact for ${h(sel)}</h2>
    <form class="card row" id="ua">
      <input name="user_agent" size="42" placeholder="Jane Doe jane@example.com" value="${h(ua.user_agent || "")}">
      <button ${lock("user_agent")}>Save</button>
      <span class="muted">${ua.user_agent ? "sent with every SEC request from this project" : ua.shell ? `not set here · the shell's <code>${h(ua.shell)}</code> is used` : "not set · the SEC refuses requests without one"}</span>
    </form>` : ""}
    <h2>Theme</h2>
    <div class="card themes">
      ${conf.themes.map((t) => `<label class="${t === conf.theme ? "on" : ""}"><input type="radio" name="theme" value="${h(t)}" ${t === conf.theme ? "checked" : ""}>
        <span class="swatch" style="background:${t.endsWith("dark") ? "#0a0a0a" : "#fbfbfa"}"></span>
        ${t === "fidetolabs-dark" ? "FidetoLabs dark" : "FidetoLabs white"}</label>`).join("")}
    </div>`;

  const nameIn = $("#new input[name=name]");
  if (nameIn && (empty || rest[0] === "empty")) nameIn.focus();
  const again = () => settings(view, []);

  view.querySelectorAll("[data-open]").forEach((b) => (b.onclick = async () => {
    try { await post(`/api/projects/${encodeURIComponent(b.dataset.open)}/select`, {}); await refreshHeader(); toast(`Working in ${b.dataset.open}`); again(); } catch (e) { fail(e); }
  }));
  view.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    const name = b.dataset.del;
    const out = $("#del");
    try {
      const w = (await api(`/api/projects/${encodeURIComponent(name)}/deletion`)).would_delete;
      out.innerHTML = `<div class="notice" style="margin-top:12px">
        <p><b>Delete ${h(name)}?</b> This cannot be undone. It takes ${num(w.files, 0)} files (${num(w.bytes / 1e6, 1)} MB)${w.filters ? `, ${num(w.filters, 0)} saved filters` : ""}${w.tables.length ? `, and these tables: ${w.tables.map((t) => `<code>${h(t.table)}</code> ${num(t.rows, 0)} rows`).join(", ")}` : ""}.</p>
        <form class="row" id="sure"><input name="typed" placeholder="type ${h(name)}" size="24"><button class="danger">Delete for good</button><button type="button" class="ghost" id="nope">Cancel</button></form></div>`;
      $("#nope").onclick = () => (out.innerHTML = "");
      $("#sure").onsubmit = async (ev) => {
        ev.preventDefault();
        if (ev.target.typed.value !== name) return toast(`Type ${name} to delete it`, true);
        try { await del(`/api/projects/${encodeURIComponent(name)}?confirm=${encodeURIComponent(name)}`); toast(`Deleted ${name}`); again(); } catch (e) { fail(e); }
      };
    } catch (e) { fail(e); }
  }));
  const nf = $("#new");
  if (nf) nf.onsubmit = async (ev) => {
    ev.preventDefault();
    const name = ev.target.name.value.trim();
    try {
      await post("/api/projects", { name });
      await post(`/api/projects/${encodeURIComponent(name)}/select`, {});
      await refreshHeader(); toast(`Created ${name}`); again();
    } catch (e) { fail(e); }
  };
  const uf = $("#ua");
  if (uf) uf.onsubmit = async (ev) => {
    ev.preventDefault();
    try { await put(`/api/projects/${encodeURIComponent(sel)}/settings`, { user_agent: ev.target.user_agent.value }); toast("Saved"); again(); } catch (e) { fail(e); }
  };
  view.querySelectorAll("input[name=theme]").forEach((r) => (r.onchange = async () => {
    try { applyTheme((await put("/api/settings/theme", { theme: r.value })).theme); again(); } catch (e) { fail(e); }
  }));

  // The status is live: a job started elsewhere shows up without a reload.
  clearTimeout(settings.timer);
  settings.timer = setTimeout(() => { if (location.hash.startsWith("#/settings") && !$("#sure")) again(); }, 5000);
}

async function refreshHeader() {
  HEALTH = await api("/api/health");
  $("#project").textContent = HEALTH.project || "no project";
  $("#scope").textContent = `scope ${HEALTH.scope}`;
  applyTheme(HEALTH.theme);
}

// ---- routing -------------------------------------------------------------------
const VIEWS = { "": home, filings, holdings, metrics, filters, tables, pipeline, settings };

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
        const can = f.pile === r.ingested_pile;
        const held = f.ingested_here;
        return `<tr><td>${can && !held ? `<input type="checkbox" data-i="${i}">` : ""}</td>
          <td>${h(f.company)} <span class="muted">${h(f.company_name || "")}</span></td><td>${h(f.form)}</td>
          <td class="mono">${h(f.filed)}</td><td class="mono">${h(f.period_end || "—")}</td>
          <td>${mark(can, can ? "yes" : "not today")} <span class="muted">${h(f.ingestable)}</span></td><td>${mark(held, held ? "held" : "—")}</td></tr>`;
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
      if (done.landed) {
        $("#res").insertAdjacentHTML("beforeend", `<div class="row" style="margin-top:8px"><span>Landed as raw documents. Facts, sections and metrics come from the transforms.</span>
          <button id="run" ${lock("run")}>Run the transforms</button></div><div id="ran" class="muted"></div>`);
        $("#run").onclick = () => runTransforms($("#run"), $("#ran"));
      }
    } catch (e) { fail(e); } finally { $("#go").disabled = false; }
  };
}

// ---- what I hold (T-03) ------------------------------------------------------------
async function holdings(view, [filer]) {
  const c = await api("/api/companies?limit=200");
  view.innerHTML = `
    <h1>What I hold</h1>
    <p class="lede">Every company with filings here, and how far each filing has got: raw → facts → sections → metrics.</p>
    <div class="row" style="margin-bottom:12px"><button id="run" class="ghost" ${lock("run")}>Run the transforms</button><span id="ran" class="muted"></span></div>
    <div class="split">
      <div class="card list">${c.companies.length ? c.companies.map((x) => `<a href="#/holdings/${encodeURIComponent(x.cik)}" class="${x.cik === filer ? "on" : ""}">${h(x.ticker || x.cik)} <span class="muted">${x.filings}</span></a>`).join("") : `<p class="empty">Nothing held yet. <a href="#/filings">Find some filings</a>.</p>`}</div>
      <div id="one">${filer ? "" : `<p class="muted">Pick a company.</p>`}</div>
    </div>`;
  $("#run").onclick = async () => { await runTransforms($("#run"), $("#ran")); if (filer) route(); };
  if (!filer) return;
  const p = await api(`/api/companies/${encodeURIComponent(filer)}/filings`);
  $("#one").innerHTML = `
    <div class="card">
      <h2 style="margin-top:0">${h(p.filer.name || p.filer.ticker || p.filer.cik)} <span class="muted mono">${h(p.filer.cik)}</span></h2>
      <table><tr><th>Filed</th><th>Form</th><th>Period</th><th>Raw</th><th>Facts</th><th>Sections</th><th>Metrics</th><th></th></tr>
      ${p.filings.map((f) => `<tr><td class="mono">${h(f.filed)}</td><td>${h(f.form)}</td><td class="mono">${h(f.period_end || "—")}</td>
        <td>${mark(f.raw, "raw")}</td><td>${mark(f.facts > 0, `${num(f.facts, 0)} facts`)}</td>
        <td>${f.sections === null ? `<span class="muted">n/a</span>` : mark(f.sections > 0, `${f.sections} sections`)}</td>
        <td>${mark(f.metrics.length > 0, `${f.metrics.length} of ${p.metrics.length}`)}
          <div class="muted mono">${h(f.metrics.join(", "))}</div></td>
        <td><button type="button" class="ghost rm" data-acc="${h(f.document || f.accession || "")}" ${lock("removal")}>Remove</button></td></tr>`).join("")}
      </table>
    </div>`;
  view.querySelectorAll("button.rm").forEach((b) => { b.onclick = () => removeFiling(b.dataset.acc, filer); });
}

// ---- taking a filing back out ---------------------------------------------------
//
// **Ask first, and show what goes.** The preview removes nothing; the confirmation
// names the four counts and the sentence about past answers, because a removal
// changes answers already given and a bare "are you sure?" does not say that.
async function removeFiling(accession, filer) {
  if (!accession) return;
  try {
    const p = await api(`/api/filings/${encodeURIComponent(accession)}/removal`);
    const lines = p.goes.map((g) => `  ${g.rows} ${g.what} (${g.table})`).join("\n");
    if (!confirm(`Remove filing ${accession}?\n\nThis removes ${p.rows} rows:\n${lines}\n\n${p.cost}`)) return;
    const d = await del(`/api/filings/${encodeURIComponent(accession)}`);
    toast(`Removed ${d.rows} rows for ${accession}`);
    route();
  } catch (e) { fail(e); }
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
        ${(t.facts.facts || []).length ? `<div class="muted">The filing's tagged facts, first 20 of ${num(t.facts.total ?? t.facts.facts.length, 0)}. The metric reads <code>${h(t.computed_from.map((u) => u.from).join(", "))}</code>; which rows its formula picked is in the formula.</div>
          <table><tr><th>Concept</th><th class="num">Value</th><th>Unit</th><th>Period</th></tr>
          ${t.facts.facts.slice(0, 20).map((f) => `<tr><td class="mono">${h(f.concept)}</td><td class="num">${num(f.value)}</td><td>${h(f.unit || "")}</td><td class="mono">${h(String(f.period_end || f.period || "").slice(0, 10))}</td></tr>`).join("")}</table>` : ""}`;
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
    await refreshHeader();
    // Nothing to open: Settings, with the reason, rather than an empty page.
    if (!HEALTH.project && !location.hash.startsWith("#/settings")) location.hash = "#/settings/empty";
  } catch (e) { fail(e); }
  window.addEventListener("hashchange", route);
  route();
})();
