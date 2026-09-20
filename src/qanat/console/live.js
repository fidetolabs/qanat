/* Live: what the strategy is earning forward, and against what it promised.
 *
 * The engine for this already existed and was well built -- the scheduler scores
 * on the rebalance grid, stamps a frontier so "out of sample" means something
 * honest, and marks the runs `live`. None of it was reachable or visible: the only
 * trace of a live pass was a line in the run log, and the only trace of a *failed*
 * one was a red line in the same log, repeating every thirty seconds while the
 * topbar went on saying `connected`.
 *
 * The comparison is the whole reason to run forward. An out-of-sample number was
 * measured on rows that were already on disk when the alpha was picked: the data
 * did not argue back through the fitting, but it argued back through the person,
 * because you knew how that year went. Only what happens after the frontier is
 * free of that. So the dashed line is the rate the backtest implied and the solid
 * line is what actually happened, and the gap between them is the number worth
 * looking at.
 */
(function () {
  'use strict';

  function el(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function pct(x, dp) {
    if (x == null || isNaN(x)) return '—';
    return (x >= 0 ? '+' : '') + (x * 100).toFixed(dp == null ? 2 : dp) + '%';
  }
  function sign(x) { return x == null ? '' : x > 0 ? ' up' : x < 0 ? ' down' : ''; }
  function day(s) { return s ? String(s).slice(0, 10) : '—'; }

  async function api(path, opts) {
    var r = await fetch(path, opts || undefined);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  function tile(k, v, note, cls) {
    return '<div class="tile"><div class="k">' + k + (note ? '<i>' + note + '</i>' : '') +
      '</div><div class="v' + (cls || '') + '">' + v + '</div></div>';
  }

  //  The periods after the frontier, which are the only ones this page is about.
  function forward(d) {
    if (!d.since) return [];
    var edge = Date.parse(String(d.since).slice(0, 19).replace(' ', 'T'));
    return (d.periods || []).filter(function (p) {
      return Date.parse(String(p.as_of).slice(0, 19).replace(' ', 'T')) > edge;
    });
  }

  //  Solid: what the money did, compounded. Dashed: the same number of periods at
  //  the rate the out-of-sample half of the backtest was running at.
  function chart(ps, perPeriod, hasExp) {
    var W = 960, H = 150, pad = 8;
    if (!ps.length) {
      return '<div class="lvempty">Nothing has been scored past the frontier yet. ' +
        'The next pass runs when the data reaches the next rebalance date.</div>';
    }
    var eq = [1], e = 1;
    ps.forEach(function (p) { e *= 1 + p.net; eq.push(e); });
    var exp = [1];
    for (var i = 0; i < ps.length; i++) exp.push(Math.pow(1 + (perPeriod || 0), i + 1));

    var all = hasExp ? eq.concat(exp) : eq;
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    if (hi - lo < 1e-9) { hi = lo + 0.01; }
    var x = function (i) { return pad + (i / (eq.length - 1 || 1)) * (W - pad * 2); };
    var y = function (v) { return H - pad - ((v - lo) / (hi - lo)) * (H - pad * 2); };
    var path = function (arr) {
      return arr.map(function (v, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' +
        y(v).toFixed(1); }).join(' ');
    };
    var last = eq[eq.length - 1], want = hasExp ? exp[exp.length - 1] : last;
    return '<svg class="lvchart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" ' +
      'role="img" aria-label="live equity against the rate the backtest implied">' +
      '<line x1="0" y1="' + y(1).toFixed(1) + '" x2="' + W + '" y2="' + y(1).toFixed(1) +
        '" stroke="#2e2b28" stroke-width="1" stroke-dasharray="3 5"></line>' +
      (hasExp ? '<path d="' + path(exp) + '" fill="none" stroke="#8b857a" ' +
        'stroke-width="1.3" stroke-dasharray="5 5"></path>' : '') +
      '<path d="' + path(eq) + '" fill="none" stroke="' +
        (last >= want ? '#a2e65d' : '#e8c069') + '" stroke-width="1.8" ' +
        'stroke-linejoin="round"></path>' +
      '<circle cx="' + x(eq.length - 1).toFixed(1) + '" cy="' + y(last).toFixed(1) +
        '" r="3.4" fill="' + (last >= want ? '#a2e65d' : '#e8c069') + '"></circle>' +
      '</svg>';
  }

  //  "On, and nothing has come out of it" has two very different causes, and saying
  //  the wrong one is worse than saying nothing. A misconfiguration needs fixing; a
  //  pass that has not run yet needs only the next rebalance date to arrive.
  function waitingCard(d) {
    return '<div class="lvoff">' +
      '<b>Live is on. Waiting for the first pass.</b>' +
      '<p>Scoring ' + esc((d.alphas || []).join(' + ').replace(/alpha_/g, '') || 'the wired alpha') +
      ' every <span class="mono">' + esc(d.rebalance || '—') + '</span>. ' +
      'The scheduler checks every 30 seconds and runs a pass once the data reaches the next ' +
      'rebalance date — this page updates itself when it does.</p></div>';
  }

  function stalledCard(d) {
    var book = d.book || [];
    return '<div class="lvbad">' +
      '<div class="lvbad-h"><span class="ic">▲</span>' +
      '<b>Live is on and has scored nothing.</b></div>' +
      '<p>' + esc(d.why === 'no alpha named'
        ? 'This project has ' + book.length + ' alphas and none is named to price. Pricing two ' +
          'together is a third strategy, so the choice is not the scheduler’s to make.'
        : (d.why || 'Every pass has ended in an error. The run log has the reason.')) + '</p>' +
      (book.length
        ? '<div class="lvfix"><label for="lv-pick">price live</label>' +
          '<select id="lv-pick">' + book.map(function (a) {
            return '<option value="' + esc(a) + '">' + esc(a.replace(/^alpha_/, '')) + '</option>';
          }).join('') + '</select>' +
          '<button type="button" class="btn go" id="lv-start">start scoring</button>' +
          '<span class="faint" id="lv-say"></span></div>'
        : '') +
      '</div>';
  }

  function offCard(d) {
    return '<div class="lvoff">' +
      '<b>Live scoring is off.</b>' +
      '<p>Switched on, <span class="mono">qanat serve</span> scores a pass every time the data ' +
      'reaches the next rebalance date, and stamps the frontier once — the last date the data ' +
      'held when you started. Everything after it is a return nobody could have looked at.</p>' +
      (d.book && d.book.length
        ? '<div class="lvfix"><label for="lv-pick">price live</label>' +
          '<select id="lv-pick">' + d.book.map(function (a) {
            return '<option value="' + esc(a) + '">' + esc(a.replace(/^alpha_/, '')) + '</option>';
          }).join('') + '</select>' +
          '<button type="button" class="btn go" id="lv-start">turn it on</button>' +
          '<span class="faint" id="lv-say"></span></div>'
        : '<p class="faint">There is no alpha to price yet. Build one on the Alpha page first.</p>') +
      '</div>';
  }

  async function turnOn() {
    var say = el('lv-say'), pick = el('lv-pick');
    say.textContent = 'saving…';
    try {
      //  `GET /api/project` answers with a report -- {config, valid, errors,
      //  warnings} -- and the project itself is under `config`. Putting the report
      //  back is how you get seven pydantic errors at once: three fields missing
      //  because they are a level down, four rejected because a report is not a
      //  project.
      var proj = await api('/api/project');
      var raw = proj.config;
      if (!raw) throw new Error('could not read the project file');
      raw.backtest = raw.backtest || {};
      raw.backtest.live = true;
      raw.backtest.live_alphas = [pick.value];
      await api('/api/project', {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(raw),
      });
    } catch (e) {
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    say.textContent = 'on — the next pass runs when the data reaches the next date';
    paint();
    if (window.QANAT) window.QANAT.paintSpine();
  }

  async function paint() {
    var host = el('live-body');
    if (!host) return;
    var d;
    try {
      d = await api('/api/live');
    } catch (e) {
      host.innerHTML = '<div class="warnbox">' + esc(e.message) + '</div>';
      return;
    }

    if (!d.on) { host.innerHTML = offCard(d); wireFix(); return; }
    // `why` set means the scheduler gave up on purpose; no reason and no passes yet
    // just means it has not come round to one.
    if (d.stalled && d.why) { host.innerHTML = stalledCard(d); wireFix(); return; }
    if (d.stalled) { host.innerHTML = waitingCard(d); return; }

    var ps = forward(d);
    var seg = d.segments || {};
    var oos = seg.out_of_sample || {};
    //  Only a run that was split has an out-of-sample half to promise anything. With
    //  no split there is no rate, and drawing a flat line labelled "expected +0.00%"
    //  would invent a promise nobody made.
    var hasOos = !!(oos.periods && oos.net_per_period != null);
    var perPeriod = hasOos ? oos.net_per_period : 0;

    var eq = 1;
    ps.forEach(function (p) { eq *= 1 + p.net; });
    var got = eq - 1;
    var want = ps.length ? Math.pow(1 + perPeriod, ps.length) - 1 : 0;
    var held = ps.filter(function (p) { return p.holdings; }).length;

    host.innerHTML =
      '<div class="lvhead">' +
        '<span class="lvname">' + esc((d.alphas || []).join(' + ').replace(/alpha_/g, '') ||
          'live') + '</span>' +
        '<span class="lvmeta">running since ' + esc(day(d.since)) + ' · every ' +
          esc(d.rebalance || '—') + '</span>' +
        '<span class="grow"></span>' +
        '<span class="lvmeta">next pass at <b>' + esc(day(d.next_at)) + '</b></span>' +
      '</div>' +

      '<div class="htiles lvtiles">' +
        tile('since live', pct(got), ps.length + ' periods · ' + held + ' held', sign(got)) +
        (hasOos
          ? tile('expected', pct(want), 'at the out-of-sample rate') +
            tile('tracking', pct(got - want), got >= want ? 'ahead of it' : 'behind it',
                 sign(got - want))
          : tile('expected', '—',
                 'this run had no <span class="mono">split</span>, so there is no ' +
                 'out-of-sample rate to compare against') +
            tile('per period', pct(ps.length ? got / ps.length : 0, 3), 'live, so far')) +
        tile('passes', String(d.runs), 'scored since it was turned on') +
      '</div>' +

      '<div class="lvcard">' +
        '<div class="lvcard-h"><span class="t">Forward, against what the backtest promised</span>' +
        '<span class="grow"></span>' +
        (hasOos ? '<span class="lgd"><i class="dash"></i>backtest OOS rate</span>' : '') +
        '<span class="lgd"><i class="solid"></i>live</span></div>' +
        chart(ps, perPeriod, hasOos) +
        '<div class="lvfoot">frontier ' + esc(day(d.since)) +
          ' — nothing after this was on disk when the alpha was chosen</div>' +
      '</div>' +

      (d.holdings && d.holdings.length
        ? '<div class="kicker">Holding now <span class="grow"></span>' +
          '<span class="faint">as the pipeline last wrote it</span></div>' +
          '<div class="lvhold">' + d.holdings.map(function (h) {
            return '<span>' + esc(h.symbol) + '<i>' + (h.weight * 100).toFixed(1) + '%</i></span>';
          }).join('') + '</div>'
        : '');
  }

  function wireFix() {
    var b = el('lv-start');
    if (b) b.onclick = turnOn;
  }

  window.LivePage = { paint: paint };
})();
