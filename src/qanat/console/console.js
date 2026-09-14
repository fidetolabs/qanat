/* The console: the graph, what is selected, and the data itself.
 *
 * Replaces the stream-shaped app that came from the data-platform prototype. Three
 * things changed with it:
 *
 *   * the graph draws **jobs**, so clicking one shows the operator -- its script,
 *     its options, what it reads and writes -- instead of only a table's schema
 *   * the right rail is the **data table**, paged and sortable, not a five-row sample
 *   * nothing moves unless a job ran. There is no idle animation to mistake for work
 */
(function () {
  //  FastAPI sends {"detail": "..."}; a 422 sends a list of them. Showing the raw
  //  JSON to a person is the difference between an error and a message.
  function unwrap(text) {
    try {
      var b = JSON.parse(text);
      if (typeof b.detail === 'string') return b.detail;
      if (Array.isArray(b.detail)) {
        return b.detail.map(function (d) {
          return (d.loc || []).slice(1).join('.') + ': ' + d.msg;
        }).join('; ');
      }
    } catch (e) { /* not JSON */ }
    return text;
  }

  'use strict';

  var DAG = null, GRAPH = null, SEL = null, TABLE = null, TICK = null;

  function el(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
    });
  }
  function num(n) {
    if (n == null) return '—';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
    return String(n);
  }

  var DOWN = false;   // the server stopped answering

  //  No timeout meant a hung server never failed: the console kept saying
  //  "connected" and drawing the last numbers it saw, indefinitely.
  function fetchTimeout(path, opts, ms) {
    var c = new AbortController();
    var t = setTimeout(function () { c.abort(); }, ms || 8000);
    return fetch(path, Object.assign({}, opts || {}, { signal: c.signal }))
      .finally(function () { clearTimeout(t); });
  }

  //  `opts` used to be dropped on the floor, so every caller that thought it was
  //  posting was quietly issuing a GET and reading the answer to a question it
  //  had not asked.
  async function api(path, opts) {
    var r;
    try {
      r = await fetch(path, opts || undefined);
    } catch (e) {
      // "Failed to fetch" is the browser's phrase for "nothing answered", and on
      // its own it tells nobody anything. Say what it means once, and stop the
      // rest of the page repeating it.
      serverGone();
      throw new Error('qanat is not answering on this address. Is `qanat serve` ' +
                      'still running?');
    }
    if (!r.ok) throw new Error(unwrap(await r.text()) || r.statusText);
    if (DOWN) serverBack();
    return r.json();
  }

  function serverGone() {
    if (DOWN) return;
    DOWN = true;
    document.body.classList.add('server-down');
    el('led').className = 'led off';
    el('conn').textContent = 'not answering';
  }

  function serverBack() {
    DOWN = false;
    document.body.classList.remove('server-down');
    el('led').className = 'led';
    el('conn').textContent = 'connected';
  }

  // -------------------------------------------------------------- left rail
  function paintRails(g) {
    if (g.project) el('project').textContent = g.project;
    // Health had its own tiles once. The graph says all of it more directly -- a
    // failing job is a red box -- so the rail shows the book instead.
    var h = g.health || {};
    el('s-drift').textContent = h.drift || 0;

    // Tables are not listed separately any more: every table has exactly one job
    // that writes it, and every job is a box in the graph. Clicking the job shows
    // the table, so a second list would only be a second way to say the same thing.
  }

  // ------------------------------------------------------------- right rail
  // The panel slides over the graph rather than sitting beside it, so the graph
  // keeps the full width until you actually ask a question of one box.
  function openDetail() {
    document.getElementById('detail').classList.add('open');
    refitSoon();
  }

  function closeDetail() {
    document.getElementById('detail').classList.remove('open');
    SEL = null;
    TABLE = null;
    if (DAG) DAG.select(null);
    refitSoon();
  }

  // The panel takes width from the middle column, so the graph has to be refitted
  // into what is left -- during the slide and once it has finished.
  function refitSoon() {
    if (!DAG) return;
    // Slide, do not rescale: the panel changes how much room there is, not how big
    // the pipeline is. Run it across the slide so it tracks rather than snaps.
    var t0 = Date.now();
    (function step() {
      DAG.recentre();
      if (Date.now() - t0 < 360) requestAnimationFrame(step);
    })();
  }

  function isOpen() { return document.getElementById('detail').classList.contains('open'); }

  async function selectNode(table, maker) {
    if (!table) { closeDetail(); paintSelection(); return; }
    if (isOpen() && SEL && SEL.ref === table.ref) {
      closeDetail();
      paintSelection();
      return;
    }
    // if the table is not part of the alpha you had picked, the pick lets go.
    // leaving one alpha lit while you read a table it never touches is a lie
    if (DAG.focus && !DAG.byId[table.ref]) DAG.setFocus(null);
    else if (DAG.focus && DAG.byId[table.ref] && DAG.byId[table.ref].dim) {
      DAG.setFocus(null);
      if (window.clearAlphaPick) window.clearAlphaPick();
    }
    openDetail();
    SEL = { kind: 'table', ref: table.ref, table: table, maker: maker ? maker.id : null };
    DAG.select(table.ref);
    // the panel is not wiped while the request is in flight: clearing it and filling
    // it a moment later is the blink, and there is nothing to gain from it
    paintHeadOnly();
    if (table.written_by_replay) SEL.maker = null;
    if (SEL.maker) {
      try {
        SEL.detail = await api('/api/jobs/' + encodeURIComponent(SEL.maker));
      } catch (e) {
        SEL.error = e.message;
      }
    }
    await loadTable(table.ref, 0, null, true);
    paintSelection();
  }

  // Header first, body when the answer arrives: the panel says what you clicked
  // immediately and fills in underneath, instead of going blank and back.
  function paintHeadOnly() {
    if (!SEL) return;
    document.getElementById('sel-head').textContent = SEL.ref;
    document.getElementById('sel-sub').textContent = 'loading…';
  }

  async function selectTable(ref) {
    var g = GRAPH || {};
    var t = (g.tables || []).filter(function (x) { return x.ref === ref; })[0];
    var maker = t && t.producer
      ? (g.jobs || []).filter(function (j) { return j.id === t.producer; })[0] : null;
    return selectNode(t || { ref: ref }, maker);
  }

  async function loadTable(ref, offset, order, quiet) {
    var parts = ref.split('.');
    var q = '?limit=50&offset=' + (offset || 0);
    if (order) q += '&order=' + encodeURIComponent(order.col) + '&desc=' + (order.desc ? 'true' : 'false');
    try {
      TABLE = await api('/api/table/' + encodeURIComponent(parts[0]) + '/' +
                        encodeURIComponent(parts[1]) + q);
      TABLE.sortKey = order || null;
    } catch (e) {
      TABLE = { ref: ref, error: e.message };
    }
    if (!quiet) paintSelection();
  }

  function connectionBlock(conn) {
    if (!conn) return '';
    var rows = [];
    var add = function (k, v) {
      if (v == null || v === '' || (Array.isArray(v) && !v.length)) return;
      rows.push('<tr><td class="mono">' + esc(k) + '</td><td class="n mono">' +
        esc(typeof v === 'object' ? JSON.stringify(v) : v) + '</td></tr>');
    };
    add('connector', conn.connector);
    add('path', conn.path);
    add('on disk', conn.path ? (conn.exists ? 'yes' : 'not found') : null);
    add('url', conn.url);
    add('params', Object.keys(conn.params || {}).length ? conn.params : null);
    add('headers', (conn.headers || []).length ? conn.headers.join(', ') : null);
    add('records at', conn.records);
    add('server', conn.dsn);
    add('query', conn.query);
    add('mode', conn.mode);
    return '<div class="kicker">Where these rows come from: ' + esc(conn.kind) +
      '<button type="button" class="info edit" id="conn-edit" title="change this ' +
      'connection">edit</button></div>' +
      (rows.length ? '<table class="t"><tbody>' + rows.join('') + '</tbody></table>' : '') +
      '<div class="bt-note-inline">This table is the start of the pipeline, so there is no step ' +
      'behind it. Values written as <span class="mono">${VAR}</span> stay unexpanded and ' +
      'passwords are masked. The console reads pipelines, not secrets.</div>';
  }

  function operatorBlock(d) {
    if (!d) return '';
    if (d.kind === 'source') {
      return '<div class="tiles">' +
        tile('what it is', 'brings data in') +
        tile('runs', startedBy(d)) +
        tile('last run', d.last_run ? d.last_run.status : 'never run') +
        tile('rows it wrote', d.last_run ? num(d.last_run.rows_out) : '—') +
        '</div>' + connectionBlock(d.connection);
    }
    var meta = '<div class="tiles">' +
      tile('what it is', d.kind === 'source' ? 'brings data in' : 'a ' +
        (/\.sql$/i.test(d.script || '') ? 'SQL' : 'Python') + ' step') +
      tile('runs', startedBy(d)) +
      tile('last run', d.last_run ? d.last_run.status : 'never run') +
      tile('rows it wrote', d.last_run ? num(d.last_run.rows_out) : '—') +
      '</div>';
    var io = '<div class="iorow"><span class="k">reads</span>' +
      ((d.reads || []).length
        ? d.reads.map(function (t) { return '<button class="chip" data-table="' + esc(t) + '">' +
            esc(t) + '</button>'; }).join('')
        : '<span class="faint">nothing. This one brings data in from outside</span>') + '</div>' +
      '<div class="iorow"><span class="k">writes</span>' +
      (d.writes || []).map(function (t) {
        return '<button class="chip" data-table="' + esc(t) + '">' + esc(t) + '</button>';
      }).join('') + '</div>' +
      (d.universe ? '<div class="iorow"><span class="k">universe</span><span class="chip flat">' +
        esc(d.universe) + '</span></div>' : '');
    var opts = Object.keys(d.options || {}).length
      ? '<div class="kicker">Settings this step was given</div><table class="t"><tbody>' +
        Object.keys(d.options).map(function (k) {
          return '<tr><td class="mono">' + esc(k) + '</td><td class="n mono">' +
            esc(JSON.stringify(d.options[k])) + '</td></tr>';
        }).join('') + '</tbody></table>' : '';
    var src = d.source != null
      ? '<div class="kicker">What it computes: ' + esc(d.script) +
        '</div><pre class="src">' + esc(d.source) + '</pre>'
      : (d.error ? '<div class="kicker">Script</div><div class="warnbox">' + esc(d.error) +
         '</div>' : '');
    return meta + io + opts + src;
  }

  // A job runs on a clock, or when a table it reads gets new rows, or when you
  // ask. Both can be set, and then it is both.
  function startedBy(d) {
    var bits = [];
    if (d.schedule) bits.push('on ' + d.schedule);
    if ((d.when || []).length) bits.push('when ' + d.when.join(', ') + ' changes');
    return bits.join(' · ') || 'only when you ask';
  }

  function tile(k, v) {
    return '<div class="tile"><div class="k">' + esc(k) + '</div><div class="v">' +
      esc(v) + '</div></div>';
  }

  function dataTable() {
    if (!TABLE) return '';
    if (TABLE.error) {
      return '<div class="kicker">The table itself</div><div class="warnbox">' +
        esc(TABLE.error) + '</div>';
    }
    var t = TABLE;
    var from = t.offset + 1, to = Math.min(t.rows, t.offset + (t.sample || []).length);
    var head = '<div class="panel-h"><h3>' + esc(t.ref) + '</h3><span class="r">' +
      (t.rows ? from + '–' + to + ' of ' + num(t.rows) : '0 rows') + '</span></div>';
    var cols = (t.columns || []).map(function (c) { return c.name; });
    var thead = '<tr>' + (t.columns || []).map(function (c) {
      var on = t.order === c.name;
      // a real control, with the sort state on the header where a screen reader
      // looks for it -- this used to be a click handler on a <th>, mouse only
      var aria = on ? (t.desc ? 'descending' : 'ascending') : 'none';
      return '<th class="sortable' + (on ? ' on' : '') + '" aria-sort="' + aria + '">' +
        '<button type="button" data-col="' + esc(c.name) + '">' +
        esc(c.name) + (on ? (t.desc ? ' ▾' : ' ▴') : '') +
        '<i>' + esc(c.type) + '</i></button></th>';
    }).join('') + '</tr>';
    var body = (t.sample || []).map(function (r) {
      return '<tr>' + cols.map(function (c) {
        var v = r[c];
        return '<td class="' + (typeof v === 'number' ? 'n mono' : 'mono') + '">' +
          esc(v == null ? '—' : v) + '</td>';
      }).join('') + '</tr>';
    }).join('');
    var pager = '<div class="pager">' +
      '<button class="btn sm" data-page="first"' + (t.offset ? '' : ' disabled') + '>⏮</button>' +
      '<button class="btn sm" data-page="prev"' + (t.offset ? '' : ' disabled') + '>prev</button>' +
      '<button class="btn sm" data-page="next"' + (to < t.rows ? '' : ' disabled') + '>next</button>' +
      '<span class="faint">' + (t.order ? 'sorted by ' + esc(t.order) + ', click a column to change'
        : 'click a column to sort') + '</span>' +
      '</div>';
    return head + '<div class="tablewrap grow"><table class="t data"><thead>' + thead +
      '</thead><tbody>' + body + '</tbody></table></div>' + pager;
  }

  function paintSelection() {
    var head = el('sel-head'), sub = el('sel-sub'), body = el('sel-body');
    // a stage paints its own panel and keeps it while you type in it
    if (SEL && SEL.kind === 'stage') return;
    if (!SEL) {
      head.textContent = '—';
      sub.textContent = 'click a table in the graph';
      body.innerHTML = '<div class="bt-empty">Every box is a <b>table</b> and every arrow is the ' +
        '<b>step</b> that makes it. Click a table to see what wrote it: the code and its ' +
        'settings, or, for a source table, where its rows come from. Then the table ' +
        'itself.</div>';
      return;
    }
    var d = SEL.detail;
    var t = SEL.table || {};
    head.textContent = SEL.ref;
    sub.innerHTML = d
      ? (d.kind === 'source'
          ? 'brought in by <b>' + esc(d.id) + '</b>'
          : 'written by <b>' + esc(d.id) + '</b>' + (d.script ? ' · ' + esc(d.script) : ''))
      : (t.written_by_replay
          ? 'written by a <b>backtest</b> of ' + esc(alphaNames(t)) +
            ': what it earned per rebalance'
          : 'nothing in this project writes it');
    body.innerHTML = (SEL.error ? '<div class="warnbox">' + esc(SEL.error) + '</div>' : '') +
      (t.written_by_replay ? replayBlock(t) : operatorBlock(d)) +
      dataTable();

    Array.prototype.forEach.call(body.querySelectorAll('[data-table]'), function (b) {
      b.onclick = function () { selectTable(b.getAttribute('data-table')); };
    });
    if (el('conn-edit')) {
      el('conn-edit').onclick = function () { window.AlphaEdit.openSource(d); };
    }
    Array.prototype.forEach.call(body.querySelectorAll('[data-page]'), function (b) {
      b.onclick = function () {
        var t = TABLE, step = t.limit;
        var off = b.getAttribute('data-page') === 'first' ? 0
          : b.getAttribute('data-page') === 'prev' ? Math.max(0, t.offset - step)
          : t.offset + step;
        loadTable(t.ref, off, t.sortKey);
      };
    });
    Array.prototype.forEach.call(body.querySelectorAll('th.sortable button'), function (th) {
      th.onclick = function () {
        var col = th.getAttribute('data-col');
        var desc = !(TABLE.order === col && TABLE.desc);
        loadTable(TABLE.ref, 0, { col: col, desc: desc });
      };
    });
  }

  function alphaNames(t) {
    var by = t.producers || (t.producer ? [t.producer] : []);
    return by.map(function (a) { return a.replace(/^alpha_/, ''); }).join(' + ') || 'an alpha';
  }

  // What a PnL table is: the result of pricing one or more portfolios. A blend has
  // several weights tables behind it, and naming them here is the only place the
  // panel can say which alphas were actually in the book.
  function replayBlock(t) {
    var by = t.producers || (t.producer ? [t.producer] : []);
    var chips = by.map(function (a) {
      var ref = 'weights.' + a.replace(/^alpha_/, '');
      return '<button type="button" class="chip" data-table="' + esc(ref) + '">' +
        esc(ref) + '</button>';
    }).join(' ');
    return '<div class="tiles">' +
      tile('what it is', by.length > 1 ? 'a blend of ' + by.length + ' alphas'
                                       : 'one alpha, priced') +
      tile('written by', 'a backtest, not a step') +
      tile('rows', num(t.rows || 0)) +
      tile('one row is', 'one rebalance') +
      '</div>' +
      '<div class="iorow"><span class="k">holds</span>' + chips + '</div>' +
      '<div class="bt-note-inline">Every row is one rebalance: what the book held from ' +
      'that date to the next, and what it earned after fees and slippage. Run another ' +
      'backtest and this table is written again.</div>';
  }

  // -------------------------------------------------------------------- log
  async function paintLog() {
    try {
      var events = await api('/api/events?limit=60');
    } catch (e) { return; }
    el('log').innerHTML = events.map(function (v) {
      //  Who caused it. This is why the log is on both pages: an agent over MCP
      //  can change the project while you are reading a result, and the row that
      //  says `agent` is the only place that shows. Rows written before the
      //  column existed carry no actor, and are left unattributed rather than
      //  credited to whoever happens to be looking.
      var by = v.actor
        ? '<span class="by ' + esc(v.actor === 'agent' ? 'mcp' : v.actor) + '">' +
          esc(v.actor) + '</span>'
        : '';
      return '<div class="lrow ' + esc(v.level) + '"><span class="t">' +
        esc(String(v.ts).slice(11, 19)) + '</span><span class="j">' + esc(v.job_id) +
        '</span><span class="m">' + esc(v.message) + '</span>' + by + '</div>';
    }).join('') || '<div class="bt-empty">Nothing has run yet. Run a job, or start a backtest.</div>';
  }

  // -------------------------------------------------------------------- ask
  //
  //  A question, answered by the agent CLI already on this machine. The console
  //  holds no key, so there is nothing to set up: if `claude` or `cursor-agent`
  //  is installed and signed in, the box works, and if it is not, the box says so
  //  rather than pretending.

  var ASK_TICK = null;
  //  Set when the person folds the panel away. Sticky for the rest of that
  //  question: a new line arriving is not a reason to reopen something they
  //  just closed. Cleared when they ask again.
  var ASK_FOLDED = false;
  //  The last state we saw. Polling stops the moment a question finishes, so
  //  anything that needs to redraw the bar afterwards -- folding, unfolding --
  //  has to work from this rather than from the next tick, which never comes.
  var ASK_LAST = null;

  var ASK_HINTS = {
    strategies: 'build me a momentum strategy over 3 months and back it up with a test',
    pipeline: 'why does features.momentum only have 8 rows?',
  };

  function askPlaceholder() {
    var page = document.body.classList.contains('page-pipeline') ? 'pipeline' : 'strategies';
    var box = el('ask-q');
    if (box && !box.disabled) box.placeholder = ASK_HINTS[page];
  }

  async function askSetup() {
    var box = el('ask-q'), go = el('ask-go');
    try {
      var s = await api('/api/ask');
    } catch (e) { return; }
    if (!s.available) {
      box.disabled = true; go.disabled = true;
      box.placeholder = 'install Claude Code or Cursor and sign in -- qanat never holds a key';
      el('ask-lbl').textContent = 'Ask';
      return;
    }
    el('ask-lbl').textContent = 'Ask ' + s.cli;
    askPlaceholder();
    if (s.ask && !s.ask.done) { askButton(true); el('ask-q').disabled = true;
                                paintAsk(s.ask); pollAsk(); }
  }

  function paintAsk(a) {
    ASK_LAST = a;
    var work = el('ask-work');
    work.hidden = ASK_FOLDED;
    peek(a);
    work.classList.toggle('done', !!a.done);
    //  The question, not the machinery. Once the box is cleared this is the only
    //  place it still says what was asked.
    var q = a.question || '';
    el('ask-what').textContent = q.length > 96 ? q.slice(0, 95) + '…' : q;
    el('ask-what').title = q;
    el('ask-el').textContent = a.elapsed + 's';
    el('ask-lines').innerHTML = (a.lines || []).map(function (l) {
      return '<li class="' + (l.kind === 'diff' ? 'diffrow' : '') + '"><span class="at">' +
        esc(l.at) + 's</span><span class="k ' + esc(l.kind) + '">' + esc(l.kind) +
        '</span><span class="x">' + esc(l.text) + '</span></li>';
    }).join('');
    var lines = el('ask-lines');
    lines.scrollTop = lines.scrollHeight;
    var ans = el('ask-answer');
    if (a.error || a.answer) {
      ans.hidden = false;
      ans.className = 'askanswer' + (a.error ? ' bad' : '');
      ans.textContent = a.error || a.answer;
    } else {
      ans.hidden = true;
    }
    if (a.done) {
      var box = el('ask-q');
      if (box.value.trim() === (a.question || '').trim()) box.value = '';
      box.disabled = false;
      askButton(false);
      //  The agent may have written a step or run a job, so the graph, the book
      //  and the log are all potentially stale the moment it finishes.
      poll();
      if (window.repaintBook) window.repaintBook();
    }
  }

  //  The newest line, the clock, and whether it is still going -- enough to know
  //  what is happening without giving the panel the room to say it.
  function peek(a) {
    var bar = el('ask-peek');
    if (!a) { bar.hidden = true; return; }
    //  Always present once there is something to report, open or folded. It used
    //  to appear only when folded, which changed the height of the bar and made
    //  the whole page jump every time the panel was toggled.
    var last = (a.lines || [])[a.lines.length - 1] || { kind: 'start', text: 'working' };
    bar.hidden = false;
    bar.classList.toggle('lifted', !ASK_FOLDED);
    el('peek-more').textContent = ASK_FOLDED ? 'show' : 'hide';
    el('peek-kind').className = 'pk ' + esc(last.kind);
    el('peek-kind').textContent = last.kind;
    el('peek-text').textContent = a.error ? a.error : (last.text || '');
    el('peek-el').textContent = a.elapsed + 's';
    el('peek-spin').style.visibility = a.done ? 'hidden' : 'visible';
  }

  function pollAsk() {
    clearInterval(ASK_TICK);
    ASK_TICK = setInterval(async function () {
      try {
        var s = await api('/api/ask');
      } catch (e) { return; }
      if (!s.ask) return;
      paintAsk(s.ask);
      if (s.ask.done) clearInterval(ASK_TICK);
    }, 700);
  }

  var ASK_RUNNING = false;

  function askButton(running) {
    ASK_RUNNING = running;
    var go = el('ask-go');
    go.textContent = running ? 'stop' : 'ask';
    go.classList.toggle('stop', running);
    go.disabled = false;
    go.title = running ? 'stop the agent · anything it already did stays done' : '';
  }

  function wireAsk() {
    el('ask-form').addEventListener('submit', async function (e) {
      e.preventDefault();
      //  The same button, because it is the same thing at two moments: it asks,
      //  and while it is asking it is the way to change your mind.
      if (ASK_RUNNING) {
        el('ask-go').disabled = true;
        try { await api('/api/ask', { method: 'DELETE' }); } catch (err) { /* it ended */ }
        return;
      }
      var box = el('ask-q'), q = box.value.trim();
      if (!q || box.disabled) return;
      //  The question stays in the box while it is being answered -- taking it
      //  away the instant you press ask reads like it was thrown out. It is
      //  cleared when the answer lands, which is also what stops the next
      //  question being typed onto the end of this one.
      box.disabled = true; askButton(true);
      el('ask-lines').innerHTML = '';
      el('ask-answer').hidden = true;
      ASK_FOLDED = false;              // a new question opens the panel again
      ASK_LAST = null;
      el('ask-work').hidden = false;
      el('ask-work').classList.remove('done');
      el('ask-what').textContent = 'starting';
      try {
        await api('/api/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q, base: location.origin }),
        });
        pollAsk();
      } catch (err) {
        el('ask-answer').hidden = false;
        el('ask-answer').className = 'askanswer bad';
        el('ask-answer').textContent = String(err.message || err);
        box.disabled = false; askButton(false);
      }
    });
    var fold = function (on) {
      ASK_FOLDED = on;
      el('ask-work').hidden = on;
      peek(ASK_LAST);          // not from the next tick: there may not be one
    };
    el('ask-fold').onclick = function () { fold(true); };
    el('ask-peek').onclick = function () { fold(!ASK_FOLDED ? true : false); };

    //  It floats over the page, so it gets out of the way like anything that
    //  floats: click past it, or press Escape. Folding mid-run is fine now --
    //  the bar keeps reporting, so nothing is lost by putting the panel away.
    document.addEventListener('mousedown', function (e) {
      if (el('ask-work').hidden) return;
      if (!el('askbar').contains(e.target)) fold(true);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !el('ask-work').hidden) fold(true);
    });
    askSetup();
  }

  //  The log folds. Remembered, because someone who does not want it does not
  //  want it again on the next reload either.
  function wireLog() {
    var wrap = el('logwrap'), btn = el('log-fold');
    if (!wrap || !btn) return;
    var open = localStorage.getItem('qanat.log') !== '0';
    var set = function (on) {
      wrap.setAttribute('data-open', on ? '1' : '0');
      btn.setAttribute('aria-expanded', String(on));
      localStorage.setItem('qanat.log', on ? '1' : '0');
      refitSoon();
    };
    set(open);
    btn.onclick = function () { set(wrap.getAttribute('data-open') !== '1'); };
  }

  // ------------------------------------------------------------------ pages
  //
  //  Two pages, one question each. `strategies` is the book and the report;
  //  `pipeline` is the graph, the tables and the steps. The choice is remembered,
  //  because someone testing ideas all afternoon should not land on the graph
  //  every time they reload.
  //
  //  The canvas is `display:none` on the strategies page, so it has no size to
  //  fit into. Coming back to the pipeline has to fit again once it is visible.

  var PAGES = ['strategies', 'pipeline'];

  function startingPage() {
    // `?view=backtests` is what `open_console` sends when an agent wants the
    // person looking at results, so it decides the page rather than fighting it.
    var q = new URLSearchParams(location.search).get('view');
    if (q === 'backtests') return 'strategies';
    if (q === 'pipeline') return 'pipeline';
    var saved = localStorage.getItem('qanat.page');
    return PAGES.indexOf(saved) >= 0 ? saved : 'strategies';
  }

  function setPage(name) {
    if (PAGES.indexOf(name) < 0) name = 'strategies';
    PAGES.forEach(function (p) {
      document.body.classList.toggle('page-' + p, p === name);
      var tab = el('pt-' + p);
      if (tab) tab.setAttribute('aria-selected', String(p === name));
    });
    localStorage.setItem('qanat.page', name);
    askPlaceholder();

    if (name === 'strategies') {
      // the report is the page, so it is never left collapsed here
      if (window.openBacktests) window.openBacktests();
    } else {
      // the canvas had no size while it was hidden; give it one now
      closeDetail();
      [0, 60, 260].forEach(function (ms) {
        setTimeout(function () { if (DAG) DAG.fit(true); }, ms);
      });
    }
  }

  function wirePages() {
    PAGES.forEach(function (p) {
      var tab = el('pt-' + p);
      if (tab) tab.onclick = function () { setPage(p); };
    });
  }

  // Fit once the canvas has stopped resizing. A ResizeObserver fires on every
  // step of the panel animation, so the fit is deferred until the size has held
  // still for a beat, and then it happens once.
  var settleTimer = null, sizeWatch = null;

  function fitWhenSettled() {
    var cv = el('dagcv');
    var again = function () {
      clearTimeout(settleTimer);
      settleTimer = setTimeout(function () {
        DAG.resize();
        DAG.fitFocus();
        if (sizeWatch) { sizeWatch.disconnect(); sizeWatch = null; }
      }, 120);
    };
    if (sizeWatch) sizeWatch.disconnect();
    if (typeof ResizeObserver === 'function') {
      sizeWatch = new ResizeObserver(again);
      sizeWatch.observe(cv);
    }
    again();
  }

  // ------------------------------------------------------------------- poll
  //  One focusable control per table, kept beside the canvas. The canvas carries
  //  the picture; this carries the same action for anybody not using a mouse.
  var KEY_SIG = '';
  function paintKeyList() {
    var host = el('dag-keys');
    if (!host || !GRAPH || !GRAPH.tables) return;
    var sig = GRAPH.tables.map(function (x) { return x.ref + ':' + x.rows; }).join('|');
    if (sig === KEY_SIG) return;
    KEY_SIG = sig;
    host.innerHTML = GRAPH.tables.map(function (x) {
      return '<li><button type="button" data-ref="' + esc(x.ref) + '">' +
             esc(x.ref) + ' · ' + (x.rows || 0).toLocaleString() + ' rows · ' +
             esc(x.status || 'idle') + '</button></li>';
    }).join('');
    Array.prototype.forEach.call(host.querySelectorAll('button'), function (b) {
      // selectTable takes a ref and looks the node up; selectNode wants the node
      // itself, so passing it a string quietly opened nothing at all
      b.onclick = function () { selectTable(b.getAttribute('data-ref')); };
    });
  }

  async function poll() {
    var led = el('led'), conn = el('conn');
    try {
      GRAPH = await api('/api/graph');
      paintKeyList();
    } catch (e) {
      led.className = 'led off';
      conn.textContent = 'disconnected';
      return;
    }
    led.className = 'led';
    conn.textContent = 'connected';
    DAG.setGraph(GRAPH);
    paintRails(GRAPH);
    if (SEL && SEL.kind === 'job') DAG.select(SEL.id);
    paintLog();
  }

  function start() {
    DAG = window.QanatDag.mount(el('dagcv'));
    DAG.onSelect = selectNode;
    paintKeyList();
    window.QANAT = {
      dag: DAG, poll: poll, selectTable: selectTable, closeDetail: closeDetail,
      // the book calls this when an alpha is picked: the graph becomes that alpha
      // `set` names the alphaset, so exactly its own result table lights up
      // Picking an alphaset frames its lineage. The results panel and the side
      // panel both change the size of the canvas, and they animate, so fitting
      // straight away frames the wrong rectangle. Guessing at the timing with
      // three staggered fits is what made this jump about; watch the canvas
      // instead and fit when it has actually stopped changing.
      focusAlpha: function (ids, set) {
        DAG.setFocus(ids, set);
        if (ids && ids.length) fitWhenSettled();
        else refitSoon();
      },
    };
    el('b-fit').onclick = function () { DAG.fit(true); };
    el('b-add-src').onclick = function () { window.AlphaEdit.openSource(null); };
    // clicking the same stage again lets go, the way clicking a table twice does
    DAG.onStage = function (band) {
      if (isOpen() && SEL && SEL.kind === 'stage' && SEL.ref === band.id) {
        closeDetail();
        return;
      }
      openDetail();
      SEL = { kind: 'stage', ref: band.id };
      DAG.select(null);
      window.AlphaEdit.openStage(band.id, GRAPH);
    };
    el('sel-close').onclick = function () { closeDetail(); paintSelection(); };

    wireLog();
    wirePages();
    wireAsk();
    setPage(startingPage());
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      //  This used to close the detail rail unconditionally -- so pressing Escape
      //  over the run form closed the panel *behind* it and left the form up,
      //  while #sel-close advertises title="close (esc)".
      var runner = el('runner');
      if (runner && !runner.hidden) {
        if (window.closeRunner) window.closeRunner();
        return;
      }
      var editor = document.querySelector('.editor:not([hidden])');
      if (editor && editor.id !== 'runner') { closeDetail(); paintSelection(); return; }
      closeDetail(); paintSelection();
    });
    el('btn-bt').onclick = function () { window.toggleBacktests(); };
    el('btn-run').onclick = function () { window.openRunner(); };
    paintSelection();
    poll();
    // The first layout settles a frame or two after mount, and the rails are sized
    // last. Fit on a few early frames rather than guessing one delay.
    [0, 120, 400, 900].forEach(function (ms) { setTimeout(function () { DAG.fit(true); }, ms); });
    TICK = setInterval(poll, 2500);
  }

  document.addEventListener('DOMContentLoaded', start);
  window.addEventListener('beforeunload', function () { clearInterval(TICK); });
})();
