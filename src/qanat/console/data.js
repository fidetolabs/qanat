/* Data: what you connected, and what is actually in it.
 *
 * The Pipeline page draws the *shape* of the project -- which table feeds which
 * step. That is the right picture once something exists. It is the wrong one on
 * day two, when the only question is whether the thing you just connected is any
 * good: how far back does it go, how many symbols are in it, where are the holes.
 *
 * Row counts alone cannot answer that. "57 rows" and "3,360 rows" look like the
 * same kind of fact until you notice one covers three months and the other covers
 * fourteen, and that every strategy built across both will hold nothing for the
 * eleven months only one of them reaches. So the unit here is the **column**, and
 * the bar is how much of it is filled.
 *
 * Nothing is computed in the browser: `/api/profile` aggregates in the database,
 * because the alternative is moving a million rows so somebody can learn there are
 * five hundred symbols.
 */
(function () {
  'use strict';

  function el(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function num(n) {
    if (n == null) return '—';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
    return String(n);
  }

  async function api(path) {
    var r = await fetch(path);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  //  "*/2 * * * *" is exact and unreadable at a glance. Same rendering the graph
  //  uses under a source node, so the two pages say it the same way.
  function everyWhen(cron) {
    var f = String(cron || '').trim().split(/\s+/);
    if (f.length < 5) return 'on a schedule';
    var m = f[0], h = f[1], dom = f[2], dow = f[4];
    var step = function (v) { var x = /^\*\/(\d+)$/.exec(v); return x ? +x[1] : 0; };
    if (step(m) && h === '*') return 'every ' + step(m) + ' min';
    if (step(h) && dom === '*') return 'every ' + step(h) + 'h';
    if (h === '*') return 'every hour';
    if (dom === '*' && dow === '*') return 'every day';
    if (dow !== '*') return 'every week';
    return 'every month';
  }

  //  A date column is the one worth reading as a span rather than a range, because
  //  a span is what tells you whether two sources overlap.
  function isTime(col) {
    return /DATE|TIME/i.test(col.type || '');
  }

  function days(a, b) {
    var t0 = Date.parse(String(a).slice(0, 10)), t1 = Date.parse(String(b).slice(0, 10));
    if (isNaN(t0) || isNaN(t1)) return null;
    return Math.round((t1 - t0) / 86400000);
  }

  function colRow(col, span) {
    //  For a date column the bar is **coverage of the project's own span**, not how
    //  many cells are filled. That is the difference between "57 rows, none of them
    //  null" -- which is true and useless -- and "this reaches across a seventh of
    //  the history you have". Nulls are still reported, on the right, where they are
    //  a detail rather than the headline.
    var fill, why;
    if (span && isTime(col) && col.min && col.max) {
      var mine = days(col.min, col.max);
      fill = (mine == null || !span.total) ? 1 : Math.max(0, Math.min(1, mine / span.total));
      why = 'covers ' + Math.round(fill * 100) + '% of the project span';
    } else {
      fill = col.fill == null ? 0 : col.fill;
      why = Math.round(fill * 100) + '% filled';
    }
    //  Amber below full, red when most of it is missing: a short column is not a
    //  failure, it is a thing to know before you build on it.
    var tone = fill >= 0.999 ? '' : fill >= 0.5 ? ' part' : ' thin';
    var right;
    if (isTime(col) && col.min && col.max) {
      var d = days(col.min, col.max);
      right = String(col.min).slice(0, 10) + ' → ' + String(col.max).slice(0, 10) +
        (d == null ? '' : '  · ' + num(d) + 'd');
    } else if (col.distinct <= 12 && col.distinct > 0) {
      right = col.distinct + ' distinct';
    } else if (col.min != null && col.max != null && !isTime(col)) {
      right = num(col.distinct) + ' distinct · ' + String(col.min).slice(0, 10) +
        '–' + String(col.max).slice(0, 10);
    } else {
      right = num(col.distinct) + ' distinct';
    }
    var miss = col.nulls ? '<span class="miss">' + num(col.nulls) + ' missing</span>' : '';
    return '<div class="prow">' +
      '<span class="pc" title="' + esc(col.type) + '">' + esc(col.name) + '</span>' +
      '<span class="pbar" title="' + esc(why) + '"><i class="' + tone.trim() +
        '" style="width:' + (fill * 100).toFixed(1) + '%"></i></span>' +
      '<span class="pv">' + esc(right) + ' ' + miss + '</span>' +
      '</div>';
  }

  function card(p, srcOnly, span) {
    var when = p.updated_at ? String(p.updated_at).slice(0, 19) : 'never run';
    var head = '<div class="dh">' +
      '<span class="tag ' + (srcOnly ? 'src' : 'ft') + '">' +
        (srcOnly ? 'source' : 'table') + '</span>' +
      '<span class="dn">' + esc(p.ref) + '</span>' +
      '<span class="dm">' +
        (p.connector ? esc(p.connector) + ' · ' : '') +
        (p.schedule ? esc(everyWhen(p.schedule)) + ' · ' : 'only when you ask · ') +
        num(p.rows) + ' rows · ' + esc(when) +
      '</span>' +
      '<span class="grow"></span>' +
      '<button type="button" class="dbuild" data-ref="' + esc(p.ref) + '">build a step from this</button>' +
      '</div>';
    return '<div class="dcard">' + head +
      (p.columns || []).map(function (c) { return colRow(c, span); }).join('') +
      '</div>';
  }

  //  The widest stretch any source reaches, which is what a date column's bar is a
  //  fraction of. Measuring each source against the project rather than against
  //  itself is what makes a short one *look* short.
  function projectSpan(profiles) {
    var lo = null, hi = null;
    profiles.forEach(function (p) {
      (p.columns || []).forEach(function (c) {
        if (!isTime(c) || !c.min || !c.max || c.name !== p.time_column) return;
        var a = String(c.min).slice(0, 10), b = String(c.max).slice(0, 10);
        if (lo === null || a < lo) lo = a;
        if (hi === null || b > hi) hi = b;
      });
    });
    if (lo === null) return null;
    return { from: lo, to: hi, total: days(lo, hi) || 1 };
  }

  //  Two sources whose dates barely overlap is the single most expensive thing to
  //  find out late: a strategy spanning both holds nothing for the difference, and
  //  the backtest reports that stretch as flat rather than as missing.
  function overlapWarning(profiles) {
    var spans = [];
    profiles.forEach(function (p) {
      (p.columns || []).forEach(function (c) {
        if (isTime(c) && c.min && c.max && c.name === p.time_column) {
          spans.push({ ref: p.ref, from: String(c.min).slice(0, 10), to: String(c.max).slice(0, 10) });
        }
      });
    });
    if (spans.length < 2) return '';
    var latestStart = spans.slice().sort(function (a, b) { return a.from < b.from ? 1 : -1; })[0];
    var earliestStart = spans.slice().sort(function (a, b) { return a.from > b.from ? 1 : -1; })[0];
    var gap = days(earliestStart.from, latestStart.from);
    if (gap == null || gap < 30) return '';
    return '<div class="dwarn"><span class="ic">▲</span><span>' +
      '<b>' + esc(latestStart.ref) + '</b> starts ' + num(gap) + ' days after <b>' +
      esc(earliestStart.ref) + '</b> (' + esc(latestStart.from) + ' vs ' +
      esc(earliestStart.from) + '). A strategy reading both holds nothing until the ' +
      'later one begins, and a backtest counts that stretch as flat.' +
      '</span></div>';
  }

  async function paint() {
    var host = el('data-cards'), note = el('data-note');
    if (!host) return;
    var graph;
    try {
      graph = await api('/api/graph');
    } catch (e) {
      host.innerHTML = '<div class="warnbox">' + esc(e.message) + '</div>';
      return;
    }
    var sources = (graph.jobs || []).filter(function (j) { return j.kind === 'source'; });
    var refs = [];
    sources.forEach(function (j) { (j.to || []).forEach(function (t) { refs.push(t); }); });
    if (!refs.length) {
      note.textContent = '';
      host.innerHTML = '<div class="dempty">Nothing is connected yet. ' +
        '<b>＋ data source</b> on the Pipeline page brings a table in.</div>';
      return;
    }

    note.textContent = 'profiled in the database, not in this page';
    var profiles = [];
    for (var i = 0; i < refs.length; i++) {
      var parts = refs[i].split('.');
      try {
        profiles.push(await api('/api/profile/' + encodeURIComponent(parts[0]) + '/' +
                                encodeURIComponent(parts[1])));
      } catch (e) {
        profiles.push({ ref: refs[i], rows: 0, columns: [], error: e.message });
      }
    }

    var span = projectSpan(profiles);
    if (span) {
      note.textContent = 'bars are coverage of ' + span.from + ' → ' + span.to +
        ', the widest stretch anything here reaches';
    }
    host.innerHTML = overlapWarning(profiles) + profiles.map(function (p) {
      if (p.error) {
        return '<div class="dcard"><div class="dh"><span class="tag src">source</span>' +
          '<span class="dn">' + esc(p.ref) + '</span>' +
          '<span class="dm">' + esc(p.error) + '</span></div></div>';
      }
      return card(p, true, span);
    }).join('');

    //  The handoff. Data is where you notice a step is needed; the graph is where
    //  you build it. Arriving there with the table already ticked is the difference
    //  between the two pages being one workflow and being two screens.
    Array.prototype.forEach.call(host.querySelectorAll('.dbuild'), function (b) {
      b.onclick = function () {
        var ref = b.getAttribute('data-ref');
        if (window.QANAT) window.QANAT.setPage('alpha');
        setTimeout(function () {
          window.AlphaEdit.openStep(null).then(function () {
            if (window.QANAT) window.QANAT.dag.toggleInput(ref);
          });
        }, 120);
      };
    });
  }

  window.DataPage = { paint: paint };
})();
