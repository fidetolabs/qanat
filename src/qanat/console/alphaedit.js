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

  var SHELF = null, EDITING = true;

  function el(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  async function api(path, opts) {
    var r = await fetch(path, opts);
    if (!r.ok) throw new Error(unwrap(await r.text()) || r.statusText);
    return r.json();
  }

  function isEditing() { return EDITING; }

  //  Where a new feature step lands: the LAST stage of kind `features`, which is the
  //  one sitting closest to weights. Taking the first match instead picked
  //  `normalized` in any project declaring both -- the scaffold does, and both are
  //  kind `features` -- so a step the form called a feature step was written one
  //  stage earlier than the label beside it promised. The label now reads this too,
  //  rather than hard-coding a name nobody checked against the project.
  function featureStage() {
    var fs = ((SHELF && SHELF.stages) || []).filter(function (s) { return s.kind === 'features'; });
    return (fs[fs.length - 1] || {}).id || '';
  }

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
    //  What the step already reads, as a set, with the rule's own table first. A
    //  brand new alpha starts on the last table offered, which is the furthest
    //  along the pipeline and so the most likely to be the one worth ranking on.
    var picked = (alpha && alpha.reads && alpha.reads.length)
      ? alpha.reads.slice()
      : [SHELF.reads[SHELF.reads.length - 1] || ''].filter(Boolean);
    var primary = (alpha && alpha.primary) || cur.reads || picked[0] || '';
    if (primary && picked.indexOf(primary) < 0) picked.unshift(primary);
    var reads = primary;
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

      //  `from:` is a list on `Step` and always has been -- the scaffold's own
      //  `portfolio` alpha reads three feature tables. Offering one <select> meant
      //  a step the engine runs happily could not be authored here at all. Tick as
      //  many as the step should be allowed to read; the one marked `ranks on` is
      //  what a shelf rule sorts by, and goes to `options.reads`.
      row('reads', '<div class="readset" id="ae-readset">' +
        SHELF.reads.map(function (t) {
          var on = picked.indexOf(t) >= 0;
          return '<label class="rsrow' + (on ? ' on' : '') + '">' +
            '<input type="checkbox" class="ae-read" value="' + esc(t) + '"' +
            (on ? ' checked' : '') + '>' +
            '<span class="rt">' + esc(t) + '</span>' +
            '<span class="rp"><input type="radio" name="ae-primary" class="ae-prim" value="' +
            esc(t) + '"' + (t === primary ? ' checked' : '') +
            (on ? '' : ' disabled') + '><i>ranks on</i></span></label>';
        }).join('') +
        '<button type="button" class="rsnew" id="ae-newbtn">＋ a new feature step…</button>' +
        '</div>',
        '<span id="ae-readnote">' + picked.length + ' selected</span> · every table this step ' +
        'may read. <span class="mono">ctx.read()</span> refuses anything not ticked here') +

      '<div id="ae-newfeat" hidden>' +
      row('feature name', '<input id="nf-name" placeholder="zscore_20">',
          'becomes <span class="mono">' + esc(featureStage()) + '.&lt;name&gt;</span>') +
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

  //  See backtests.js: `for` makes the label the control's name, not just text
  //  sitting next to it.
  function row(label, control, hint) {
    var m = /id="([^"]+)"/.exec(control);
    var attr = m ? ' for="' + m[1] + '"' : '';
    return '<div class="rrow"><label' + attr + '>' + esc(label) + '<i>' + hint + '</i></label>' +
      control + '</div>';
  }

  // the settings a rule takes are its own, so they are drawn from the rule you pick
  function paintOptions(alpha) {
    var pick = el('ae-shelf').value;
    var entry = SHELF.shelf.filter(function (a) { return a.name === pick; })[0];
    //  What the step itself says beats whatever the last run overrode it with.
    var have = Object.assign({}, (alpha && alpha.conditions) || {}, (alpha && alpha.options) || {});
    var host = el('ae-opts');
    if (!entry) {
      //  "keep the script it has" used to draw nothing, and `save` gathers options
      //  from the fields on screen -- so opening any existing alpha and pressing
      //  save wrote an empty options block over its lookback, top_n and window.
      //  They are its script's settings whether or not a shelf rule is selected,
      //  so they are shown, and shown editable.
      var own = Object.keys(have).filter(function (k) { return k !== 'reads'; });
      host.innerHTML = own.length
        ? own.map(function (k) {
            return row(k, '<input class="ae-opt" data-k="' + esc(k) + '" value="' +
              esc(have[k]) + '">', '');
          }).join('') +
          '<div class="bt-note-inline">what this script is already set to.</div>'
        : '';
      return;
    }
    host.innerHTML = Object.keys(entry.options).map(function (k) {
      var v = have[k] != null ? have[k] : entry.options[k];
      return row(k, '<input class="ae-opt" data-k="' + esc(k) + '" value="' + esc(v) + '">',
        k === 'lookback' ? 'how far back it looks, in rows'
          : k === 'top_n' ? 'how many names it holds'
          : k === 'window' ? 'how many rows the measure uses' : '');
    }).join('') +
      '<div class="bt-note-inline">' + esc(entry.why) + '</div>';
  }

  //  Ticked tables, in the order the rows are drawn, with the primary lifted to the
  //  front -- `from[0]` is what `options.reads` will say.
  function chosen() {
    var out = [];
    Array.prototype.forEach.call(document.querySelectorAll('.ae-read'), function (c) {
      if (c.checked) out.push(c.value);
    });
    var p = document.querySelector('.ae-prim:checked');
    if (p && out.indexOf(p.value) > 0) {
      out.splice(out.indexOf(p.value), 1);
      out.unshift(p.value);
    }
    return out;
  }

  function syncReadset() {
    var on = chosen();
    Array.prototype.forEach.call(document.querySelectorAll('.ae-read'), function (c) {
      var radio = c.parentElement.querySelector('.ae-prim');
      c.parentElement.classList.toggle('on', c.checked);
      // A table cannot be what the rule ranks on unless the step may read it.
      if (radio) {
        radio.disabled = !c.checked;
        if (!c.checked) radio.checked = false;
      }
    });
    if (on.length && !document.querySelector('.ae-prim:checked')) {
      var first = document.querySelector('.ae-read:checked');
      if (first) {
        var r = first.parentElement.querySelector('.ae-prim');
        if (r) r.checked = true;
      }
    }
    var note = el('ae-readnote');
    if (note) note.textContent = on.length + ' selected';
  }

  function wire(alpha) {
    var shelf = el('ae-shelf');
    shelf.onchange = function () { paintOptions(alpha); };
    paintOptions(alpha);

    Array.prototype.forEach.call(document.querySelectorAll('.ae-read, .ae-prim'), function (i) {
      i.onchange = syncReadset;
    });
    syncReadset();

    el('ae-newbtn').onclick = function () {
      var box = el('ae-newfeat');
      box.hidden = !box.hidden;
      if (!box.hidden && !el('nf-sql').value) {
        el('nf-sql').value = 'SELECT\n    date,\n    symbol,\n    close\nFROM ' +
          (SHELF.reads[SHELF.reads.length - 1] || 'normalized__prices').replace('.', '__');
      }
    };
    el('ae-save').onclick = function () { save(alpha); };
    var del = el('ae-del');
    if (del) del.onclick = function () {
      //  This sits in the same row as `save`. One misclick used to rewrite
      //  qanat.yaml with no question asked.
      var name = (alpha && (alpha.name || alpha.alpha)) || 'this alpha';
      if (!window.confirm('Remove ' + name + ' from the project?\n\n' +
                          'The script stays on disk, and so does anything it wrote.')) return;
      remove(alpha);
    };
  }

  async function save(alpha) {
    var say = el('ae-say'), go = el('ae-save');
    var name = (alpha ? alpha.name : el('ae-name').value || '').trim();
    if (!name) { say.textContent = 'it needs a name'; return; }
    var reads = chosen();
    go.disabled = true;
    say.textContent = 'saving…';

    try {
      // a new feature step is made first, because the alpha has to read something
      // that exists. It joins the selection rather than replacing it.
      if (!el('ae-newfeat').hidden) {
        var fname = (el('nf-name').value || '').trim();
        if (!fname) throw new Error('the new feature step needs a name');
        var stage = featureStage();
        if (!stage) throw new Error('this project has no features stage');
        var rel = 'steps/' + fname + '.sql';
        await api('/api/steps', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            id: fname, from: [el('nf-reads').value], to: [stage + '.' + fname],
            script: rel, source: el('nf-sql').value,
          }),
        });
        reads.push(stage + '.' + fname);
      }
      if (!reads.length) throw new Error('tick at least one table for it to read');

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
    if (el('sg-del')) el('sg-del').onclick = function () {
      var n = (tables || []).length;
      if (!window.confirm('Remove the stage "' + id + '"?' +
            (n ? '\n\nIt holds ' + n + ' table(s).' : ''))) return;
      dropStage(id);
    };
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

  // ------------------------------------------------------------- wiring a step
  //
  //  One gesture for both halves of the pipeline. Qanat has a single `Step`: one
  //  that lands in `features` is a feature step, the same one landing in `weights`
  //  is an alpha, and `docs/words.md` has no third word because there is no third
  //  thing. So this panel changes by exactly what the model changes by -- the
  //  column it writes into -- and grows the alpha-only fields when that column is
  //  the weights stage.
  //
  //  Inputs are ticked on the graph rather than chosen from a list, because `from:`
  //  is a set across stages and a dropdown cannot say "these three, from two
  //  different columns".

  var WIRING = null;

  function stageKind(id) {
    var s = (SHELF.stages || []).filter(function (x) { return x.id === id; })[0];
    return (s && s.kind) || 'features';
  }

  async function openStep(stage) {
    var detail = el('detail');
    detail.classList.add('open');
    var body = el('sel-body');
    body.innerHTML = '<div class="bt-empty">reading what this project offers…</div>';
    el('sel-head').textContent = 'new step';
    el('sel-sub').textContent = 'tables in, one table out';
    try {
      SHELF = SHELF || await api('/api/shelf');
    } catch (e) {
      body.innerHTML = '<div class="warnbox">' + esc(e.message) + '</div>';
      return;
    }
    //  Never the raw stage: raw is landed as it arrived and a step may not write
    //  there. Offering it would only be offering an error.
    var targets = (SHELF.stages || []).filter(function (s) { return s.kind !== 'raw'; });
    var start = stage || featureStage() || (targets[0] || {}).id;
    WIRING = { stage: start, name: '', picked: {} };
    setEditing(true);
    if (window.QANAT) window.QANAT.dag.setWiring(WIRING);

    body.innerHTML = stepForm(targets);
    wireStep(targets);
  }

  function stepForm(targets) {
    var kind = stageKind(WIRING.stage);
    return '<div class="rform ae">' +
      row('name', '<input id="st-name" placeholder="zscore_20">',
        'becomes <span class="mono" id="st-ref">' + esc(WIRING.stage) + '.&lt;name&gt;</span>') +

      row('writes into', '<select id="st-stage">' + targets.map(function (s) {
        return '<option value="' + esc(s.id) + '"' +
          (s.id === WIRING.stage ? ' selected' : '') + '>' + esc(s.id) +
          ' · ' + esc(s.kind) + '</option>';
      }).join('') + '</select>',
        'the column it lands in. Land it in the <span class="mono">weights</span> stage and ' +
        'you have written an alpha') +

      row('reads', '<div class="wireset" id="st-reads"></div>',
        '<b id="st-count">nothing yet</b> · click tables on the graph to feed them in. ' +
        'This is the list <span class="mono">ctx.read()</span> will allow') +

      row('script', '<select id="st-kind"><option value="sql">.sql · one SELECT</option>' +
        '<option value="py">.py · run(ctx) returns a DataFrame</option></select>',
        'the console used to write SQL only, so half the steps in the scaffold could ' +
        'not be authored here') +
      row('body', '<textarea id="st-src" rows="8"></textarea>',
        'tables are addressed as <span class="mono">stage__table</span> in SQL, and by ' +
        'their ordinary name in <span class="mono">ctx.read()</span>') +

      //  Only meaningful on a step that writes a portfolio. Drawn for every step and
      //  they would read as settings that do nothing.
      '<div id="st-alpha"' + (kind === 'weights' ? '' : ' hidden') + '>' +
      '<div class="kicker sub">because it lands in weights</div>' +
      row('universe', '<select id="st-uni"><option value="">— none —</option>' +
        (SHELF.universes || []).map(function (u) {
          return '<option value="' + esc(u.id) + '">' + esc(u.id) + '</option>';
        }).join('') + '</select>', 'the symbols it is allowed to hold') +
      row('rebalance', '<input id="st-reb" placeholder="as the project says">',
        'how often it decides, e.g. <span class="mono">5d</span>') +
      row('decay', '<input id="st-decay" type="number" min="0" placeholder="0">',
        'hold a blend of the last N portfolios') +
      '</div>' +

      '</div>' +
      '<div class="rgo"><button type="button" class="btn go" id="st-save">wire it in</button>' +
      '<button type="button" class="btn" id="st-cancel">cancel</button>' +
      '<span id="st-say" class="faint"></span></div>';
  }

  function paintWired() {
    var picked = Object.keys(WIRING.picked);
    var host = el('st-reads');
    if (!host) return;
    host.innerHTML = picked.length
      ? picked.map(function (r) {
          return '<span class="wchip" data-ref="' + esc(r) + '">' + esc(r) +
            '<b title="stop reading this">✕</b></span>';
        }).join('')
      : '<span class="faint">nothing selected</span>';
    Array.prototype.forEach.call(host.querySelectorAll('[data-ref]'), function (chip) {
      chip.querySelector('b').onclick = function () {
        if (window.QANAT) window.QANAT.dag.toggleInput(chip.getAttribute('data-ref'));
      };
    });
    el('st-count').textContent = picked.length
      ? picked.length + (picked.length === 1 ? ' table' : ' tables')
      : 'nothing yet';
  }

  function wireStep(targets) {
    var name = el('st-name'), stage = el('st-stage');

    var reref = function () {
      WIRING.name = (name.value || '').trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_');
      el('st-ref').textContent = WIRING.stage + '.' + (WIRING.name || '<name>');
      if (window.QANAT) window.QANAT.dag.setWiring(WIRING);
    };
    name.oninput = reref;

    stage.onchange = function () {
      WIRING.stage = stage.value;
      // Moving the target can make a chosen table illegal -- data only moves
      // forward -- so the graph re-judges and whatever it drops leaves here too.
      if (window.QANAT) window.QANAT.dag.setWiring(WIRING);
      el('st-alpha').hidden = stageKind(WIRING.stage) !== 'weights';
      reref();
      paintWired();
    };

    el('st-kind').onchange = function () {
      var t = el('st-src');
      if (t.value.trim()) return;
      t.value = el('st-kind').value === 'sql'
        ? 'SELECT\n    date,\n    symbol,\n    close\nFROM normalized__prices'
        : 'import pandas as pd\n\n\ndef run(ctx):\n    df = ctx.read("normalized.prices")\n'
          + '    return df\n';
    };

    if (window.QANAT) {
      window.QANAT.dag.onWire = function () { paintWired(); };
    }
    paintWired();

    el('st-cancel').onclick = closeStep;
    el('st-save').onclick = saveStep;
  }

  function closeStep() {
    WIRING = null;
    setEditing(false);
    if (window.QANAT) {
      window.QANAT.dag.onWire = null;
      window.QANAT.dag.setWiring(null);
      window.QANAT.closeDetail();
    }
  }

  async function saveStep() {
    var say = el('st-say'), go = el('st-save');
    var name = WIRING.name;
    if (!name) { say.textContent = 'it needs a name'; return; }
    var picked = Object.keys(WIRING.picked);
    if (!picked.length) { say.textContent = 'click a table on the graph to feed it in'; return; }
    var body = el('st-src').value;
    if (!body.trim()) { say.textContent = 'it needs a body to run'; return; }

    var payload = {
      id: name, from: picked, to: [WIRING.stage + '.' + name],
      script: 'steps/' + name + '.' + el('st-kind').value,
      source: body,
    };
    if (stageKind(WIRING.stage) === 'weights') {
      payload.universe = el('st-uni').value || null;
      payload.rebalance = el('st-reb').value.trim() || null;
      var d = parseInt(el('st-decay').value, 10);
      if (d) payload.decay = d;
    }
    go.disabled = true;
    say.textContent = 'saving…';
    try {
      await api('/api/steps', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      go.disabled = false;
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    go.disabled = false;
    closeStep();
    if (window.QANAT) window.QANAT.poll();
    if (window.repaintBook) window.repaintBook();
  }

  window.AlphaEdit = {
    open: open, openSource: openSource, openStage: openStage, openStep: openStep,
    setEditing: setEditing, isEditing: isEditing,
  };
})();
