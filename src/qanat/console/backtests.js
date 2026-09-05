/* The backtest report.
 *
 * Ported from the alpha-research prototype's backtest page, which was drawn for
 * exactly this and not for a stream: equity, drawdown, rolling Sharpe, the spread
 * of period results, month by month, year by year, and the cost model.
 *
 * Two things it insists on:
 *
 *   * **in sample and out of sample are shown apart.** The lookback, rebalance and
 *     decay were chosen by someone looking at the in-sample half, so that number
 *     is partly a measure of the choosing. The out-of-sample column was not
 *     allowed to argue back.
 *
 *   * **a point on the curve opens.** Click a rebalance and the panel below narrows
 *     to that period: what was held, what each name returned, what had to be traded.
 *     The server recomputes it from what the run recorded, so the drill-down and the
 *     curve cannot drift apart.
 *
 * Numbers come from /api/backtests. Nothing here is simulated.
 */
(function () {
  'use strict';

  var PANEL = null, LIST = [], CURRENT = null, TIMER = null, DETAIL = null, VIEW = 'all';
  var LIST_SIG = null, BOOK = [], WIRED = null, PICKED = null, STATS = {};
  //: a span of rebalances dragged on the chart. Null is the whole period, which is
  //: what a run means until someone narrows it.
  var RANGE = null;
  //: every chart drawn this paint, so hovering one can say what it is pointing at
  var CHARTS = {}, CHART_N = 0;

  function el(id) { return document.getElementById(id); }
  function pct(x, dp) { return (x == null) ? '—' : (x * 100).toFixed(dp == null ? 2 : dp) + '%'; }
  function sign(x) { return x == null ? '' : (x > 0 ? 'up' : (x < 0 ? 'down' : '')); }
  function esc(t) {
    return String(t).replace(/[&<>]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
    });
  }

  async function api(path) {
    var r = await fetch(path);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  // ------------------------------------------------------------------ series
  function equity(ps) {
    var out = [1], i;
    for (i = 0; i < ps.length; i++) out.push(out[out.length - 1] * (1 + ps[i].net));
    return out;
  }

  function drawdown(eq) {
    var peak = -Infinity;
    return eq.map(function (v) { peak = Math.max(peak, v); return v / peak - 1; });
  }

  function mean(a) {
    return a.length ? a.reduce(function (x, y) { return x + y; }, 0) / a.length : 0;
  }

  function stdev(a) {
    if (a.length < 2) return 0;
    var m = mean(a);
    return Math.sqrt(mean(a.map(function (v) { return (v - m) * (v - m); })) * a.length / (a.length - 1));
  }

  // Periods are rebalances, not days, so the annualisation comes from the rebalance
  // gap rather than a hard-coded 252 that would quietly be wrong.
  function perYear(rebalance) {
    var m = /^(\d+)\s*([a-z]+)$/.exec(String(rebalance || '1d'));
    if (!m) return 252;
    var days = { d: 1, day: 1, days: 1, w: 7, week: 7, weeks: 7 }[m[2]] || 1;
    return 365 / Math.max(1e-9, parseInt(m[1], 10) * days);
  }

  function rollingSharpe(ps, win, rebalance) {
    var k = Math.sqrt(perYear(rebalance)), out = [];
    for (var i = 0; i < ps.length; i++) {
      if (i + 1 < win) { out.push(null); continue; }
      var slice = ps.slice(i + 1 - win, i + 1).map(function (p) { return p.net; });
      var sd = stdev(slice);
      out.push(sd === 0 ? null : mean(slice) / sd * k);
    }
    return out;
  }

  function byMonth(ps) {
    var m = {};
    ps.forEach(function (p) {
      var key = p.as_of.slice(0, 7);
      m[key] = (m[key] == null ? 1 : m[key]) * (1 + p.net);
    });
    return Object.keys(m).sort().map(function (k) { return { key: k, net: m[k] - 1 }; });
  }

  function byYear(ps) {
    var y = {};
    ps.forEach(function (p) {
      var key = p.as_of.slice(0, 4);
      if (!y[key]) y[key] = { key: key, eq: 1, n: 0, win: 0, gross: 0, cost: 0 };
      y[key].eq *= 1 + p.net;
      y[key].n += 1;
      y[key].win += p.net > 0 ? 1 : 0;
      y[key].gross += p.gross;
      y[key].cost += p.fees + p.slippage;
    });
    return Object.keys(y).sort().map(function (k) {
      var r = y[k];
      r.net = r.eq - 1;
      return r;
    });
  }

  // --------------------------------------------------------------------- svg
  var W = 760;

  // the svg body only, so several series can share one hover box
  function lineSvg(values, h, opts) {
    opts = opts || {};
    var pts = values.filter(function (v) { return v != null; });
    if (pts.length < 2) return '<div class="bt-empty">not enough periods yet</div>';
    var lo = opts.lo != null ? opts.lo : Math.min.apply(null, pts);
    var hi = opts.hi != null ? opts.hi : Math.max.apply(null, pts);
    if (hi - lo < 1e-12) hi = lo + 1e-12;
    var x = function (k) { return 6 + (W - 12) * (k / (values.length - 1)); };
    var y = function (v) { return 6 + (h - 12) * (1 - (v - lo) / (hi - lo)); };

    var d = '', started = false;
    values.forEach(function (v, k) {
      if (v == null) { started = false; return; }
      d += (started ? 'L' : 'M') + x(k).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      started = true;
    });
    var zeroAt = opts.zero != null && lo <= opts.zero && hi >= opts.zero
      ? '<line class="bt-base" x1="6" x2="' + (W - 6) + '" y1="' + y(opts.zero).toFixed(1) +
        '" y2="' + y(opts.zero).toFixed(1) + '"></line>' : '';
    // The in-sample half is shaded, so the eye separates the two without reading.
    var band = opts.isCount
      ? '<rect class="bt-band" x="6" y="4" width="' + Math.max(0, x(opts.isCount) - 6).toFixed(1) +
        '" height="' + (h - 8) + '"></rect>' : '';
    var mark = opts.markAt != null && opts.markAt >= 0 && values[opts.markAt] != null
      ? '<circle class="bt-mark" cx="' + x(opts.markAt).toFixed(1) + '" cy="' +
        y(values[opts.markAt]).toFixed(1) + '" r="3.4"></circle>' : '';
    return '<svg class="bt-svg" style="height:' + h + 'px" viewBox="0 0 ' + W + ' ' + h +
      '" preserveAspectRatio="none">' + band + zeroAt +
      (opts.fill ? '<path class="bt-fill ' + (opts.cls || '') + '" d="' + d.trim() +
        'L' + x(values.length - 1).toFixed(1) + ' ' + y(opts.zero).toFixed(1) +
        ' L' + x(0).toFixed(1) + ' ' + y(opts.zero).toFixed(1) + ' Z"></path>' : '') +
      '<path class="bt-line ' + (opts.cls || '') + '" d="' + d.trim() + '"></path>' + mark +
      '</svg>';
  }

  // one hover box around one or more plots that share an x-axis
  function chartBox(svgs, opts) {
    opts = opts || {};
    var id = 'c' + (++CHART_N);
    CHARTS[id] = {
      kind: opts.kind || 'line', values: opts.values || [], labels: opts.labels || [],
      fmt: opts.fmt || 'pct', offset: opts.offset || 0, clickable: !!opts.clickable,
      extra: opts.extra || null,
    };
    return '<div class="chartbox" data-chart="' + id + '">' + svgs +
      '<i class="guide"></i><b class="ctip"></b></div>';
  }

  function line(values, h, opts) {
    opts = opts || {};
    return chartBox(lineSvg(values, h, opts), {
      values: values, labels: opts.labels, fmt: opts.fmt,
      offset: opts.offset, clickable: opts.clickable,
    });
  }

  function bars(values, h, opts) {
    opts = opts || {};
    if (!values.length) return '';
    var m = Math.max.apply(null, values.map(function (v) { return Math.abs(v.v); })) || 1e-9;
    var mid = h / 2, step = (W - 12) / values.length;
    var bw = Math.max(1, step - 1.2);
    var rects = values.map(function (v, k) {
      var bh = Math.abs(v.v) / m * (mid - 5);
      return '<rect class="bt-bar ' + sign(v.v) +
        (opts.markAt === k ? ' on' : '') + '" data-i="' + k + '" x="' + (6 + k * step).toFixed(1) +
        '" y="' + (v.v >= 0 ? mid - bh : mid).toFixed(1) + '" width="' + bw.toFixed(1) +
        '" height="' + Math.max(bh, 0.8).toFixed(1) + '">' +
        '<title>' + esc(v.label) + '  ' + pct(v.v, 3) + '</title></rect>';
    }).join('');
    var band = opts.isCount
      ? '<rect class="bt-band" x="6" y="2" width="' + Math.max(0, opts.isCount * step).toFixed(1) +
        '" height="' + (h - 4) + '"></rect>' : '';
    var id = 'c' + (++CHART_N);
    CHARTS[id] = {
      kind: 'bars', values: values.map(function (v) { return v.v; }),
      labels: values.map(function (v) { return v.label; }), fmt: 'pct',
      offset: 0, clickable: !!opts.clickable, name: opts.name || '',
    };
    return '<div class="chartbox" data-chart="' + id + '">' +
      '<svg class="bt-svg bars' + (opts.clickable ? ' clickable' : '') +
      '" viewBox="0 0 ' + W + ' ' + h + '" preserveAspectRatio="none">' + band +
      '<line class="bt-base" x1="6" x2="' + (W - 6) + '" y1="' + mid + '" y2="' + mid + '"></line>' +
      rects + '</svg><i class="guide"></i><b class="ctip"></b></div>';
  }

  function histogram(ps, h) {
    if (ps.length < 4) return '<div class="bt-empty">not enough periods yet</div>';
    var xs = ps.map(function (p) { return p.net; });
    var lo = Math.min.apply(null, xs), hi = Math.max.apply(null, xs);
    var n = Math.min(21, Math.max(7, Math.round(Math.sqrt(xs.length))));
    var w = (hi - lo) / n || 1e-9, counts = new Array(n).fill(0);
    xs.forEach(function (v) { counts[Math.min(n - 1, Math.floor((v - lo) / w))]++; });
    var top = Math.max.apply(null, counts) || 1, step = (W - 12) / n;
    var rects = counts.map(function (c, k) {
      var centre = lo + w * (k + 0.5);
      return '<rect class="bt-bar ' + (centre >= 0 ? 'up' : 'down') + '" x="' +
        (6 + k * step).toFixed(1) + '" y="' + (h - 6 - (c / top) * (h - 14)).toFixed(1) +
        '" width="' + (step - 1.2).toFixed(1) + '" height="' + ((c / top) * (h - 14)).toFixed(1) +
        '"><title>' + pct(centre, 2) + ' · ' + c + ' period' + (c === 1 ? '' : 's') +
        '</title></rect>';
    }).join('');
    var zx = 6 + ((0 - lo) / (hi - lo || 1)) * (W - 12);
    var zero = (lo <= 0 && hi >= 0)
      ? '<line class="bt-base" x1="' + zx.toFixed(1) + '" x2="' + zx.toFixed(1) +
        '" y1="2" y2="' + (h - 4) + '"></line>' : '';
    return '<svg class="bt-svg bars" viewBox="0 0 ' + W + ' ' + h + '" preserveAspectRatio="none">' +
      rects + zero + '</svg>';
  }

  // ------------------------------------------------------------------ panels
  function panel(title, right, body) {
    return '<section class="bt-panel"><div class="bt-ph"><h4>' + title + '</h4>' +
      '<span class="r">' + (right || '') + '</span></div>' + body + '</section>';
  }

  function costModel(t, c) {
    if (!t || !t.periods) return '';
    var per = function (v) { return t.periods ? v / t.periods : 0; };
    return '<table class="t bt-cost"><tbody>' +
      '<tr><td>commission</td><td class="n">' + (c.fee_bps || 0) + ' bps</td>' +
        '<td class="n down">' + pct(-t.fees, 3) + '</td></tr>' +
      '<tr><td>slippage</td><td class="n">' + (c.slippage_bps || 0) + ' bps</td>' +
        '<td class="n down">' + pct(-t.slippage, 3) + '</td></tr>' +
      '<tr><td>turnover<i class="gloss">1.0 = the whole book changed hands</i></td>' +
        '<td class="n">' + t.turnover.toFixed(2) + '</td>' +
        '<td class="n faint">' + per(t.turnover).toFixed(3) + ' each rebalance</td></tr>' +
      '<tr><td>gross edge / rebalance</td><td class="n"></td>' +
        '<td class="n ' + sign(per(t.gross)) + '">' + pct(per(t.gross), 3) + '</td></tr>' +
      '<tr class="net"><td><b>net edge / rebalance</b></td><td class="n"></td>' +
        '<td class="n ' + sign(t.net_per_period) + '"><b>' + pct(t.net_per_period, 3) +
        '</b></td></tr></tbody></table>';
  }

  function segments(seg) {
    if (!seg || !seg.in_sample || !seg.out_of_sample) {
      return '<div class="bt-note-inline">This run was not split. The lookback, the rebalance ' +
        'gap and the decay were all chosen by someone who could see all of this data, so ' +
        'part of the result is that choosing. Give <span class="mono">split</span> a date and the ' +
        'run is reported twice: the half you chose on, and the half you never looked at.</div>';
    }
    var a = seg.in_sample, b = seg.out_of_sample;
    var row = function (k, va, vb, dp) {
      return '<tr><td>' + k + '</td><td class="n ' + sign(va) + '">' + pct(va, dp) +
        '</td><td class="n ' + sign(vb) + '">' + pct(vb, dp) + '</td></tr>';
    };
    return '<table class="t bt-seg"><thead><tr><th></th><th class="n">in sample</th>' +
      '<th class="n oos">out of sample</th></tr></thead><tbody>' +
      '<tr><td>rebalances</td><td class="n">' + a.periods + '</td><td class="n">' + b.periods +
        '</td></tr>' +
      row('net', a.net, b.net, 2) +
      row('net per rebalance', a.net_per_period, b.net_per_period, 3) +
      row('gross per rebalance', a.periods ? a.gross / a.periods : 0,
          b.periods ? b.gross / b.periods : 0, 3) +
      '<tr><td>hit rate</td><td class="n">' + (a.hit_rate * 100).toFixed(0) + '%</td>' +
        '<td class="n">' + (b.hit_rate * 100).toFixed(0) + '%</td></tr>' +
      '</tbody></table><div class="bt-note-inline">Split at <b>' + seg.split.slice(0, 10) +
      '</b>. The settings were chosen while looking at the left column, so the right one is the ' +
      'only part that was never allowed to argue back.' +
      (seg.decay_vs_in_sample != null
        ? ' Out of sample keeps <b class="' + (seg.decay_vs_in_sample >= 0 ? 'up' : 'down') + '">' +
          (100 + seg.decay_vs_in_sample * 100).toFixed(0) + '%</b> of the in-sample edge per period.'
        : '') + '</div>';
  }

  function monthGrid(ms, ps) {
    if (!ms.length) return '';
    var m = Math.max.apply(null, ms.map(function (r) { return Math.abs(r.net); })) || 1e-9;
    return '<div class="bt-months">' + ms.map(function (r) {
      var a = Math.min(0.85, 0.12 + Math.abs(r.net) / m * 0.73);
      var first = ps.findIndex(function (p) { return p.as_of.slice(0, 7) === r.key; });
      return '<button type="button" class="mo ' + sign(r.net) + '" style="--a:' + a.toFixed(2) +
        '" data-i="' + first + '" title="' + r.key + '  ' + pct(r.net, 2) +
        ' · open the first rebalance of this month"><span class="k">' + r.key.slice(2) +
        '</span><span class="v">' + (r.net * 100).toFixed(1) + '</span></button>';
    }).join('') + '</div>';
  }

  function yearTable(ys) {
    if (!ys.length) return '';
    return '<table class="t"><thead><tr><th>year</th><th class="n">periods</th>' +
      '<th class="n">gross</th><th class="n">cost</th><th class="n">net</th>' +
      '<th class="n">hit rate</th></tr></thead><tbody>' +
      ys.map(function (r) {
        return '<tr><td class="mono">' + r.key + '</td><td class="n">' + r.n + '</td>' +
          '<td class="n ' + sign(r.gross) + '">' + pct(r.gross, 2) + '</td>' +
          '<td class="n down">' + pct(-r.cost, 2) + '</td>' +
          '<td class="n ' + sign(r.net) + '"><b>' + pct(r.net, 2) + '</b></td>' +
          '<td class="n">' + Math.round(r.win / r.n * 100) + '%</td></tr>';
      }).join('') + '</tbody></table>';
  }

  function drillPanel() {
    if (!DETAIL) {
      return panel('Selected rebalance', '<span class="faint">nothing picked</span>',
        '<div class="bt-empty">Click a bar above and this shows that one rebalance: which names ' +
        'were held, what each of them did, and what had to be bought or sold to get there.' +
        '</div>');
    }
    var d = DETAIL, p = d.period || {};
    var head = '<span class="mono">' + d.as_of.slice(0, 10) + ' → ' + d.next_as_of.slice(0, 10) +
      '</span> · priced ' + d.priced_from.slice(0, 10) + ' → ' + d.priced_to.slice(0, 10) +
      (d.in_sample == null ? '' : ' · <span class="tagx ' + (d.in_sample ? 'is' : 'oos') + '">' +
        (d.in_sample ? 'in sample' : 'out of sample') + '</span>');
    var sums = '<div class="bt-sum wide">' +
      '<div class="row"><span>gross</span><b class="' + sign(p.gross) + '">' + pct(p.gross, 3) +
        '</b></div>' +
      '<div class="row"><span>cost of trading</span><b class="down">' +
        pct(-((p.fees || 0) + (p.slippage || 0)), 3) + '</b></div>' +
      '<div class="row net"><span>net</span><b class="' + sign(p.net) + '">' + pct(p.net, 3) +
        '</b></div>' +
      '<div class="row"><span>turnover</span><b>' + (p.turnover || 0).toFixed(3) + '</b></div>' +
      '</div>';
    var moves = (d.opened.length || d.closed.length)
      ? '<div class="bt-note-inline">' +
        (d.opened.length ? 'opened <b>' + esc(d.opened.join(', ')) + '</b>. ' : '') +
        (d.closed.length ? 'closed <b>' + esc(d.closed.join(', ')) + '</b>.' : '') + '</div>' : '';
    var rows = d.holdings.filter(function (h) { return h.weight || h.traded; }).map(function (h) {
      return '<tr><td class="mono">' + esc(h.symbol) + '</td>' +
        '<td class="n">' + h.weight.toFixed(4) + '</td>' +
        '<td class="n faint">' + h.was.toFixed(4) + '</td>' +
        '<td class="n ' + sign(h.traded) + '">' + (h.traded >= 0 ? '+' : '') +
          h.traded.toFixed(4) + '</td>' +
        '<td class="n ' + sign(h.return) + '">' + pct(h.return, 2) + '</td>' +
        '<td class="n ' + sign(h.contribution) + '"><b>' + pct(h.contribution, 3) +
          '</b></td></tr>';
    }).join('');
    return panel('Selected rebalance', head, sums + moves +
      '<div class="tablewrap bt-table"><table class="t"><thead><tr><th>symbol</th>' +
      '<th class="n">weight</th><th class="n">prev<i>last rebalance</i></th>' +
      '<th class="n">traded<i>to get there</i></th>' +
      '<th class="n">return</th><th class="n">contribution<i>weight × return</i></th>' +
      '</tr></thead><tbody>' +
      rows + '</tbody></table></div>');
  }

  // ------------------------------------------------------------------ render
  function inView() {
    var all = (CURRENT && CURRENT.periods) || [];
    var seg = (CURRENT && CURRENT.segments) || {};
    if (VIEW === 'is' && seg.split) {
      return all.filter(function (p) { return p.as_of < seg.split; });
    }
    if (VIEW === 'oos' && seg.split) {
      return all.filter(function (p) { return p.as_of >= seg.split; });
    }
    return all;
  }

  function visible() {
    var ps = inView();
    if (!RANGE) return ps;
    return ps.slice(RANGE[0], RANGE[1] + 1);
  }

  // Totals for whatever is on screen. A dragged range is a different question from
  // the whole run, so it gets its own numbers rather than borrowing the run's.
  function totalsOf(ps) {
    if (!ps.length) return { periods: 0 };
    var net = ps.map(function (p) { return p.net; });
    var eq = 1;
    net.forEach(function (n) { eq *= 1 + n; });
    var sum = function (f) { return ps.reduce(function (a, p) { return a + f(p); }, 0); };
    return {
      periods: ps.length,
      gross: sum(function (p) { return p.gross; }),
      fees: sum(function (p) { return p.fees; }),
      slippage: sum(function (p) { return p.slippage; }),
      net: net.reduce(function (a, b) { return a + b; }, 0),
      turnover: sum(function (p) { return p.turnover; }),
      net_per_period: net.reduce(function (a, b) { return a + b; }, 0) / net.length,
      hit_rate: net.filter(function (n) { return n > 0; }).length / net.length,
      equity: eq,
    };
  }

  function renderDetail() {
    if (!CURRENT) {
      var who = (BOOK.filter(function (x) { return x.alpha === PICKED; })[0] || {}).name;
      return PICKED
        ? '<div class="bt-empty"><b>' + esc(who || niceName(PICKED)) + '</b> has never been run. ' +
          'Its result table is drawn dashed on the graph, where the numbers will ' +
          'land. Fill in the run on the right.</div>'
        : '<div class="bt-empty">pick a run on the left</div>';
    }
    var seg = CURRENT.segments || {}, c = CURRENT.conditions || {};
    var ps = visible();
    var t = RANGE
      ? totalsOf(ps)
      : ((VIEW === 'is' ? seg.in_sample : VIEW === 'oos' ? seg.out_of_sample : CURRENT.totals)
         || CURRENT.totals || {});

    var mine = LIST.filter(function (b) { return !c.alpha || b.alpha === c.alpha; });
    var picker = mine.length > 1
      ? '<select class="runpick" id="bt-run">' + mine.map(function (b) {
          return '<option value="' + b.run_id + '"' +
            (String(b.run_id) === String(CURRENT.run_id) ? ' selected' : '') + '>' +
            String(b.from_date).slice(0, 10) + ' → ' + String(b.to_date).slice(0, 10) +
            ' · ' + b.rebalance + ' · seed ' + b.seed + ' · ' + pct(b.net) + '</option>';
        }).join('') + '</select>'
      : '';
    var head = '<div class="bt-head"><h3>' +
      esc(niceName(c.alpha) || 'run') +
      (CURRENT.live ? ' <span class="bt-live">live</span>' : '') + '</h3>' +
      '<span class="faint mono">' + String(CURRENT.from || '').slice(0, 10) + ' → ' +
      String(CURRENT.to || '').slice(0, 10) + ' · every ' + CURRENT.rebalance +
      ' · seed ' + CURRENT.seed + (c.decay ? ' · decay ' + c.decay : '') +
      (c.universe ? ' · ' + esc(c.universe) : '') + '</span>' + picker +
      '<button type="button" class="chip" id="bt-run-new">re-run</button>' +
      (seg.split ? '<div class="bt-tabs">' + ['all', 'is', 'oos'].map(function (v) {
        return '<button type="button" data-v="' + v + '" class="' + (VIEW === v ? 'on' : '') +
          '">' + { all: 'whole run', is: 'in sample', oos: 'out of sample' }[v] + '</button>';
      }).join('') + '</div>' : '') + '</div>';

    if (!t.periods || ps.length < 1) {
      return head + '<div class="bt-empty">this run priced no period</div>';
    }

    var eq = equity(ps), dd = drawdown(eq);
    var win = Math.min(12, Math.max(4, Math.round(ps.length / 6)));
    var roll = rollingSharpe(ps, win, CURRENT.rebalance);
    var isCount = (VIEW === 'all' && seg.split)
      ? (CURRENT.periods || []).filter(function (p) { return p.as_of < seg.split; }).length : null;
    var markAt = DETAIL ? ps.findIndex(function (p) { return p.as_of === DETAIL.as_of; }) : -1;
    var worst = Math.min.apply(null, dd);

    var tiles = '<div class="htiles bt-tiles">' +
      '<div class="tile"><div class="k">net<i>after fees and slippage, compounded' +
        (t.net_sum != null ? ' · ' + pct(t.net_sum) + ' summed' : '') + '</i></div>' +
        '<div class="v ' + sign(t.net) + '">' + pct(t.net) + '</div></div>' +
      '<div class="tile"><div class="k">net / rebalance<i>the edge per decision</i></div>' +
        '<div class="v">' + pct(t.net_per_period, 3) + '</div></div>' +
      '<div class="tile"><div class="k">max drawdown<i>worst fall from a high</i></div>' +
        '<div class="v down">' + pct(worst, 2) + '</div></div>' +
      '<div class="tile"><div class="k">hit rate<i>rebalances that made money</i></div>' +
        '<div class="v">' + (t.hit_rate * 100).toFixed(0) + '%</div></div></div>';

    var band = RANGE
      ? '<div class="rangebar">showing <b>' + ps.length + '</b> rebalances · <b>' +
        ps[0].as_of.slice(0, 10) + '</b> → <b>' + ps[ps.length - 1].as_of.slice(0, 10) +
        '</b><button type="button" class="chip" id="bt-whole">whole period</button></div>'
      : '';
    return head + band + tiles +
      panel('In sample vs out of sample',
            '<span class="faint">IS is the half the settings were chosen on · OOS is the half ' +
            'they were not</span>', segments(seg)) +
      panel('Equity curve',
            '<span class="faint">what 1.00 turned into, and how far under its own high it ' +
            'went</span> · ends at <b class="' + sign(eq[eq.length - 1] - 1) + '">' +
            eq[eq.length - 1].toFixed(4) + '</b> · MDD <b class="down">' + pct(worst, 2) +
            '</b>' + (isCount ? ' · <span class="faint">shaded = in sample</span>' : ''),
            chartBox(
              lineSvg(eq, 150, { cls: eq[eq.length - 1] >= 1 ? 'up' : 'down', zero: 1,
                                 isCount: isCount, markAt: markAt >= 0 ? markAt + 1 : -1 }) +
              '<p class="bt-cap under">underwater: below the previous high</p>' +
              lineSvg(dd, 74, { cls: 'down', zero: 0, hi: 0, fill: true, isCount: isCount }),
              { values: eq, fmt: 'eq', offset: 1, clickable: true,
                labels: ['start'].concat(ps.map(function (p) { return p.as_of.slice(0, 10); })),
                extra: { label: 'drawdown', values: dd } })) +
      '<div class="bt-two">' +
        panel('Rolling Sharpe', '<span class="faint">return per unit of risk · ' + win +
              ' rebalances at a time, annualised from the ' + CURRENT.rebalance + ' gap</span>',
              line(roll, 118, { cls: 'up', zero: 0, isCount: isCount, fmt: 'raw',
                                clickable: true, labels: ps.map(function (p) {
                                  return p.as_of.slice(0, 10); }) })) +
        panel('Distribution of returns', '<span class="faint">how often each size of result ' +
              'happened · ' + ps.length + ' rebalances</span>', histogram(ps, 118)) +
      '</div>' +
      panel('Net per rebalance', '<span class="faint">after fees and slippage · click a bar to ' +
            'open it, drag across to narrow the whole report</span>',
            bars(ps.map(function (p) { return { v: p.net, label: p.as_of.slice(0, 10) }; }), 96,
                 { clickable: !CURRENT.live, markAt: markAt, isCount: isCount })) +
      drillPanel() +
      panel('Monthly returns', '<span class="faint">per cent, compounded within each month</span>',
            monthGrid(byMonth(ps), ps)) +
      '<div class="bt-two">' +
        panel('Year by year', '', yearTable(byYear(ps))) +
        panel('Cost model', '<span class="faint">charged on turnover, not on what is held</span>',
              costModel(t, c)) +
      '</div>' +
      ((CURRENT.failures || []).length
        ? '<div class="bt-notes bad">' + CURRENT.failures.slice(0, 6).map(function (n) {
            return '<div>' + esc(n) + '</div>'; }).join('') + '</div>' : '') +
      ((CURRENT.notes || []).length
        ? '<div class="bt-notes">' + CURRENT.notes.slice(0, 6).map(function (n) {
            return '<div>' + esc(n) + '</div>'; }).join('') + '</div>' : '');
  }

  function renderItem(b) {
    var on = CURRENT && String(CURRENT.run_id) === String(b.run_id);
    var who = niceName(b.alpha);
    return '<button type="button" class="bt-item' + (on ? ' on' : '') +
      (PICKED && b.alpha === PICKED ? ' kin' : '') + '" data-id="' + b.run_id + '">' +
      '<span class="w">' + (who ? '<b class="who">' + esc(who) + '</b> ' : '') +
        String(b.from_date).slice(0, 10) + ' → ' + String(b.to_date).slice(0, 10) + '</span>' +
      '<span class="m faint">' + b.rebalance + ' · seed ' + b.seed + ' · ' + b.status + '</span>' +
      '<span class="n ' + sign(b.net) + '">' + pct(b.net) + '</span></button>';
  }

  // A sparkline of the last run's equity. Twelve lines, no library, and it is the
  // same series the big chart draws -- just thinned.
  function spark(points, w, h) {
    if (!points || points.length < 2) return '<div class="sp"></div>';
    var lo = Math.min.apply(null, points), hi = Math.max.apply(null, points);
    if (hi - lo < 1e-12) hi = lo + 1e-12;
    var d = points.map(function (v, i) {
      return (i ? 'L' : 'M') + (i / (points.length - 1) * w).toFixed(1) + ' ' +
        (h - (v - lo) / (hi - lo) * h).toFixed(1);
    }).join(' ');
    var up = points[points.length - 1] >= 1;
    return '<svg class="sp" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      '<path class="bt-line ' + (up ? 'up' : 'down') + '" d="' + d + '"></path></svg>';
  }

  function renderRunsHead() {
    return '<div class="kicker">Runs' +
      (PICKED ? ' <span class="faint">· showing the newest of ' +
        esc(niceName(PICKED)) + '</span>' : '') + '</div>';
  }

  // How the book hangs together. Correlation is the one that matters: two rules
  // that made money on the same days are one rule twice.
  function renderStats() {
    var st = STATS || {};
    var head = '<div class="htiles">' +
      '<div class="tile"><div class="k">alphas' +
        (st.blends ? '<i>+ ' + st.blends + ' blended</i>' : '') + '</div><div class="v">' +
        (st.alphas || 0) + '</div></div>' +
      '<div class="tile"><div class="k">backtests</div><div class="v">' + (st.runs || 0) +
        '</div></div>' +
      '<div class="tile"><div class="k">best OOS<i>out of sample</i></div><div class="v ' +
        sign(st.best_out_of_sample) + '">' +
        (st.best_out_of_sample == null ? '—' : pct(st.best_out_of_sample)) + '</div></div>' +
      '<div class="tile"><div class="k">wired now</div><div class="v mono sm">' +
        esc(niceName(WIRED) || '—') + '</div></div>' +
      '</div>';
    return head +
      '<div class="kicker sub">Correlation between alphas' +
      '<button type="button" class="info" id="corr-why" title="what this means">i</button>' +
      '</div>' +
      '<div class="bt-note-inline" id="corr-note" hidden>Correlation of net per rebalance, on ' +
      'the dates two alphas share. Near <b>0</b> is a different bet. <b class="down">Red</b> is ' +
      'the same bet twice: the second one only adds its own costs.</div>' +
      heatmap(st);
  }

  // A matrix, not a list of pairs: pairs grow as n², and by four alphas a list is
  // already unreadable. Colour carries the number so the eye finds the hot cells.
  function heatmap(st) {
    var pairs = st.correlation || [];
    if (!pairs.length) {
      return '<div class="bt-note-inline">' + esc(st.note || 'nothing to compare yet') + '</div>';
    }
    var names = [];
    pairs.forEach(function (c) {
      if (names.indexOf(c.a) < 0) names.push(c.a);
      if (names.indexOf(c.b) < 0) names.push(c.b);
    });
    names.sort();
    var at = {};
    pairs.forEach(function (c) { at[c.a + '|' + c.b] = c; at[c.b + '|' + c.a] = c; });
    var shortName = niceName;
    var tag = function (n) { return shortName(n).slice(0, 3); };

    var cells = '<div class="hm" style="--n:' + names.length + '">';
    cells += '<i class="hm-c corner"></i>';
    names.forEach(function (n) {
      cells += '<i class="hm-h" title="' + esc(n) + '">' + esc(tag(n)) + '</i>';
    });
    names.forEach(function (a) {
      cells += '<i class="hm-h row" title="' + esc(a) + '">' + esc(tag(a)) + '</i>';
      names.forEach(function (b) {
        if (a === b) { cells += '<i class="hm-c self">·</i>'; return; }
        var c = at[a + '|' + b];
        var r = c && c.r;
        if (r == null) { cells += '<i class="hm-c none" title="too few shared dates">—</i>'; return; }
        // green when the bets differ, red when they are the same bet twice
        var mag = Math.min(1, Math.abs(r));
        var hue = r >= 0 ? '193,80,63' : '162,230,93';
        cells += '<i class="hm-c" style="background:rgba(' + hue + ',' +
          (0.10 + mag * 0.62).toFixed(2) + ')" title="' + esc(shortName(a)) + ' vs ' +
          esc(shortName(b)) + '  r = ' + r.toFixed(2) + '  over ' + c.n + ' shared dates">' +
          r.toFixed(1).replace('0.', '.') + '</i>';
      });
    });
    cells += '</div>';
    return cells;
  }

  function renderBook() {
    if (!BOOK.length) {
      return '<div class="bt-empty">Nothing backtested yet. An alpha joins the book the ' +
        'moment it produces a result.</div>';
    }
    var editing = window.AlphaEdit && window.AlphaEdit.isEditing();
    return BOOK.map(function (a) {
      var score = a.out_of_sample != null ? a.out_of_sample : a.last_net;
      var badge = score == null ? '<span class="sh faint">no result</span>'
        : '<span class="sh ' + sign(score) + '">' +
          (a.out_of_sample != null ? 'OOS ' : 'net ') + pct(score) + '</span>';
      var c = a.conditions || {};
      return '<button type="button" class="sitem' + (PICKED === a.alpha ? ' on' : '') +
        (a.wired ? ' wired' : '') + '" data-alpha="' + esc(a.alpha) + '" title="' +
        esc(a.alpha) + '">' +
        '<span class="r1"><span class="code">' + esc(a.name || a.alpha) + '</span>' +
        badge + '</span>' +
        '<span class="nm">' + a.runs + ' run' + (a.runs === 1 ? '' : 's') +
        (c.rebalance ? ' · ' + esc(c.rebalance) : '') +
        (c.decay ? ' · decay ' + c.decay : '') +
        (wiring(a) ? ' · <span class="mono faint">' + wiring(a) + '</span>' : '') + '</span>' +
        spark(a.spark, 190, 22) +
        (String(a.alpha).indexOf('+') < 0
          ? '<span class="edrow" data-edit="' + esc(a.alpha) + '" title="change this alpha">' +
            'edit</span>'
          : '') + '</button>';
    }).join('') +
      (editing
        ? '<button type="button" class="sitem add" id="book-add">＋ add an alpha</button>'
        : '');
  }

  // what feeds this strategy and where its result lands. A blend reads several
  // weights tables into one PnL table, which is the whole shape of it in one line.
  function wiring(a) {
    var from = a.reads_weights && a.reads_weights.length ? a.reads_weights
      : (a.writes ? [a.writes] : []);
    if (!from.length) return '';
    return from.map(esc).join(' + ') + (a.pnl ? ' → ' + esc(a.pnl) : '');
  }

  function renderList() {
    if (CURRENT && CURRENT.live) {
      var t = CURRENT.totals || {};
      return '<button type="button" class="bt-item on">' +
        '<span class="w">' + String(CURRENT.from).slice(0, 10) + ' → ' +
          String(CURRENT.to).slice(0, 10) + '</span>' +
        '<span class="m faint">' + CURRENT.rebalance + ' · running</span>' +
        '<span class="n ' + sign(t.net) + '">' + (t.periods ? pct(t.net) : '—') + '</span>' +
        '</button>' + LIST.filter(function (b) {
          return String(b.run_id) !== String(CURRENT.run_id);
        }).map(renderItem).join('');
    }
    var runs = LIST;
    if (!runs.length) {
      return '<div class="bt-empty">No backtest yet.<br><span class="faint">' +
        'Run <span class="mono">qanat backtest --from … --to …</span>, or ask your agent to.' +
        '</span></div>';
    }
    return runs.map(renderItem).join('');
  }

  function paintHeader() {
    var title = el('bt-title'), sum = el('bt-inline-sum');
    if (!title) return;
    if (!CURRENT) {
      title.textContent = LIST.length ? 'pick a run' : 'no replay yet';
      sum.textContent = '—';
      return;
    }
    var seg = CURRENT.segments || {};
    var t = RANGE ? totalsOf(visible()) : (CURRENT.totals || {});
    title.textContent = String(CURRENT.from || '').slice(0, 10) + ' → ' +
      String(CURRENT.to || '').slice(0, 10) + '  every ' + CURRENT.rebalance;
    if (!t.periods) { sum.textContent = 'no priced period'; return; }
    if (RANGE) {
      sum.innerHTML = '<span class="faint">selected</span> net <b class="' + sign(t.net) + '">' +
        pct(t.net) + '</b> · ' + t.periods + ' rebalances · hit rate ' +
        (t.hit_rate * 100).toFixed(0) + '%';
      return;
    }
    sum.innerHTML = seg.out_of_sample
      ? 'OOS <b class="' + sign(seg.out_of_sample.net) + '">' + pct(seg.out_of_sample.net) +
        '</b> · IS <span class="faint">' + pct(seg.in_sample.net) + '</span> · ' +
        t.periods + ' rebalances'
      : 'net <b class="' + sign(t.net) + '">' + pct(t.net) + '</b> · ' + t.periods +
        ' rebalances · hit rate ' + (t.hit_rate * 100).toFixed(0) + '%';
  }

  function paint() {
    var main = el('bt-main') || document.querySelector('.bt-main');
    var keep = main ? main.scrollTop : 0;
    var stats = el('book-stats');
    if (stats) stats.innerHTML = renderStats();
    var book = el('book');
    if (book) book.innerHTML = renderBook();
    el('bt-detail').innerHTML = renderDetail();

    var picker = el('bt-run');
    if (picker) picker.onchange = function () { select(picker.value); };
    var whole = el('bt-whole');
    if (whole) whole.onclick = function () { RANGE = null; DETAIL = null; paint(); };
    var runNew = el('bt-run-new');
    // Same settings, same seed, same numbers -- that is the guarantee the engine
    // makes, so a re-run that changes nothing is a slow way to get the answer you
    // already have. The form opens on this run's settings and says so.
    if (runNew) runNew.onclick = function () { openRunner(CURRENT); };
    if (main) main.scrollTop = keep;
    var add = el('book-add');
    if (add) add.onclick = function () { window.AlphaEdit.open(null); };
    var why = el('corr-why');
    if (why) {
      why.onclick = function () {
        var note = el('corr-note');
        note.hidden = !note.hidden;
        why.classList.toggle('on', !note.hidden);
      };
    }
    // `edit` opens the alpha itself; the rest of the card picks it. One card, two
    // things you can mean by clicking it, and the label says which is which.
    Array.prototype.forEach.call(document.querySelectorAll('[data-edit]'), function (b) {
      b.onclick = function (ev) {
        ev.stopPropagation();
        var a = b.getAttribute('data-edit');
        var r = BOOK.filter(function (x) { return x.alpha === a; })[0] || {};
        if (r.reads_weights && r.reads_weights.length > 1) return;   // a blend has no step
        window.AlphaEdit.open({
          id: a, name: r.name || a, writes: r.writes,
          reads: (r.reads || [])[0] || (r.conditions || {}).reads,
          universe: r.universe, rebalance: r.rebalance, decay: r.decay,
          conditions: r.conditions || {},
        });
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-alpha]'), function (b) {
      b.onclick = function () {
        var a = b.getAttribute('data-alpha');
        // Picking a strategy opens its newest run over the whole period. It does not
        // hide the other runs -- they are the point of keeping a book. Clicking the
        // same one again lets go, the way clicking a table twice does.
        if (PICKED === a) {
          PICKED = null;
          if (window.QANAT) window.QANAT.focusAlpha(null);
          paint();
          return;
        }
        PICKED = a;
        var row = BOOK.filter(function (x) { return x.alpha === a; })[0];
        // `a` is the alphaset key, which is what decides *which* result table is
        // this one's -- an alpha inside three blends must not light all four
        if (window.QANAT) window.QANAT.focusAlpha(row ? row.dag : null, a);
        RANGE = null;
        DETAIL = null;
        VIEW = 'all';
        var latest = LIST.filter(function (r) { return r.alpha === a; })[0];
        if (latest) {
          // it has been run: show what it earned, and open its result table beside
          // it: the numbers in the report and the rows they came from
          select(latest.run_id);
          openBacktests();
          if (row && row.pnl && window.QANAT) window.QANAT.selectTable(row.pnl);
        } else {
          // It has never been run. Leaving the last alphaset's report and its table
          // on screen under a different card is the worst of both: it reads as this
          // one's numbers. Clear them, draw where the result will land, and offer
          // the run.
          CURRENT = null;
          if (window.QANAT) window.QANAT.closeDetail();
          paint();
          openRunner();
        }
      };
    });
    Array.prototype.forEach.call(el('bt-detail').querySelectorAll('.bt-tabs button'), function (b) {
      b.onclick = function () { VIEW = b.getAttribute('data-v'); DETAIL = null; paint(); };
    });
    // the rects do not take clicks any more: the whole chart handles click and drag,
    // so a drag that starts on a bar behaves the same as one that starts between them
    Array.prototype.forEach.call(el('bt-detail').querySelectorAll('[data-chart]'), wireChart);
    Array.prototype.forEach.call(el('bt-detail').querySelectorAll('.mo[data-i]'), function (m) {
      m.onclick = function () { openPeriod(parseInt(m.getAttribute('data-i'), 10)); };
    });
    paintHeader();
  }

  // Hovering any chart says what is under the pointer, and clicking it opens that
  // rebalance -- so the equity line, the drawdown and the bars are all one control
  // rather than one clickable chart and four pictures.
  function wireChart(box) {
    var c = CHARTS[box.getAttribute('data-chart')];
    if (!c || !c.values.length) return;
    var guide = box.querySelector('.guide'), tip = box.querySelector('.ctip');

    function idxAt(e) {
      var r = box.getBoundingClientRect();
      var f = Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width)));
      return Math.round(f * (c.values.length - 1));
    }
    var drag = null, sel = null;

    function paintSel(a, b) {
      if (!sel) {
        sel = document.createElement('span');
        sel.className = 'sel';
        box.appendChild(sel);
      }
      var lo = Math.min(a, b) / Math.max(1, c.values.length) * 100;
      var hi = (Math.max(a, b) + 1) / Math.max(1, c.values.length) * 100;
      sel.style.left = lo + '%';
      sel.style.width = (hi - lo) + '%';
    }

    if (c.clickable) {
      box.addEventListener('pointerdown', function (e) {
        drag = { from: idxAt(e), to: idxAt(e), x0: e.clientX };
        box.setPointerCapture(e.pointerId);
      });
      box.addEventListener('pointerup', function (e) {
        if (!drag) return;
        var from = drag.from, to = idxAt(e);
        drag = null;
        if (sel) { sel.remove(); sel = null; }
        // a span on any chart narrows the report; a tap opens that one rebalance
        if (Math.abs(to - from) >= 1) {
          setRange(Math.min(from, to) - c.offset, Math.max(from, to) - c.offset);
        } else {
          openPeriod(from - c.offset);
        }
      });
      box.addEventListener('pointercancel', function () {
        drag = null;
        if (sel) { sel.remove(); sel = null; }
      });
    }

    box.addEventListener('pointermove', function (e) {
      var i = idxAt(e), v = c.values[i];
      if (drag) { drag.to = i; paintSel(drag.from, i); }
      if (v == null) { box.classList.remove('hot'); return; }
      box.classList.add('hot');
      var pos = (i / Math.max(1, c.values.length - 1)) * 100;
      guide.style.left = pos + '%';
      tip.style.left = pos + '%';
      var label = c.labels[Math.max(0, i - c.offset)] || '';
      var main = c.fmt === 'raw' ? v.toFixed(2)
        : (c.fmt === 'eq' ? v.toFixed(4) : pct(v, 3));
      var more = c.extra && c.extra.values[i] != null
        ? ' · <span class="down">' + esc(c.extra.label) + ' ' +
          pct(c.extra.values[i], 2) + '</span>' : '';
      tip.innerHTML = (label ? '<span class="faint">' + esc(label) + '</span> ' : '') +
        main + more;
    });
    box.addEventListener('pointerleave', function () { box.classList.remove('hot'); });
    if (c.clickable) box.style.cursor = 'crosshair';
  }

  // A dragged span. Everything above and below narrows to it, because "how did it
  // do in that stretch" is a different question from "how did it do overall" and
  // deserves its own numbers rather than a highlight on someone else's.
  function setRange(a, b) {
    var ps = inView();
    a = Math.max(0, Math.min(ps.length - 1, a));
    b = Math.max(0, Math.min(ps.length - 1, b));
    RANGE = (b - a + 1) >= ps.length ? null : [a, b];
    DETAIL = null;
    paint();
  }

  // A point on the curve, opened. The server recomputes it from what the run
  // recorded, so this panel and the chart above it cannot tell different stories.
  async function openPeriod(i) {
    if (!CURRENT || CURRENT.live) return;
    var p = visible()[i];
    if (!p) return;
    if (DETAIL && DETAIL.as_of === p.as_of) { DETAIL = null; paint(); return; }
    try {
      DETAIL = await api('/api/backtests/' + CURRENT.run_id + '/periods/' +
                         encodeURIComponent(p.as_of));
    } catch (e) {
      DETAIL = null;
      el('bt-detail').insertAdjacentHTML('afterbegin',
        '<div class="bt-empty bad">' + esc(e.message) + '</div>');
      return;
    }
    paint();
  }

  async function select(runId) {
    try {
      CURRENT = await api('/api/backtests/' + runId);
      DETAIL = null;      // the whole period, not a single rebalance
      VIEW = 'all';
      RANGE = null;
    } catch (e) {
      CURRENT = null;
      el('bt-detail').innerHTML = '<div class="bt-empty bad">' + esc(e.message) + '</div>';
      return;
    }
    paint();
  }

  async function refresh() {
    var fresh;
    try {
      fresh = await api('/api/backtests?limit=40');
    } catch (e) {
      el('bt-list').innerHTML = '<div class="bt-empty bad">' + esc(e.message) + '</div>';
      return;
    }
    // Repaint only when something actually changed. A poll that redraws every four
    // seconds throws away the scroll position and makes a chart hard to click --
    // the panel is for reading, and reading takes longer than the poll.
    try {
      var book = await api('/api/alphas');
      BOOK = book.alphas || [];
      WIRED = book.wired;
      STATS = book.stats || {};
    } catch (e) { /* the runs still stand without the book */ }
    var sig = fresh.map(function (b) {
      return b.run_id + ':' + b.status + ':' + b.periods + ':' + b.net;
    }).join('|') + '#' + BOOK.map(function (a) {
      return a.alpha + ':' + a.runs + ':' + a.wired;
    }).join('|');
    LIST = fresh;
    if (CURRENT && !fresh.some(function (r) {
      return String(r.run_id) === String(CURRENT.run_id);
    })) CURRENT = null;
    // Only fall back to the newest run when nothing is picked. If an alphaset with
    // no result is picked, showing another one's numbers under its card is exactly
    // the confusion this is meant to avoid.
    var pickedHasRun = !PICKED || LIST.some(function (r) { return r.alpha === PICKED; });
    if (!CURRENT && LIST.length && pickedHasRun) {
      LIST_SIG = sig;
      await select(LIST[0].run_id);
      return;
    }
    if (sig === LIST_SIG) return;
    LIST_SIG = sig;
    paint();
  }

  function markOpen(open) {
    document.body.classList.toggle('results-open', !!open);
  }

  function refit() {
    // the graph shares the column with this panel, so opening it makes the graph
    // shorter -- and a graph that does not refit is a graph half off the screen
    if (window.QANAT && window.QANAT.dag) window.QANAT.dag.recentre();
  }

  function openBacktests() {
    PANEL = PANEL || el('btwrap');
    PANEL.setAttribute('data-open', '1');
    markOpen(true);
    PANEL.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    setTimeout(refit, 60);
    refresh();
    TIMER = TIMER || setInterval(refresh, 4000);
  }

  function closeBacktests() {
    PANEL = PANEL || el('btwrap');
    PANEL.setAttribute('data-open', '0');
    markOpen(false);
    if (TIMER) { clearInterval(TIMER); TIMER = null; }
    setTimeout(refit, 60);
  }

  function toggleBacktests() {
    PANEL = PANEL || el('btwrap');
    if (PANEL.getAttribute('data-open') === '1') closeBacktests(); else openBacktests();
  }

  function showNewest() {
    CURRENT = null; DETAIL = null; VIEW = 'all'; LIST_SIG = null;
    openBacktests();
  }

  function showLive(p) {
    var c = p.conditions || {};
    CURRENT = {
      run_id: p.run_id, live: true,
      from: c.from || '', to: c.to || '', rebalance: c.rebalance || '',
      seed: c.seed, conditions: c, segments: p.segments || {},
      totals: p.totals || {}, periods: p.periods || [], notes: [], failures: [],
    };
    DETAIL = null;
    PANEL = PANEL || el('btwrap');
    if (PANEL.getAttribute('data-open') !== '1') {
      PANEL.setAttribute('data-open', '1');
      markOpen(true);
      setTimeout(refit, 60);
    }
    paint();
  }

  // ------------------------------------------------------------- run it here
  // Every one of these changes the answer, so the form asks for all of them rather
  // than picking quietly. It is the same list the agent is told to ask for.
  async function openRunner(from) {
    var box = el('runner');
    box.hidden = false;
    var body = el('runner-body');
    body.innerHTML = '<div class="bt-empty">reading what this project can be asked…</div>';
    var c;
    try {
      c = await api('/api/backtest/conditions');
    } catch (e) {
      body.innerHTML = '<div class="warnbox">' + esc(e.message) + '</div>';
      return;
    }
    var d = c.defaults || {}, span = c.data || {};
    var opt = function (list, sel, blank) {
      return (blank ? '<option value="">' + blank + '</option>' : '') + list.map(function (x) {
        return '<option value="' + esc(x.id) + '"' + (x.id === sel ? ' selected' : '') + '>' +
          esc(x.id) + '</option>';
      }).join('');
    };
    // Several alphas can be priced as one portfolio, so this is a list of checks
    // rather than a dropdown. Each one keeps its own weights table; the run holds
    // the sum of them, and the share box says how the money is split.
    // re-running opens on the settings that produced the run you are looking at
    var fc = (from && from.conditions) || {};
    var chosen = _asked(fc.alpha || PICKED);
    if (!chosen.length && c.alphas[0]) chosen = [c.alphas[0].id];
    var alphaPicker = '<div class="apick">' + c.alphas.map(function (a) {
      var on = chosen.indexOf(a.id) >= 0;
      return '<label class="apick-row' + (on ? ' on' : '') + '">' +
        '<input type="checkbox" class="f-alpha" value="' + esc(a.id) + '"' +
        (on ? ' checked' : '') + '>' +
        '<span class="apick-name">' + esc(a.id) + '</span>' +
        '<input type="number" class="f-share" min="0" step="1" value="1" title="share">' +
        '</label>';
    }).join('') + '</div>';
    body.innerHTML = rerunNote(from, chosen) +
      '<div class="rform">' +
      row('alpha', alphaPicker,
          'which pipeline to replay · tick more than one to price them together as a ' +
          'single book · the number beside each is its share of the money') +
      row('from', '<input id="f-from" type="date" value="' +
          esc(String(fc.from || span.earliest || '').slice(0, 10)) + '">',
          'first decision date' + (span.earliest ? ' · data starts ' + esc(span.earliest) : '')) +
      row('to', '<input id="f-to" type="date" value="' +
          esc(String(fc.to || span.latest || '').slice(0, 10)) + '">',
          'last decision date' + (span.latest ? ' · data ends ' + esc(span.latest) : '')) +
      row('universe', '<select id="f-uni">' + opt(c.universes, '', 'as the step declares') +
          '</select>', 'hold the alpha to a different set of symbols') +
      row('rebalance', '<input id="f-reb" value="' + esc(fc.rebalance || d.rebalance || '5d') +
          '">',
          'gap between decisions, e.g. 1d, 5d, 20d') +
      row('decay', '<input id="f-decay" type="number" min="0" value="' +
          (fc.decay != null ? fc.decay : (d.decay || 0)) + '">',
          'blend the last N portfolios · 0 is off · cuts turnover') +
      row('split', '<input id="f-split" type="date" value="' + esc(d.split || '') + '">',
          'first out-of-sample date · everything before it is the half you chose on') +
      row('seed', '<input id="f-seed" type="number" value="' +
          (fc.seed != null ? fc.seed : (d.seed || 0)) + '">',
          'same seed, same answer') +
      row('commission', '<input id="f-fee" type="number" step="0.5" value="' +
          (fc.fee_bps != null ? fc.fee_bps : (c.costs.fee_bps || 0)) +
          '"> <span class="unit">bps</span>',
          'charged on turnover · raise it until the edge dies, and you know how much ' +
          'of the edge is real') +
      row('slippage', '<input id="f-slip" type="number" step="0.5" value="' +
          (fc.slippage_bps != null ? fc.slippage_bps : (c.costs.slippage_bps || 0)) +
          '"> <span class="unit">bps</span>',
          'charged on turnover too') +
      row('embargo', '<input id="f-emb" value="' + esc(c.costs.embargo || '0d') + '">',
          'wait this long after the as-of date before a return counts') +
      row('purge', '<input id="f-purge" value="' + esc(c.costs.purge || '0d') + '">',
          'hold rows back this long before a step may read them') +
      '</div>' +
      '<div class="rgo"><button type="button" class="btn go" id="f-go">run it</button>' +
      '<span id="f-say" class="faint"></span></div>';
    el('f-go').onclick = fire;
    var ea = el('f-edit-alpha');
    if (ea) {
      ea.onclick = function () {
        var a = ea.getAttribute('data-a');
        var r = BOOK.filter(function (x) { return x.alpha === a; })[0] || {};
        closeRunner();
        window.AlphaEdit.open({
          id: a, name: r.name || a, writes: r.writes,
          reads: (r.reads || [])[0] || (r.conditions || {}).reads,
          universe: r.universe, rebalance: r.rebalance, decay: r.decay,
          conditions: r.conditions || {},
        });
      };
    }
    var byId = {};
    c.alphas.forEach(function (a) { byId[a.id] = a; });
    // On a re-run the form is showing what a run actually used. The alpha's own
    // default must not overwrite that on the way in -- it only applies once you
    // change which alphas are ticked.
    var settled = !from;
    var marks = function () {
      var n = document.querySelectorAll('.f-alpha:checked').length;
      // an alpha says how it wants to be run -- a five-day reversal and a sixty-day
      // momentum do not want the same gap. Ticking one fills the form with its own
      // answer; you can still override it, which is the point of a form.
      var on = Array.prototype.map.call(document.querySelectorAll('.f-alpha:checked'),
        function (i) { return byId[i.value] || {}; });
      var one = function (f) {
        var v = on.map(function (a) { return a[f]; })
          .filter(function (x) { return x != null && x !== ''; });
        return v.length === 1 || (v.length > 1 && v.every(function (x) { return x === v[0]; }))
          ? v[0] : null;
      };
      var reb = one('rebalance'), dec = one('decay');
      if (settled) {
        if (reb != null) el('f-reb').value = reb;
        else if (n) el('f-reb').value = d.rebalance || '5d';
        if (dec != null) el('f-decay').value = dec;
      }
      settled = true;
      var clash = on.length > 1 && reb == null &&
        on.some(function (a) { return a.rebalance; });
      el('f-reb').classList.toggle('warn', clash);
      Array.prototype.forEach.call(document.querySelectorAll('.apick-row'), function (r) {
        var on = r.querySelector('.f-alpha').checked;
        r.classList.toggle('on', on);
        // a share only means something once there is something to share with
        r.querySelector('.f-share').style.visibility = (on && n > 1) ? '' : 'hidden';
      });
    };
    Array.prototype.forEach.call(document.querySelectorAll('.f-alpha'), function (i) {
      i.onchange = marks;
    });
    marks();
  }

  // A run can price several alphas as one book, and its key joins them with "+".
  // Read it out the way a person would say it.
  function niceName(key) {
    return String(key || '').split('+').filter(Boolean)
      .map(function (x) { return x.replace(/^alpha_/, ''); }).join(' + ');
  }

  function _asked(key) {
    return String(key || '').split('+').filter(Boolean);
  }

  // What a re-run is for. Nothing in the engine is random beyond the seed, so
  // repeating a run unchanged repeats its number exactly. Either a setting here
  // moves, or the alphaset itself does. The second one is the interesting
  // case, which is why it is offered rather than buried.
  function rerunNote(from, chosen) {
    if (!from) return '';
    var one = chosen.length === 1 ? chosen[0] : null;
    return '<div class="bt-note-inline rerun">Opened on the settings of ' +
      '<b>run ' + esc(String(from.run_id)) + '</b>. Same settings and the same seed give the ' +
      'same numbers. Change one below, or change the rule itself' +
      (one ? ': <button type="button" class="chip" id="f-edit-alpha" data-a="' + esc(one) +
             '">edit ' + esc(niceName(one)) + '</button>'
           : '. This book holds ' + esc(niceName(chosen.join('+'))) +
             ', so edit one of them in the strategy book.') +
      '</div>';
  }

  function row(label, control, hint) {
    return '<div class="rrow"><label>' + esc(label) + '<i>' + hint + '</i></label>' +
      control + '</div>';
  }

  async function fire() {
    var say = el('f-say'), go = el('f-go');
    var picked = [], share = {}, evenly = true;
    Array.prototype.forEach.call(document.querySelectorAll('.f-alpha'), function (i) {
      if (!i.checked) return;
      picked.push(i.value);
      var n = parseFloat(i.parentNode.querySelector('.f-share').value);
      share[i.value] = isNaN(n) ? 1 : n;
      if (share[i.value] !== 1) evenly = false;
    });
    if (!picked.length) { say.textContent = 'pick at least one alpha'; return; }
    var body = {
      alpha: picked.length === 1 ? picked[0] : picked,
      allocation: (picked.length > 1 && !evenly) ? share : null,
      from: el('f-from').value,
      to: el('f-to').value,
      rebalance: el('f-reb').value || null,
      decay: parseInt(el('f-decay').value, 10) || 0,
      split: el('f-split').value || null,
      universe: el('f-uni').value || null,
      seed: parseInt(el('f-seed').value, 10) || 0,
      fee_bps: parseFloat(el('f-fee').value),
      slippage_bps: parseFloat(el('f-slip').value),
      embargo: el('f-emb').value.trim() || null,
      purge: el('f-purge').value.trim() || null,
    };
    if (!body.from || !body.to) { say.textContent = 'a window needs both ends'; return; }
    go.disabled = true;
    say.innerHTML = '<span class="bt-live">starting…</span>';

    // Fire it and get out of the way. The replay takes as long as it takes, and the
    // place to watch it is the graph and the growing chart -- not a form sitting on
    // top of them with a spinner.
    var run = fetch('/api/backtest', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    window.watchNow();
    PICKED = picked.join('+');
    CURRENT = null;
    RANGE = null;
    DETAIL = null;
    LIST_SIG = null;
    // the graph is about to light up for this alpha, so frame it the same way
    // picking it in the book does -- you should be looking at the right thing
    // before the first rebalance lands
    // the graph lights everything the run will touch, which for a blend is every
    // alpha in it, not the first one named
    var dag = [];
    picked.forEach(function (a) {
      var r = BOOK.filter(function (x) { return x.alpha === a; })[0];
      if (r) dag = dag.concat(r.dag || []);
    });
    if (window.QANAT) window.QANAT.focusAlpha(dag.length ? dag : null, picked.join('+'));
    closeRunner();
    openBacktests();

    try {
      var r = await run;
      if (!r.ok) throw new Error((await r.text()) || r.statusText);
      await r.json();
    } catch (e) {
      el('bt-detail').insertAdjacentHTML('afterbegin',
        '<div class="bt-empty bad">' + esc(e.message) + '</div>');
      return;
    } finally {
      go.disabled = false;
    }
    LIST_SIG = null;
    await refresh();
  }

  function closeRunner() { el('runner').hidden = true; }

  // The pipeline and the results share one column, so how it is split has to be
  // yours. Drag the divider; it is remembered.
  function wireGrip() {
    var grip = el('bt-grip'), panel = el('btwrap');
    if (!grip || !panel) return;
    var saved = parseInt(localStorage.getItem('qanat.results.h') || '', 10);
    if (saved) document.documentElement.style.setProperty('--results-h', saved + 'px');
    var drag = null;
    grip.addEventListener('pointerdown', function (e) {
      drag = { y: e.clientY, h: panel.getBoundingClientRect().height };
      grip.setPointerCapture(e.pointerId);
      grip.classList.add('on');
      document.body.classList.add('resizing');
      e.preventDefault();
    });
    grip.addEventListener('pointermove', function (e) {
      if (!drag) return;
      var stage = panel.parentElement.getBoundingClientRect();
      var h = Math.max(150, Math.min(stage.height - 150, drag.h - (e.clientY - drag.y)));
      document.documentElement.style.setProperty('--results-h', Math.round(h) + 'px');
      refit();
    });
    var stop = function () {
      if (!drag) return;
      drag = null;
      grip.classList.remove('on');
      document.body.classList.remove('resizing');
      var h = parseInt(getComputedStyle(document.documentElement)
        .getPropertyValue('--results-h'), 10);
      if (h) localStorage.setItem('qanat.results.h', String(h));
      refit();
    };
    grip.addEventListener('pointerup', stop);
    grip.addEventListener('pointercancel', stop);
  }

  window.repaintBook = function () { LIST_SIG = null; refresh(); };

  document.addEventListener('DOMContentLoaded', function () {
    el('bt-h').onclick = toggleBacktests;
    // There is no edit mode any more. Everything that can be changed says so
    // where it sits: an alpha card carries `edit`, the book ends in `＋ add an
    // alpha`, the graph carries `＋ data source`, and a stage opens from its rail.
    window.AlphaEdit.setEditing(true);
    wireGrip();
    refresh();
    // the book lives in the left rail now, so it keeps up whether or not the
    // results panel is open
    setInterval(refresh, 5000);
  });

  window.openBacktests = openBacktests;
  window.closeBacktests = closeBacktests;
  window.toggleBacktests = toggleBacktests;
  window.showNewestBacktest = showNewest;
  window.showLiveBacktest = showLive;
  // the graph calls this when a click lands outside the picked alpha's lineage
  window.clearAlphaPick = function () {
    if (!PICKED) return;
    PICKED = null;
    paint();
  };
  window.openRunner = openRunner;
  window.runBacktest = openRunner;
  window.closeRunner = closeRunner;
})();
/* Watching a replay happen.
 *
 * A backtest walks the graph once per as-of date, in dependency order. While it
 * runs, /api/backtest/progress says which job just finished and what it wrote, so
 * the console can light the same nodes in the same order. The DAG fills in from
 * the sources on the left to the portfolio on the right, once per stop.
 *
 * Nothing here invents motion. Every light corresponds to a job that really ran.
 */
(function () {
  'use strict';

  var LAST_FINISHED = null, POLL = null, WAS_RUNNING = false, LAST_PERIODS = -1;
  var LAST_STOP = null;

  function el(id) { return document.getElementById(id); }

  // The graph draws jobs now, so it takes the whole progress snapshot and lights
  // the slabs itself -- there is nothing left for this file to translate.
  function feedGraph(p) {
    if (window.QANAT && window.QANAT.dag) window.QANAT.dag.setProgress(p);
  }

  function strip(p) {
    var box = el('bt-inline-sum');
    if (!box) return;
    if (!p.running) return;
    var pctDone = p.stops_total ? Math.round(p.stops_done / p.stops_total * 100) : 0;
    var t = p.totals || {};
    var so_far = t.periods
      ? ' · net so far <b class="' + (t.net > 0 ? 'up' : 'down') + '">' +
        (t.net * 100).toFixed(2) + '%</b> over ' + t.periods
      : '';
    box.innerHTML = '<span class="bt-live">replaying</span> ' +
      String(p.stop || '').slice(0, 10) + ' · ' + p.stops_done + '/' + p.stops_total +
      ' (' + pctDone + '%)' + so_far;
    var title = el('bt-title');
    if (title) title.textContent = 'run ' + p.run_id;
  }

  async function tick() {
    var p;
    try {
      var r = await fetch('/api/backtest/progress');
      if (!r.ok) return;
      p = await r.json();
    } catch (e) { return; }

    feedGraph(p);

    if (p.running) {
      WAS_RUNNING = true;
      strip(p);
      // One sweep per rebalance. The replay really does run the graph once per
      // as-of date, so the results section gets a pulse each time one lands.
      // twenty rebalances, twenty sweeps, and the charts a little longer each time.
      if (p.stop !== LAST_STOP) {
        LAST_STOP = p.stop;
        var box = document.getElementById('btwrap');
        if (box) {
          box.classList.remove('iter');
          void box.offsetWidth;
          box.classList.add('iter');
        }
      }
      // The curve is redrawn only when a period actually closed, not on every poll.
      if ((p.periods || []).length !== LAST_PERIODS) {
        LAST_PERIODS = p.periods.length;
        window.showLiveBacktest(p);
      }
    }

    // When it ends, bring the result forward rather than making anyone look for it.
    var fin = p.finished && p.finished.run_id;
    if (fin && fin !== LAST_FINISHED && WAS_RUNNING) {
      LAST_FINISHED = fin;
      WAS_RUNNING = false;
      LAST_PERIODS = -1;
      LAST_STOP = null;
      var done = document.getElementById('btwrap');
      if (done) done.classList.remove('iter');
      window.showNewestBacktest();
    }
    schedule(p.running ? 120 : 2500);
  }

  function schedule(ms) {
    clearTimeout(POLL);
    POLL = setTimeout(tick, ms);
  }

  document.addEventListener('DOMContentLoaded', function () { schedule(800); });
  window.watchNow = function () { schedule(60); };
})();
