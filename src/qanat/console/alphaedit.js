/* Adding and changing an alpha, without leaving the page.
 *
 * An alpha is a step from a feature table to a weights table, so that is all this
 * asks for: a name, the table it reads, the rule it applies, and the universe it
 * may hold. The script on disk, the weights table and the PnL table under it all
 * follow from those four, and are made for you.
 *
 * The rule starts from the shelf because a blank editor is not a good first
 * question. Pick one, change its settings, and the script is yours to edit after.
 */
(function () {
  'use strict';

  var SHELF = null, EDITING = true;

  function el(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  async function api(path, opts) {
    var r = await fetch(path, opts);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  function isEditing() { return EDITING; }

  function setEditing(on) {
    EDITING = !!on;
    document.body.classList.toggle('book-editing', EDITING);
    if (window.repaintBook) window.repaintBook();
  }

  // ------------------------------------------------------------------ editor
  async function open(alpha) {
    var detail = el('detail');
    detail.classList.add('open');
    if (window.QANAT) window.QANAT.dag.recentre();
    el('sel-head').textContent = alpha ? alpha.name : 'new alpha';
    el('sel-sub').textContent = alpha
      ? 'a step from a feature table to ' + alpha.writes
      : 'a step from a feature table to a weights table';
    var body = el('sel-body');
    body.innerHTML = '<div class="bt-empty">reading what this project offers…</div>';
    try {
      SHELF = SHELF || await api('/api/shelf');
    } catch (e) {
      body.innerHTML = '<div class="warnbox">' + esc(e.message) + '</div>';
      return;
    }
    body.innerHTML = form(alpha);
    wire(alpha);
  }

  function form(alpha) {
    var cur = alpha ? (alpha.conditions || {}) : {};
    var reads = (alpha && alpha.reads) || cur.reads || SHELF.reads[SHELF.reads.length - 1] || '';
    var uni = (alpha && alpha.universe) || (SHELF.universes[0] || {}).id || '';
    var opt = function (list, sel) {
      return list.map(function (x) {
        var v = typeof x === 'string' ? x : x.id;
        return '<option value="' + esc(v) + '"' + (v === sel ? ' selected' : '') + '>' +
          esc(v) + '</option>';
      }).join('');
    };
    return '<div class="rform ae">' +
      row('name', alpha
        ? '<input id="ae-name" value="' + esc(alpha.name) + '" disabled>'
        : '<input id="ae-name" placeholder="fast_reversal">',
        alpha ? 'a name is the weights table and the PnL table under it, so it does not change'
              : 'lower case, no spaces. It becomes weights.&lt;name&gt; and pnl.&lt;name&gt;') +

      row('rule', '<select id="ae-shelf">' +
        (alpha ? '<option value="">keep the script it has</option>' : '') +
        SHELF.shelf.map(function (a) {
          return '<option value="' + esc(a.name) + '">' + esc(a.title) + '</option>';
        }).join('') + '</select>',
        'starts from one of the ready rules. You can edit the script afterwards') +

      row('reads', '<select id="ae-reads">' + opt(SHELF.reads, reads) +
        '<option value="__new">＋ a new feature step…</option></select>',
        'the table it turns into a portfolio. Needs a symbol, a date and a price') +

      '<div id="ae-newfeat" hidden>' +
      row('feature name', '<input id="nf-name" placeholder="zscore_20">',
          'becomes features.&lt;name&gt;') +
      row('built from', '<select id="nf-reads">' + opt(SHELF.reads, reads) + '</select>',
          'the table this new step reads') +
      row('SQL', '<textarea id="nf-sql" rows="6"></textarea>',
          'one SELECT. Tables are addressed as <span class="mono">stage__table</span>') +
      '</div>' +

      row('universe', '<select id="ae-uni">' + opt(SHELF.universes, uni) + '</select>',
        'the symbols it is allowed to hold') +

      // How the alpha is run, as opposed to what it computes. A five-day reversal
      // and a sixty-day momentum are not asking for the same rebalance gap, and
      // the gap is part of the rule.
      row('rebalance', '<input id="ae-reb" value="' + esc((alpha && alpha.rebalance) || '') +
        '" placeholder="as the project says">',
        'how often it decides, e.g. <span class="mono">5d</span> · a backtest uses this ' +
        'unless you say otherwise') +
      row('decay', '<input id="ae-decay" type="number" min="0" value="' +
        ((alpha && alpha.decay) || '') + '" placeholder="0">',
        'hold a blend of the last N portfolios · cuts turnover, blunts the signal') +

      '<div id="ae-opts"></div>' +
      '</div>' +
      '<div class="rgo"><button type="button" class="btn go" id="ae-save">' +
      (alpha ? 'save' : 'add it') + '</button>' +
      (alpha ? '<button type="button" class="btn" id="ae-del">delete</button>' : '') +
      '<span id="ae-say" class="faint"></span></div>';
  }

  function row(label, control, hint) {
    return '<div class="rrow"><label>' + esc(label) + '<i>' + hint + '</i></label>' +
      control + '</div>';
  }

  // the settings a rule takes are its own, so they are drawn from the rule you pick
  function paintOptions(alpha) {
    var pick = el('ae-shelf').value;
    var entry = SHELF.shelf.filter(function (a) { return a.name === pick; })[0];
    var have = (alpha && alpha.conditions) || {};
    var host = el('ae-opts');
    if (!entry) { host.innerHTML = ''; return; }
    host.innerHTML = Object.keys(entry.options).map(function (k) {
      var v = have[k] != null ? have[k] : entry.options[k];
      return row(k, '<input class="ae-opt" data-k="' + esc(k) + '" value="' + esc(v) + '">',
        k === 'lookback' ? 'how far back it looks, in rows'
          : k === 'top_n' ? 'how many names it holds'
          : k === 'window' ? 'how many rows the measure uses' : '');
    }).join('') +
      '<div class="bt-note-inline">' + esc(entry.why) + '</div>';
  }

  function wire(alpha) {
    var shelf = el('ae-shelf'), reads = el('ae-reads');
    shelf.onchange = function () { paintOptions(alpha); };
    paintOptions(alpha);

    reads.onchange = function () {
      var isNew = reads.value === '__new';
      el('ae-newfeat').hidden = !isNew;
      if (isNew && !el('nf-sql').value) {
        el('nf-sql').value = 'SELECT\n    date,\n    symbol,\n    close\nFROM ' +
          (SHELF.reads[SHELF.reads.length - 1] || 'normalized__prices').replace('.', '__');
      }
    };
    el('ae-save').onclick = function () { save(alpha); };
    var del = el('ae-del');
    if (del) del.onclick = function () { remove(alpha); };
  }

  async function save(alpha) {
    var say = el('ae-say'), go = el('ae-save');
    var name = (alpha ? alpha.name : el('ae-name').value || '').trim();
    if (!name) { say.textContent = 'it needs a name'; return; }
    var reads = el('ae-reads').value;
    go.disabled = true;
    say.textContent = 'saving…';

    try {
      // a new feature step is made first, because the alpha has to read something
      // that exists
      if (reads === '__new') {
        var fname = (el('nf-name').value || '').trim();
        if (!fname) throw new Error('the new feature step needs a name');
        var stage = (SHELF.stages.filter(function (s) { return s.kind === 'features'; })[0] || {}).id;
        if (!stage) throw new Error('this project has no features stage');
        var rel = 'steps/' + fname + '.sql';
        await api('/api/steps', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            id: fname, from: [el('nf-reads').value], to: [stage + '.' + fname],
            script: rel, source: el('nf-sql').value,
          }),
        });
        reads = stage + '.' + fname;
      }

      var options = {};
      Array.prototype.forEach.call(document.querySelectorAll('.ae-opt'), function (i) {
        var v = i.value.trim();
        options[i.getAttribute('data-k')] = /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v;
      });
      await api('/api/alphas', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          id: (alpha && alpha.id) || null,
          name: name, reads: reads, universe: el('ae-uni').value || null,
          shelf: el('ae-shelf').value || null, options: options,
          rebalance: el('ae-reb').value.trim() || null,
          decay: parseInt(el('ae-decay').value, 10) || null,
        }),
      });
    } catch (e) {
      go.disabled = false;
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    go.disabled = false;
    say.innerHTML = '<span class="up">saved</span>. Run it to fill its tables';
    if (window.QANAT) window.QANAT.poll();
    if (window.repaintBook) window.repaintBook();
  }

  async function remove(alpha) {
    var say = el('ae-say');
    say.textContent = 'removing…';
    try {
      await api('/api/alphas/' + encodeURIComponent(alpha.id), { method: 'DELETE' });
    } catch (e) {
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    el('detail').classList.remove('open');
    if (window.QANAT) window.QANAT.poll();
    if (window.repaintBook) window.repaintBook();
  }

  // ------------------------------------------------------------- data sources
  // A source is the one node with no step behind it, so what it needs is not a
  // rule but a connection: where the rows come from, and whether anything is on a
  // clock to fetch them again.
  var CONNECTORS = {
    csv: { label: 'a file on this machine', fields: [
      ['path', 'path to the .csv, relative to the project'] ] },
    rest: { label: 'an HTTP endpoint', fields: [
      ['url', 'the endpoint. ${VARS} are read from the environment, never stored here'],
      ['records', 'dot path to the list in the response. Leave blank if it is a list'] ] },
    sql: { label: 'a database', fields: [
      ['dsn', 'postgresql://… put the password in ${PGPASSWORD}, not here'],
      ['query', 'the SELECT to pull, or a table name'] ] },
    synthetic: { label: 'generated here. No network, no keys', fields: [
      ['series', 'prices or news'] ] },
  };

  async function openSource(job) {
    var detail = el('detail');
    detail.classList.add('open');
    el('sel-head').textContent = job ? job.id : 'new data source';
    el('sel-sub').textContent = 'where rows come into the pipeline';
    var body = el('sel-body');
    try {
      SHELF = SHELF || await api('/api/shelf');
    } catch (e) {
      body.innerHTML = '<div class="warnbox">' + esc(e.message) + '</div>';
      return;
    }
    var raw = (SHELF.stages.filter(function (x) { return x.kind === 'raw'; })[0] || {}).id || 'raw';
    var o = (job && job.options) || {};
    var kind = (job && job.connector) || 'csv';
    body.innerHTML = '<div class="rform ae">' +
      row('name', job
        ? '<input id="sc-name" value="' + esc(job.id) + '" disabled>'
        : '<input id="sc-name" placeholder="fx_rates">', 'becomes ' + esc(raw) + '.&lt;name&gt;') +
      row('comes from', '<select id="sc-kind">' + Object.keys(CONNECTORS).map(function (k) {
        return '<option value="' + k + '"' + (k === kind ? ' selected' : '') + '>' +
          esc(CONNECTORS[k].label) + '</option>';
      }).join('') + '</select>', 'how it connects') +
      '<div id="sc-fields"></div>' +
      row('fetch again', '<input id="sc-sched" value="' + esc((job && job.schedule) || '') +
        '" placeholder="leave blank for manual">',
        'a cron line, e.g. <span class="mono">*/5 * * * *</span>. Blank means it only runs ' +
        'when you ask, which is all a backtest needs') +
      row('each fetch', '<select id="sc-mode">' +
        ['replace', 'append'].map(function (m) {
          return '<option' + (m === ((job && job.mode) || 'replace') ? ' selected' : '') + '>' +
            m + '</option>';
        }).join('') + '</select>',
        '<b>append</b> keeps history and is what a live feed wants · <b>replace</b> refetches ' +
        'the lot') +
      '</div>' +
      '<div class="rgo"><button type="button" class="btn go" id="sc-save">' +
      (job ? 'save' : 'add it') + '</button><span id="sc-say" class="faint"></span></div>';

    var fields = function () {
      var k = el('sc-kind').value;
      el('sc-fields').innerHTML = CONNECTORS[k].fields.map(function (f) {
        return row(f[0], '<input class="sc-opt" data-k="' + f[0] + '" value="' +
          esc(o[f[0]] == null ? '' : o[f[0]]) + '">', f[1]);
      }).join('');
    };
    el('sc-kind').onchange = fields;
    fields();
    el('sc-save').onclick = function () { saveSource(job, raw); };
  }

  async function saveSource(job, raw) {
    var say = el('sc-say');
    var name = (job ? job.id : el('sc-name').value || '').trim();
    if (!name) { say.textContent = 'it needs a name'; return; }
    var options = {};
    Array.prototype.forEach.call(document.querySelectorAll('.sc-opt'), function (i) {
      if (i.value.trim()) options[i.getAttribute('data-k')] = i.value.trim();
    });
    say.textContent = 'saving…';
    try {
      await api('/api/sources', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          id: name, to: [raw + '.' + name], connector: el('sc-kind').value,
          schedule: el('sc-sched').value.trim() || null,
          mode: el('sc-mode').value, options: options,
        }),
      });
    } catch (e) {
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    say.innerHTML = '<span class="up">saved</span>. Run it to land its first rows';
    if (window.QANAT) window.QANAT.poll();
  }

  // ------------------------------------------------------------------- stages
  // A stage is the column: what kind of thing lives in it, and what it is for.
  // Editing it here rather than in a settings panel keeps the rule the rest of the
  // console follows -- you change a thing where you can see it.
  var KINDS = {
    raw: 'as it arrived, nothing done to it yet',
    features: 'processed for a model',
    weights: 'the portfolio. One weights table per alpha',
    pnl: 'what a backtest earned, written by a replay',
  };

  async function openStage(id, graph) {
    var st = ((graph && graph.stages) || []).filter(function (x) { return x.id === id; })[0];
    if (!st) return;
    var tables = ((graph && graph.tables) || []).filter(function (t) { return t.stage === id; });
    var ret = (graph && graph.retention) || {};
    var detail = el('detail');
    detail.classList.add('open');
    el('sel-head').textContent = id;
    el('sel-sub').innerHTML = 'a <b>stage</b>: one column of the pipeline';
    el('sel-body').innerHTML =
      '<div class="rform ae">' +
      row('kind', '<select id="sg-kind">' + Object.keys(KINDS).map(function (k) {
        return '<option value="' + k + '"' + (k === st.kind ? ' selected' : '') + '>' +
          k + '</option>';
      }).join('') + '</select>', esc(KINDS[st.kind] || '') +
        '<br>changing this changes what the contract will allow in here') +
      row('what it is for', '<input id="sg-note" value="' + esc(st.description || '') +
        '" placeholder="one line">', 'shown on the rail when you hover it') +
      '</div>' +
      '<div class="iorow"><span class="k">holds</span>' + (tables.length
        ? tables.map(function (t) {
            return '<button type="button" class="chip" data-table="' + esc(t.ref) + '">' +
              esc(t.ref) + '</button>';
          }).join(' ')
        : '<span class="faint">nothing yet</span>') + '</div>' +
      (tables.length ? '<div class="rform ae">' + tables.map(function (t) {
        return row('keep ' + t.name, '<input class="sg-ret" data-ref="' + esc(t.ref) +
          '" value="' + esc(ret[t.ref] || '') + '" placeholder="forever">',
          'e.g. <span class="mono">90d</span> · blank keeps everything');
      }).join('') + '</div>' : '') +
      '<div class="rgo"><button type="button" class="btn go" id="sg-save">save</button>' +
      (st.kind === 'raw' || st.kind === 'weights' ? ''
        : '<button type="button" class="btn" id="sg-del">remove this stage</button>') +
      '<span id="sg-say" class="faint"></span></div>';

    Array.prototype.forEach.call(el('sel-body').querySelectorAll('[data-table]'), function (b) {
      b.onclick = function () { window.QANAT.selectTable(b.getAttribute('data-table')); };
    });
    el('sg-save').onclick = function () { saveStage(id, ret); };
    if (el('sg-del')) el('sg-del').onclick = function () { dropStage(id); };
  }

  async function saveStage(id, ret) {
    var say = el('sg-say');
    say.textContent = 'saving…';
    var keep = {};
    Object.keys(ret).forEach(function (k) { keep[k] = ret[k]; });
    Array.prototype.forEach.call(document.querySelectorAll('.sg-ret'), function (i) {
      var r = i.getAttribute('data-ref');
      if (i.value.trim()) keep[r] = i.value.trim();
      else delete keep[r];
    });
    try {
      await api('/api/stages', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id: id, kind: el('sg-kind').value,
                               description: el('sg-note').value.trim() }),
      });
      await api('/api/retention', {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ retention: keep }),
      });
    } catch (e) {
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    say.innerHTML = '<span class="up">saved</span>';
    if (window.QANAT) window.QANAT.poll();
  }

  async function dropStage(id) {
    var say = el('sg-say');
    say.textContent = 'removing…';
    try {
      await api('/api/stages/' + encodeURIComponent(id), { method: 'DELETE' });
    } catch (e) {
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    say.innerHTML = '<span class="up">removed</span>';
    if (window.QANAT) { window.QANAT.poll(); window.QANAT.closeDetail(); }
  }

  window.AlphaEdit = {
    open: open, openSource: openSource, openStage: openStage,
    setEditing: setEditing, isEditing: isEditing,
  };
})();
