/* The session, and the stage that follows it.
 *
 * The Ask bar used to be a one-line strip repeated identically on four pages --
 * identical because it had no relationship to any of them. It was a text field
 * above an interface you drove yourself, which is the wrong shape for a console
 * whose agent can already reach every tool it exposes.
 *
 * So: the thread is the navigation. What the agent does appears here as it happens,
 * and the surface beside it switches to whatever the last meaningful call was
 * about. Nothing new had to be reported to make that work -- the agent talks to
 * this console's own HTTP API, so every tool call is already a request arriving at
 * this server, and `/api/trace` is the server saying what it was asked.
 *
 * Following is a default, not a hijack: the pin stops the stage moving while the
 * thread goes on saying what changed underneath.
 */
(function () {
  'use strict';

  function el(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var SEQ = 0;          // last trace entry we have seen
  var PINNED = false;   // the stage stays where the reader put it
  var LAST = null;      // the surface we last moved to, so we do not re-switch
  var AUTO = false;     // this move came from the trace, not from a click

  //  A surface name from the trace, resolved to something the console can do.
  //  Most are a page; two open a panel on a page, and one is a question.
  //  Plain words for a surface, for the line above the stage.
  var SAYS = { data: 'your connected data', alpha: 'the pipeline',
               backtest: 'the replay and what it earned', live: 'what it is earning forward',
               check: 'the contract report' };
  function says(surface) {
    if (!surface) return '—';
    if (surface.indexOf('table:') === 0) return surface.slice(6);
    if (surface.indexOf('job:') === 0) return 'step ' + surface.slice(4);
    return SAYS[surface] || surface;
  }

  function head(surface, why) {
    var w = el('sh-what'), y = el('sh-why');
    if (w) w.textContent = says(surface);
    if (y) {
      y.textContent = PINNED ? 'pinned by you' : (why || '');
      y.classList.toggle('pinned', PINNED);
    }
  }

  function go(surface, why) {
    if (!surface) return;
    if (PINNED || surface === LAST) { head(LAST || surface, why); return; }
    LAST = surface;
    head(surface, why);
    AUTO = true;
    var Q = window.QANAT;
    if (!Q) return;

    if (surface.indexOf('table:') === 0) {
      Q.setPage('alpha');
      Q.selectTable(surface.slice(6));
      return;
    }
    if (surface.indexOf('job:') === 0) {
      Q.setPage('alpha');
      return;
    }
    if (surface === 'conditions') {
      // handled by the card in the thread; the stage stays put
      LAST = null;
      return;
    }
    if (surface === 'check') { Q.setPage('alpha'); return; }
    if (['data', 'alpha', 'backtest', 'live'].indexOf(surface) >= 0) Q.setPage(surface);
  }

  //  The calls that change the project, and what each one is about.
  var WRITES = { save_alpha: 'alpha', save_step: 'step', save_source: 'source',
                 remove_alpha: 'alpha', remove_step: 'step', remove_source: 'source',
                 'edit qanat.yaml': 'project' };

  //  What just changed, read back off the project rather than guessed from the
  //  request. `plan` already knows what drifted; this is the short version.
  async function diffCard(t) {
    var g;
    try { g = await fetch('/api/state').then(function (r) { return r.json(); }); }
    catch (e) { return; }
    append('<div class="diffcard"><div class="h">' + esc(t.label) + ' · qanat.yaml</div>' +
      '<div class="row add"><span>alphas</span><span>' + esc(String(g.alpha.wired)) +
        ' wired</span></div>' +
      '<div class="row"><span>sources</span><span>' + esc(String(g.data.sources)) +
        '</span></div>' +
      '<div class="row"><span>backtests</span><span>' + esc(String(g.backtest.runs)) +
        '</span></div>' +
      '<div class="go"><button type="button" class="btn" data-undo="1">undo · restore .bak' +
        '</button></div></div>');
  }

  //  A tool line that says only what was called is a log, not context. Each one
  //  opens onto what the call actually returned -- rows, a coverage summary, the
  //  net edge, the jobs that ran -- because "it called sample_table" and "here are
  //  the first five rows it saw" are not the same amount of help.
  function line(t) {
    var kind = t.by || 'api';
    var bad = t.ok ? '' : ' bad';
    var id = 'tc' + t.seq;
    return '<div class="ctwrap">' +
      '<div class="ct by-' + kind + bad + '" data-surface="' + esc(t.surface) + '" ' +
        'data-out="' + id + '" data-label="' + esc(t.label) + '">' +
        '<i>' + esc(kind) + '</i><b>' + esc(t.label) + '</b>' +
        '<span class="grow"></span><span class="cx">▾</span></div>' +
      '<div class="cout" id="' + id + '" hidden></div>' +
      '</div>';
  }

  // ---------------------------------------------------------- what it returned
  async function j(path) { return fetch(path).then(function (r) { return r.json(); }); }
  function n(x) {
    if (x == null) return '—';
    if (x >= 1e6) return (x / 1e6).toFixed(1) + 'M';
    if (x >= 1e3) return (x / 1e3).toFixed(1) + 'k';
    return String(x);
  }
  function pc(x, dp) {
    return x == null ? '—' : (x >= 0 ? '+' : '') + (x * 100).toFixed(dp == null ? 2 : dp) + '%';
  }

  //  A few rows, the way you would glance at them -- not the whole table, which is
  //  what the stage beside this is for.
  function rowsOf(d) {
    var cols = (d.columns || []).slice(0, 4).map(function (c) { return c.name; });
    var rows = (d.sample || []).slice(0, 4);
    if (!rows.length) return '<div class="cnote">no rows</div>';
    return '<div class="cnote">' + n(d.rows) + ' rows · ' + (d.columns || []).length +
      ' columns</div>' +
      '<table class="ctab"><thead><tr>' + cols.map(function (c) {
        return '<th>' + esc(c) + '</th>';
      }).join('') + '</tr></thead><tbody>' + rows.map(function (r) {
        return '<tr>' + cols.map(function (c) {
          var v = r[c];
          if (typeof v === 'number') v = Math.abs(v) < 1e-4 && v !== 0 ? v.toExponential(2)
            : (Math.round(v * 1e4) / 1e4);
          return '<td>' + esc(String(v == null ? '' : v)).slice(0, 22) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table>';
  }

  function profileOf(d) {
    return '<div class="cnote">' + n(d.rows) + ' rows · covers ' +
      esc(String((d.columns.filter(function (c) { return c.name === d.time_column; })[0] || {}).min
        || '').slice(0, 10)) + ' → ' +
      esc(String((d.columns.filter(function (c) { return c.name === d.time_column; })[0] || {}).max
        || '').slice(0, 10)) + '</div>' +
      d.columns.slice(0, 5).map(function (c) {
        var f = c.fill == null ? 1 : c.fill;
        return '<div class="cbar"><span>' + esc(c.name) + '</span>' +
          '<i><u style="width:' + (f * 100).toFixed(0) + '%"></u></i>' +
          '<em>' + n(c.distinct) + ' distinct</em></div>';
      }).join('');
  }

  function btOf(b, full) {
    var tt = (full && full.totals) || {};
    var seg = (full && full.segments) || {};
    var oos = seg.out_of_sample;
    return '<div class="cgrid">' +
      '<div><span>net</span><b class="' + (b.net >= 0 ? 'up' : 'down') + '">' +
        pc(b.net) + '</b></div>' +
      '<div><span>periods</span><b>' + n(b.periods) + '</b></div>' +
      (tt.held_periods != null
        ? '<div><span>held</span><b>' + n(tt.held_periods) + '</b></div>' +
          '<div><span>flat</span><b class="' + (tt.flat_periods ? 'warn' : '') + '">' +
            n(tt.flat_periods) + '</b></div>'
        : '') +
      (oos ? '<div><span>out of sample</span><b>' + pc(oos.net) + '</b></div>' : '') +
      '<div><span>rebalance</span><b>' + esc(b.rebalance || '—') + '</b></div>' +
      '</div>';
  }

  function runsOf(rows) {
    if (!rows.length) return '<div class="cnote">nothing has run yet</div>';
    return rows.slice(0, 6).map(function (r) {
      return '<div class="cline' + (r.status === 'ok' ? '' : ' bad') + '">' +
        '<span>' + esc(r.job_id) + '</span>' +
        '<em>' + (r.status === 'ok' ? n(r.rows_out) + ' rows → ' + esc(r.targets || '')
                                    : esc(r.error || r.status)) + '</em></div>';
    }).join('');
  }

  function checkOf(d) {
    var out = '<div class="cnote">' + (d.ok ? 'the contract holds' : 'the contract is broken') +
      '</div>';
    (d.errors || []).forEach(function (e) {
      out += '<div class="cline bad"><em>' + esc(e) + '</em></div>';
    });
    (d.warnings || []).slice(0, 4).forEach(function (w) {
      out += '<div class="cline warn"><em>' + esc(w) + '</em></div>';
    });
    return out;
  }

  //  Fetched when it is opened, from the same endpoint the call used. The console
  //  never saw the agent's response body -- it only saw that the request happened.
  async function fill(box, surface, label) {
    box.innerHTML = '<div class="cnote">reading…</div>';
    try {
      if (surface.indexOf('table:') === 0) {
        var ref = surface.slice(6).split('.');
        box.innerHTML = rowsOf(await j('/api/table/' + ref[0] + '/' + ref[1] + '?limit=4'));
        return;
      }
      if (label.indexOf('profile_table') === 0) {
        var pr = label.split('· ')[1].split('.');
        box.innerHTML = profileOf(await j('/api/profile/' + pr[0] + '/' + pr[1]));
        return;
      }
      if (surface === 'backtest') {
        var list = await j('/api/backtests?limit=1');
        if (!list.length) { box.innerHTML = '<div class="cnote">no run</div>'; return; }
        var full = null;
        try { full = await j('/api/backtests/' + list[0].run_id); } catch (e) { /* older run */ }
        box.innerHTML = btOf(list[0], full);
        return;
      }
      if (surface === 'check') { box.innerHTML = checkOf(await j('/api/check')); return; }
      box.innerHTML = runsOf(await j('/api/runs?limit=6'));
    } catch (e) {
      box.innerHTML = '<div class="cnote">could not read it back</div>';
    }
  }

  function empty() {
    return '<div class="crempty">Ask for something and it happens here — what the agent ' +
      'reads, what it changes, and what you click yourself. The panel on the right follows ' +
      'along.</div>';
  }

  //  What is worth asking from here, read off the project rather than a fixed list.
  //  An empty console that says only "ask me anything" is asking the person to guess
  //  the shape of the product; these are the next move in the arc it is actually on.
  //  Everything already asked this session. The state behind a question often has
  //  not moved by the time it is answered -- ask "what data do I have" and you
  //  still have no alpha -- so the same three would be offered straight back, with
  //  the one just clicked at the top of them.
  var ASKED = Object.create(null);
  function asked(q) { ASKED[String(q).trim().toLowerCase()] = 1; }

  async function suggest() {
    var host = el('cr-thread');
    if (!host) return;
    var d;
    try { d = await fetch('/api/next').then(function (r) { return r.json(); }); }
    catch (e) { return; }
    var s = (d.suggestions || []).filter(function (x) {
      return !ASKED[String(x.q).trim().toLowerCase()];
    });
    if (!s.length) return;

    // only the newest block offers them: a thread stacked with stale suggestions
    // is a thread telling you to do things you already did
    var old = host.querySelectorAll('.suggest');
    Array.prototype.forEach.call(old, function (n) { n.remove(); });

    append('<div class="suggest"><div class="sh">next</div>' +
      s.map(function (x) {
        return '<button type="button" class="sq" data-q="' + esc(x.q) + '">' +
          '<span>' + esc(x.q) + '</span><em>' + esc(x.why) + '</em></button>';
      }).join('') + '</div>');
  }

  function append(html) {
    var host = el('cr-thread');
    if (!host) return;
    var first = host.querySelector('.crempty');
    if (first) first.remove();
    host.insertAdjacentHTML('beforeend', html);
    host.scrollTop = host.scrollHeight;
  }

  //  Clicking a line takes you back to what it was about. The thread is the history
  //  of the session, so it has to be navigable, not just readable.
  function wireLines() {
    var host = el('cr-thread');
    if (!host || host._wired) return;
    host._wired = true;
    host.addEventListener('click', function (ev) {
      var sq = ev.target.closest('.sq[data-q]');
      if (sq) {
        var box = el('ask-q');
        if (box && !box.disabled) {
          box.value = sq.getAttribute('data-q');
          box.focus();
          var form = el('ask-form');
          if (form) form.dispatchEvent(new Event('submit', { cancelable: true }));
        }
        return;
      }
      var row = ev.target.closest('.ct[data-surface]');
      if (!row) return;
      var box = el(row.getAttribute('data-out'));
      if (box) {
        var open = box.hasAttribute('hidden');
        if (open) {
          box.removeAttribute('hidden');
          row.classList.add('open');
          if (!box.dataset.filled) {
            box.dataset.filled = '1';
            fill(box, row.getAttribute('data-surface'), row.getAttribute('data-label'));
          }
        } else {
          box.setAttribute('hidden', '');
          row.classList.remove('open');
        }
      }
      //  Opening the output is not the same as wanting the stage moved, so the
      //  stage only follows when you click the label itself.
      if (ev.target.closest('b')) {
        LAST = null;
        var was = PINNED; PINNED = false;
        go(row.getAttribute('data-surface'));
        PINNED = was;
      }
    });
  }

  async function poll() {
    var r;
    try {
      r = await fetch('/api/trace?after=' + SEQ).then(function (x) { return x.json(); });
    } catch (e) { return; }
    var rows = r.trace || [];
    if (!rows.length) return;
    SEQ = r.seq;

    rows.forEach(function (t) {
      seal();
      append(line(t));
      // A write is worth more than a line: show what it did to the file.
      if (t.ok && WRITES[t.label.split(' · ')[0]]) diffCard(t);
    });

    // the surface of the last thing that happened, which is what to show
    for (var i = rows.length - 1; i >= 0; i--) {
      if (rows[i].ok && rows[i].surface) {
        if (rows[i].surface === 'conditions' && window.Conditions) {
          window.Conditions.offer();
        }
        go(rows[i].surface, 'following ' + rows[i].label.split(' · ')[0]);
        // the newest one opens itself: context should not need a click
        var row = el('tc' + rows[i].seq);
        if (row) {
          row.removeAttribute('hidden');
          row.dataset.filled = '1';
          var head = row.previousElementSibling;
          if (head) head.classList.add('open');
          fill(row, rows[i].surface, rows[i].label);
        }
        break;
      }
    }
  }

  //  What the person typed, and what the agent said back, belong in the same thread
  //  as the tool calls -- otherwise there are two histories and neither is complete.
  //  The agent answers in markdown, and it uses tables -- asked for a PnL table it
  //  returns one. Escaped into a div that collapses whitespace, that arrived as a
  //  single run-on line of pipes and dashes: the header, the `|---|` rule and the
  //  first rows all on top of each other.
  //
  //  Escape first, format second, so nothing here can introduce markup. Only the
  //  handful of things the agent actually uses are rendered; anything else stays
  //  the text it was.
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  }

  function cells(row) {
    return row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|')
      .map(function (c) { return c.trim(); });
  }
  function isRule(row) { return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(row) && row.indexOf('-') >= 0; }

  function md(text) {
    var lines = esc(text).split(/\r?\n/);
    var out = [], i = 0;
    while (i < lines.length) {
      var ln = lines[i];

      // a table: a header row, a rule under it, then rows until they stop
      if (ln.indexOf('|') >= 0 && i + 1 < lines.length && isRule(lines[i + 1])) {
        var head = cells(ln);
        i += 2;
        var body = [];
        while (i < lines.length && lines[i].indexOf('|') >= 0) {
          var r = cells(lines[i]);
          if (r.join('')) body.push(r);
          i++;
        }
        out.push('<div class="mdtwrap"><table class="mdt"><thead><tr>' +
          head.map(function (h) { return '<th>' + inline(h) + '</th>'; }).join('') +
          '</tr></thead><tbody>' + body.map(function (r) {
            return '<tr>' + r.map(function (c) {
              return '<td>' + inline(c) + '</td>';
            }).join('') + '</tr>';
          }).join('') + '</tbody></table></div>');
        continue;
      }

      if (/^\s*[-*]\s+/.test(ln)) {
        var items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push('<li>' + inline(lines[i].replace(/^\s*[-*]\s+/, '')) + '</li>');
          i++;
        }
        out.push('<ul class="mdl">' + items.join('') + '</ul>');
        continue;
      }

      if (!ln.trim()) { i++; continue; }

      var para = [];
      while (i < lines.length && lines[i].trim() &&
             lines[i].indexOf('|') < 0 && !/^\s*[-*]\s+/.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      if (para.length) out.push('<p>' + inline(para.join(' ')) + '</p>');
      else i++;
    }
    return out.join('');
  }

  function said(who, text) {
    if (!text) return;
    if (who === 'me') asked(text);
    var body = who === 'me' ? '<p>' + esc(text) + '</p>' : md(text);
    append('<div class="cm ' + (who === 'me' ? 'me' : 'agent') + '">' + body + '</div>');
  }

  //  The reply while it is still being written. One element, rewritten as the text
  //  grows, so the thread does not fill with a hundred copies of a paragraph.
  //  Markdown is re-rendered each time: a table appears the moment its rule line
  //  lands, rather than sitting as pipes until the end.
  //  How much of the reply has already been sealed into a finished block above a
  //  tool call. The server hands back the whole prose so far, so without this the
  //  paragraphs written before the call would be drawn twice.
  var SEALED = 0;

  //  A tool call interrupts the prose. Sealing what is written so far keeps the
  //  thread in the order things happened -- text, call, text -- instead of leaving
  //  one growing block pinned above every call that came after it.
  function seal() {
    var host = el('cr-thread');
    var live = host && host.querySelector('.cm.live');
    if (!live) return;
    if (!live.textContent.trim()) { live.remove(); return; }
    live.classList.remove('live');
    SEALED = STREAM_LEN;
  }

  var STREAM_LEN = 0;

  //  Smoothing. Even on one connection the text arrives in bursts -- a model emits
  //  a clause at a time, not a character. Holding a target and walking toward it a
  //  few characters per frame turns those bursts into something that reads like
  //  writing. The rate scales with the backlog, so it never falls behind: a long
  //  burst is caught up quickly rather than trickling out for a minute.
  var TARGET = '', SHOWN = 0, RAF = null;

  function reveal() {
    RAF = null;
    if (SHOWN >= TARGET.length) return;
    var behind = TARGET.length - SHOWN;
    SHOWN += Math.max(2, Math.ceil(behind / 8));
    if (SHOWN > TARGET.length) SHOWN = TARGET.length;
    paintStream(TARGET.slice(0, SHOWN));
    RAF = requestAnimationFrame(reveal);
  }

  function streaming(text) {
    TARGET = text || '';
    if (SHOWN > TARGET.length) SHOWN = 0;      // a new answer started
    if (!RAF) RAF = requestAnimationFrame(reveal);
  }

  function paintStream(text) {
    var host = el('cr-thread');
    if (!host) return;
    STREAM_LEN = text ? text.length : 0;
    var rest = text ? text.slice(SEALED) : '';
    var live = host.querySelector('.cm.live');
    if (!rest.trim()) { if (live && !live.textContent.trim()) live.remove(); return; }
    text = rest;
    if (!live) {
      var first = host.querySelector('.crempty');
      if (first) first.remove();
      host.insertAdjacentHTML('beforeend', '<div class="cm agent live"></div>');
      live = host.querySelector('.cm.live');
    }
    live.innerHTML = md(text);
    //  Only follow the text down if the reader is already at the bottom. Scrolling
    //  somebody back while they are reading what the agent said earlier is worse
    //  than making them scroll.
    if (host.scrollHeight - host.scrollTop - host.clientHeight < 90) {
      host.scrollTop = host.scrollHeight;
    }
  }

  function endStream() {
    if (RAF) { cancelAnimationFrame(RAF); RAF = null; }
    TARGET = ''; SHOWN = 0;
    var host = el('cr-thread');
    var live = host && host.querySelector('.cm.live');
    if (live) live.remove();
    SEALED = 0;
    STREAM_LEN = 0;
  }

  //  One connection for the whole turn: the reply and the calls it makes, in the
  //  order they happened. Falls back to the poll if the browser cannot open it.
  var ES = null;
  function listen() {
    if (ES) { ES.close(); ES = null; }
    try {
      ES = new EventSource('/api/ask/stream?after=' + SEQ);
    } catch (e) { return false; }
    ES.onmessage = function (ev) {
      var d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.seq != null) SEQ = d.seq;
      (d.trace || []).forEach(function (row) {
        seal();
        append(line(row));
        if (row.ok && WRITES[row.label.split(' · ')[0]]) diffCard(row);
        if (row.ok && row.surface) {
          if (row.surface === 'conditions' && window.Conditions) window.Conditions.offer();
          go(row.surface, 'following ' + row.label.split(' · ')[0]);
        }
      });
      if (d.ask && !d.ask.done) streaming(d.ask.partial || '');
      if (d.ask && window.paintAskState) window.paintAskState(d.ask);
    };
    ES.addEventListener('end', function () { if (ES) { ES.close(); ES = null; } });
    ES.onerror = function () { if (ES) { ES.close(); ES = null; } };
    return true;
  }

  function setPinned(on) {
    PINNED = !!on;
    var b = el('cr-pin');
    if (!b) return;
    b.classList.toggle('pinned', PINNED);
    b.textContent = PINNED ? 'stage: pinned' : 'stage: following';
    b.title = PINNED
      ? 'the stage stays here; the thread keeps reporting'
      : 'the stage follows what the agent is doing';
    head(LAST, PINNED ? 'pinned by you' : 'following along');
  }

  function start() {
    var host = el('cr-thread');
    if (host && !host.children.length) host.innerHTML = empty();
    wireLines();
    var pin = el('cr-pin');
    if (pin) pin.onclick = function () { setPinned(!PINNED); };
    setPinned(false);
    poll();
    suggest();                 // an empty console still has a next move
    setInterval(poll, 1500);
  }

  //  Navigating by hand has to say so too, or the header goes on claiming to be
  //  following a tool call that finished three clicks ago.
  function showing(surface) {
    if (AUTO) { AUTO = false; return; }   // the trace already said why
    LAST = surface;
    head(surface, PINNED ? 'pinned by you' : 'you opened it');
  }

  window.Thread = { said: said, poll: poll, start: start, showing: showing,
                    suggest: suggest, streaming: streaming, endStream: endStream,
                    asked: asked,
                    listen: listen,
                    pinned: function () { return PINNED; } };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
