/* The pipeline: table - (step) - table - ... - table.
 *
 * The old drawing came from the data-platform prototype: tables as nodes, rows
 * flowing between them, motion whether or not anything was happening. That is the
 * right picture for a feed. It is the wrong one for a backtest, where the dataset
 * is fixed and the only thing that moves is work.
 *
 * **The table is the node and the step is the edge.** A raw source, processed into
 * features, turned into weights, priced into PnL, with a named piece of work on
 * every arrow between them. Columns are the stages, left to right, so the shape of
 * the file is the shape on screen.
 *
 * A packet crosses an edge only while the step on that edge is running.
 *
 * Nothing here animates on its own. Every light comes from a run that happened.
 */
(function (global) {
  'use strict';

  var SLAB_W = 196, SLAB_H = 78, COL_GAP = 96, ROW_GAP = 26;
  var RAIL_H = 48;   // the stage rail sits across the top and must not be sat on
  var RAIL_TOP = 0;  // pushed down when a banner takes the top of the pane

  var SKIN = {
    queued:  { edge: '#3a3a30', ink: '#99958e', bg: '#15150f', bar: '#49453f', word: 'EMPTY' },
    running: { edge: '#a2e65d', ink: '#e9f4da', bg: '#1a2110', bar: '#a2e65d', word: 'FILLING' },
    ok:      { edge: '#4f7a3a', ink: '#cfe6b4', bg: '#111a0e', bar: '#8fce6a', word: 'WRITTEN' },
    failed:  { edge: '#c1503f', ink: '#eda393', bg: '#1e100d', bar: '#c1503f', word: 'FAILED' },
    skipped: { edge: '#2b2b24', ink: '#5c5953', bg: '#101009', bar: '#292826', word: 'STALE' },
  };
  //: the layer a table belongs to, which is the only thing its colour says
  var LAYER = {
    raw: { k: 'SOURCE', c: '#a2e65d' },
    features: { k: 'FEATURE', c: '#7fc4b4' },
    weights: { k: 'WEIGHT', c: '#e8c069' },
    pnl: { k: 'PNL', c: '#c2b6d8' },
  };

  function opOf(job, weightsStage) {
    if (job.kind === 'source') return 'source';
    if ((job.to || []).some(function (t) { return t.split('.')[0] === weightsStage; })) return 'alpha';
    return /\.sql$/i.test(job.script || '') ? 'sql' : 'python';
  }

  function stateOf(job) {
    if (job.status === 'running') return 'running';
    if (job.status === 'failed') return 'failed';
    if (job.status === 'ok') return 'ok';
    if (!job.schedule && job.kind !== 'source') return 'queued';
    return job.schedule ? 'queued' : 'skipped';
  }

  // "*/2 * * * *" is exact but nobody reads it at a glance. The sidebar still shows
  // the cron line verbatim -- this is only the label under the node.
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

  function Dag(canvas) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.nodes = [];
    this.edges = [];
    this.byId = {};
    this.packets = [];
    this.view = { x: 0, y: 0, k: 1 };
    this.selected = null;
    this.hover = null;
    //: when an alpha is picked, its lineage lights and everything else dims. It is
    //: not removed: `normalize` being shared by three alphas is a fact about the
    //: pipeline, and a drawing that hides it is telling a smaller truth.
    this.focus = null;
    //: once you have panned or zoomed, the view is yours. Nothing refits it again
    //: until you ask -- a poll that re-centres the graph every two seconds makes it
    //: impossible to look at anything closely.
    this.userMoved = false;
    //: a view change in flight. Snapping to a new framing loses where you were
    //: looking; gliding keeps the boxes identifiable the whole way across.
    this.glide = null;
    this.onSelect = null;
    this.onHover = null;
    this.now = 0;
    this._wire();
  }

  // ------------------------------------------------------------------ model
  // `ids` is the lineage to light. `set` is the alphaset that was picked -- one
  // alpha, or several priced together -- written the way a run records it:
  // "alpha_low_vol" or "alpha_low_vol+alpha_momentum".
  //
  // The two are not the same thing, and conflating them was a bug: an alphaset has
  // exactly one result table, but a single alpha appears in every blend it is part
  // of, so lighting "every PnL table this alpha touches" lit three of them.
  Dag.prototype.setFocus = function (ids, set) {
    var next = ids && ids.length ? ids.slice().sort().join(',') : null;
    var key = set || null;
    if (next === (this.focusKey || null) && key === (this.focusSet || null)) return;
    this.focusKey = next;
    this.focusSet = key;
    this.focus = ids && ids.length ? ids : null;
    if (this.raw) this.setGraph(this.raw, true);
  };

  // the alphaset a result table belongs to: the alphas that were priced to make it
  function setOf(t) {
    var by = t.producers || (t.producer ? [t.producer] : []);
    return by.join('+');
  }

  Dag.prototype.setGraph = function (g) {
    this.raw = g;
    this.runOrder = g.run_order || [];
    this.stageMeta = {};
    (g.stages || []).forEach(function (st) { this.stageMeta[st.id] = st; }, this);
    var stageIndex = {};
    (g.stages || []).forEach(function (st, i) { stageIndex[st.id] = i; });

    var jobs = {};
    (g.jobs || []).forEach(function (j) { jobs[j.id] = j; });

    // the tables an alpha's lineage touches. Everything else stays on screen, dimmed.
    var focus = this.focus;
    var lit = null;
    if (focus) {
      lit = {};
      focus.forEach(function (id) {
        var j = jobs[id];
        if (!j) return;
        (j.to || []).forEach(function (t) { lit[t] = 1; });
        (j.from || []).forEach(function (t) { lit[t] = 1; });
      });
      // the pipeline ends in what the alpha earned, and a replay writes that table
      // rather than a step -- so it is not in anybody's `to`, and has to be added
      // One alphaset, one result. Not "every result this alpha ever went into".
      var set = this.focusSet;
      (g.tables || []).forEach(function (t) {
        if (t.stage_kind !== 'pnl') return;
        if (set ? setOf(t) === set : (t.producers || []).some(function (a) {
          return focus.indexOf(a) >= 0;
        })) lit[t.ref] = 1;
      });
    }

    var prev = {};
    this.nodes.forEach(function (n) { prev[n.id] = n; });

    var nodes = (g.tables || []).map(function (t) {
      var was = prev[t.ref];
      var maker = t.producer ? jobs[t.producer] : null;
      // a source is landed once and read from then on: a replay never recomputes
      // it, so it has no progress and never carries a bar
      var computed = !!(maker && maker.kind !== 'source');
      // A PnL table is written by the replay when the whole run is over, not once
      // per as-of date. Resetting its bar every pass would show work that is not
      // happening, so it is out of the per-pass drawing the same way a source is.
      var perPass = computed && (t.stage_kind || 'features') !== 'pnl';
      return {
        id: t.ref,
        table: t,
        maker: maker,                       // the step that writes it, if any
        layer: t.stage_kind || 'features',
        state: t.status === 'failed' ? 'failed'
          : t.status === 'running' ? 'running'
          : t.rows ? 'ok' : 'queued',
        rows: t.rows || 0,
        cols: (t.columns || []).length,
        computed: computed,
        // `perPassBase` is whether a table *could* be recomputed by a replay;
        // `perPass` narrows that to the alpha the current run is actually pricing
        perPassBase: perPass,
        perPass: was ? was.perPass : perPass,
        // what a source has instead of progress: where it comes from and whether
        // it is on a clock
        conn: maker && maker.kind === 'source' ? {
          connector: maker.connector || 'source',
          schedule: maker.schedule || null,
          next_at: maker.next_at || null,
          live: !!maker.schedule,
          status: (maker.last_run && maker.last_run.status) || (t.rows ? 'ok' : 'idle'),
        } : null,
        prog: computed ? (was ? was.prog : 0) : 0,
        flash: was ? was.flash : 0,
        depth: stageIndex[t.stage] == null ? 0 : stageIndex[t.stage],
        dim: !!(lit && !lit[t.ref]),
        // A PnL table that no backtest has written does not exist yet. It is drawn
        // only while its alpha is picked, dashed, so you can see where the result
        // will land without a row of empty boxes cluttering the graph the rest of
        // the time.
        planned: (t.stage_kind === 'pnl') && !(t.rows > 0),
        // its rows were computed by something that has since changed. Nothing was
        // deleted -- the table simply stopped being current, and says so.
        stale: !!t.stale,
        x: 0, y: 0,
      };
    });

    // drop the results that do not exist yet, unless you are looking at the alpha
    // that would produce them
    var pickedSet = this.focusSet;
    nodes = nodes.filter(function (n) {
      if (!n.planned) return true;
      if (!focus) return false;
      if (pickedSet) return setOf(n.table) === pickedSet;
      return (n.table.producers || []).some(function (a) { return focus.indexOf(a) >= 0; });
    });

    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });

    // one edge per (input table -> output table), carrying the step that does it
    var edges = [];
    // a replay writes the pnl table, so the arrow into it is named for the thing
    // that does it rather than left off the drawing
    nodes.forEach(function (n) {
      if (n.layer !== 'pnl') return;
      // one arrow per weights table that fed this result. A blend has several, which
      // is the whole point: the table is what those alphas earned together.
      var by = n.table.producers || (n.table.producer ? [n.table.producer] : []);
      var off = !!focus && (pickedSet ? setOf(n.table) !== pickedSet
                                      : !by.some(function (a) { return focus.indexOf(a) >= 0; }));
      by.forEach(function (a) {
        var j = jobs[a];
        (j && j.to || []).forEach(function (w) {
          if (byId[w]) {
            edges.push({
              from: byId[w], to: n, step: { id: 'backtest' }, dashed: true, dim: off,
            });
          }
        });
      });
    });
    (g.jobs || []).forEach(function (j) {
      var off = focus && focus.indexOf(j.id) < 0;
      (j.from || []).forEach(function (a) {
        (j.to || []).forEach(function (b) {
          if (byId[a] && byId[b]) edges.push({ from: byId[a], to: byId[b], step: j, dim: off });
        });
      });
    });

    // where along its curve each label sits: siblings from one table fan out, so
    // three alphas leaving `prices` do not print on top of each other
    var seen = {};
    edges.forEach(function (e) {
      var n = seen[e.from.id] = (seen[e.from.id] || 0) + 1;
      e.slot = n - 1;
    });
    edges.forEach(function (e) {
      var total = seen[e.from.id];
      e.at = total <= 1 ? 0.5 : 0.26 + (e.slot / (total - 1)) * 0.48;
    });

    // An edge crossing a column has to go *somewhere* in that column. Give it a
    // row of its own there -- a waypoint -- and it threads between the boxes
    // instead of detouring around all of them. This is what a layered graph
    // drawing does, and it is why Graphviz output does not have long sweeps in it.
    var routed = [];
    edges.forEach(function (e) {
      var span = e.to.depth - e.from.depth;
      if (span <= 1) {
        e.src = e.from.id;
        e.dst = e.to.id;
        routed.push(e);
        return;
      }
      var prev = e.from;
      for (var d = e.from.depth + 1; d < e.to.depth; d++) {
        var way = {
          id: '~' + e.from.id + '>' + e.to.id + '@' + d,
          dummy: true, depth: d, x: 0, y: 0, h: 18,
          layer: e.to.layer, dim: e.dim, state: 'ok', rows: 0, cols: 0, prog: 1, flash: 0,
        };
        nodes.push(way);
        byId[way.id] = way;
        routed.push({ from: prev, to: way, step: e.step, dim: e.dim, dashed: e.dashed,
                      via: true, head: prev === e.from,
                      src: e.from.id, dst: e.to.id });
        prev = way;
      }
      routed.push({ from: prev, to: e.to, step: e.step, dim: e.dim, dashed: e.dashed,
                    via: true, tail: true, src: e.from.id, dst: e.to.id });
    });
    edges = routed;

    this.nodes = nodes;
    this.edges = edges;
    this.byId = byId;
    this.jobs = jobs;
    this.layout();
    if (this.selected && !byId[this.selected]) this.selected = null;
  };

  Dag.prototype.layout = function () {
    var cols = {};
    this.nodes.forEach(function (n) { (cols[n.depth] = cols[n.depth] || []).push(n); });
    var depths = Object.keys(cols).map(Number).sort(function (a, b) { return a - b; });
    // the real pixel height of the tallest column, gaps counted once
    var tallest = 0;
    depths.forEach(function (d) {
      var hh = cols[d].reduce(function (a, n) { return a + (n.dummy ? n.h : SLAB_H); }, 0) +
        (cols[d].length - 1) * ROW_GAP;
      tallest = Math.max(tallest, hh);
    });

    // Order the rows so the lines cross as little as possible. Each node moves to
    // the average row of the things it connects to, swept forwards then backwards
    // a few times -- the standard trick for layered graphs, and it does most of the
    // work that would otherwise need edges routed around each other.
    depths.forEach(function (d) {
      cols[d].sort(function (a, b) { return a.id < b.id ? -1 : 1; });
      cols[d].forEach(function (n, i) { n.row = i; });
    });
    var edges = this.edges;
    var bary = function (n, dir) {
      var seen = [];
      edges.forEach(function (e) {
        if (dir > 0 && e.to === n && e.from.row != null) seen.push(e.from.row);
        if (dir < 0 && e.from === n && e.to.row != null) seen.push(e.to.row);
      });
      if (!seen.length) return n.row;
      return seen.reduce(function (a, b) { return a + b; }, 0) / seen.length;
    };
    for (var pass = 0; pass < 4; pass++) {
      var order = pass % 2 ? depths.slice().reverse() : depths;
      var dir = pass % 2 ? -1 : 1;
      order.forEach(function (d) {
        var col = cols[d];
        col.forEach(function (n) { n.bary = bary(n, dir); });
        col.sort(function (a, b) {
          return a.bary - b.bary || (a.id < b.id ? -1 : 1);
        });
        col.forEach(function (n, i) { n.row = i; });
      });
    }

    depths.forEach(function (d, ci) {
      var col = cols[d];
      var hs = col.map(function (n) { return n.dummy ? n.h : SLAB_H; });
      var h = hs.reduce(function (a, b) { return a + b; }, 0) + (col.length - 1) * ROW_GAP;
      var y = -h / 2;
      col.forEach(function (n, ri) {
        n.x = ci * (SLAB_W + COL_GAP);
        n.y = y;
        y += hs[ri] + ROW_GAP;
      });
    });
    this.span = {
      w: depths.length * (SLAB_W + COL_GAP) - COL_GAP,
      h: tallest,
      cols: depths.length,
    };

    // The stage rail. A stage can take more than one column -- `features` usually
    // does, because a feature reads another feature -- so consecutive columns that
    // belong to the same stage become one band, and the band is the thing you
    // hover and click. It is the shape of the file, drawn behind the graph.
    var meta = this.stageMeta || {};
    var bands = [];
    depths.forEach(function (d, ci) {
      var col = cols[d].filter(function (n) { return !n.dummy; });
      var st = col.length ? (col[0].table && col[0].table.stage) : null;
      var same = col.every(function (n) { return n.table && n.table.stage === st; });
      var id = same ? st : null;
      var last = bands[bands.length - 1];
      var x0 = ci * (SLAB_W + COL_GAP) - COL_GAP / 2;
      var x1 = x0 + SLAB_W + COL_GAP;
      if (last && last.id === id && id) { last.x1 = x1; last.tables += col.length; return; }
      bands.push({
        id: id, x0: x0, x1: x1, tables: col.length,
        kind: (meta[id] && meta[id].kind) || (col[0] && col[0].layer) || '',
        note: (meta[id] && meta[id].description) || '',
      });
    });
    this.bands = bands;
    // A poll every couple of seconds re-lays the same graph. Refitting each time
    // would undo whatever you were looking at, so it only happens when the shape
    // is genuinely different from the one on screen.
    var shape = this.nodes.map(function (n) { return n.id + '@' + n.depth; }).sort().join(',');
    if (shape !== this.shapeKey) {
      this.shapeKey = shape;
      this.fit();
    }
  };

  // Opening the panel narrows the column. Rescaling the graph for that is a lot of
  // motion for reading a table -- the boxes all change size and change back. This
  // slides the graph into the space that is left and leaves the zoom alone.
  Dag.prototype.recentre = function () {
    if (this.userMoved || this.glide || !this.span || !this.span.w) return;
    var w = this.cv.clientWidth || 800, h = this.cv.clientHeight || 400;
    this.view.x = (w - this.span.w * this.view.k) / 2;
    this.view.y = RAIL_TOP + RAIL_H + (h - RAIL_H - RAIL_TOP) / 2;
  };

  // Move the view over a beat instead of jumping. Calling it again while one is
  // running just changes the destination, so the staggered fits after a panel
  // opens read as one movement rather than three snaps.
  Dag.prototype.glideTo = function (k, x, y, dur) {
    var v = this.view;
    if (Math.abs(v.k - k) < 1e-4 && Math.abs(v.x - x) < 0.5 && Math.abs(v.y - y) < 0.5) {
      this.glide = null;
      return;
    }
    this.glide = {
      from: { k: v.k, x: v.x, y: v.y },
      to: { k: k, x: x, y: y },
      t: 0, dur: dur || 0.34,
    };
  };

  Dag.prototype.stepGlide = function (dt) {
    var g = this.glide;
    if (!g) return;
    g.t += dt / g.dur;
    if (g.t >= 1) {
      this.view.k = g.to.k;
      this.view.x = g.to.x;
      this.view.y = g.to.y;
      this.glide = null;
      return;
    }
    var t = g.t < 0.5 ? 4 * g.t * g.t * g.t
      : 1 - Math.pow(-2 * g.t + 2, 3) / 2;          // ease in and out
    this.view.k = g.from.k + (g.to.k - g.from.k) * t;
    this.view.x = g.from.x + (g.to.x - g.from.x) * t;
    this.view.y = g.from.y + (g.to.y - g.from.y) * t;
  };

  // Fit to whatever is lit. Picking an alpha should bring its lineage up to a size
  // you can read, not leave it small inside the whole file.
  Dag.prototype.fitFocus = function () {
    var lit = this.nodes.filter(function (n) { return !n.dim && !n.dummy; });
    if (!lit.length) return this.fit(true);
    var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    lit.forEach(function (n) {
      x0 = Math.min(x0, n.x); x1 = Math.max(x1, n.x + SLAB_W);
      y0 = Math.min(y0, n.y); y1 = Math.max(y1, n.y + SLAB_H);
    });
    var w = this.cv.clientWidth || 800, h = this.cv.clientHeight || 400;
    var usable = h - RAIL_H - RAIL_TOP;
    // never past natural size: a four-box lineage blown up to fill the band is
    // harder to read than the same boxes at the size everything else uses
    var k = Math.max(0.3, Math.min((w - 60) / Math.max(1, x1 - x0),
                                   (usable - 48) / Math.max(1, y1 - y0), 1.0));
    this.userMoved = false;
    this.glideTo(k, (w - (x1 - x0) * k) / 2 - x0 * k,
                 RAIL_TOP + RAIL_H + usable / 2 - ((y0 + y1) / 2) * k);
  };

  Dag.prototype.fit = function (force) {
    if (!this.span || !this.span.w) return;
    if (this.userMoved && !force) return;
    var w = this.cv.clientWidth || 800, h = this.cv.clientHeight || 400;
    var usable = h - RAIL_H - RAIL_TOP;
    var k = Math.min((w - 28) / this.span.w, (usable - 28) / Math.max(1, this.span.h), 1.15);
    var kk = Math.max(0.28, k);
    var cy = RAIL_TOP + RAIL_H + usable / 2;
    if (force) this.userMoved = false;
    // an explicit fit glides; the automatic one on first paint just lands
    if (force && this.span.w) {
      this.glideTo(kk, (w - this.span.w * kk) / 2, cy);
    } else {
      this.glide = null;
      this.view.k = kk;
      this.view.x = (w - this.span.w * kk) / 2;
      this.view.y = cy;
    }
  };

  // ------------------------------------------------------- replay reporting
  // A pass runs the graph once per as-of date, in dependency order. On a small
  // project a whole pass can finish inside one poll, and applying it in one frame
  // made the graph blink from empty to done with nothing in between -- which threw
  // away the one thing the drawing is for: showing the order the work happened in.
  //
  // So a snapshot is not drawn, it is *queued*. Every job the engine reported goes
  // into a queue in the order it ran, and the queue is drained across the gap to
  // the next snapshot. Nothing is invented and nothing is skipped: what you watch
  // is every job that ran, in its real order, at a speed an eye can follow.
  Dag.prototype.setProgress = function (p) {
    var self = this;
    if (!p || !p.running) {
      // let the queue finish before the graph goes quiet, so the last pass is not
      // cut off mid-way
      if (this.jobQ && this.jobQ.length) { this.endAfterQ = true; return; }
      this.replayStop = null;
      this.replaying = false;
      this.jobQ = [];
      this.queuedNo = 0;
      this.nodes.forEach(function (n) { n.perPass = n.perPassBase; });
      return;
    }
    this.replaying = true;
    this.endAfterQ = false;
    this.stopNo = (p.stops_done || 0) + 1;
    this.stopsTotal = p.stops_total || 0;
    this.jobQ = this.jobQ || [];

    // how long the engine takes between snapshots, measured rather than assumed
    if (this.snapAt != null) {
      var gap = this.now - this.snapAt;
      this.snapGap = this.snapGap ? this.snapGap * 0.6 + gap * 0.4 : gap;
    }
    this.snapAt = this.now;

    // Which tables this replay recomputes at all. A backtest replays one alpha's
    // lineage, so the other alphas are not idle -- they are simply not in this
    // run, and blanking their bars each pass would claim work that never happened.
    if (p.jobs_in_run && p.jobs_in_run.length) {
      var inRun = {};
      p.jobs_in_run.forEach(function (id) { inRun[id] = 1; });
      var writes = {};
      ((this.raw && this.raw.jobs) || []).forEach(function (j) {
        if (inRun[j.id]) (j.to || []).forEach(function (r) { writes[r] = 1; });
      });
      this.inRun = inRun;
      this.nodes.forEach(function (n) { n.perPass = !!(n.perPassBase && writes[n.id]); });
    }

    // Queue every pass the engine has run that has not been drawn yet. A pass can
    // finish between two polls, so this is where the missed ones are picked up --
    // they are drawn a moment late, but every one of them is drawn.
    var passes = p.passes || [];
    var from = this.queuedNo || 0;
    for (var i = 0; i < passes.length; i++) {
      var pass = passes[i];
      if (pass.no <= from) continue;
      this.jobQ.push({ reset: pass.stop, no: pass.no });
      (pass.jobs || []).forEach(function (j) { self.jobQ.push({ job: j }); });
      this.queuedNo = pass.no;
    }
  };

  // Drain the queue. The rate is set so the queue empties about when the next
  // snapshot arrives -- fast when the engine is fast, slow when it is slow. The
  // bar under a node is therefore paced by the real run, not by an animation.
  Dag.prototype.drain = function (dt) {
    var q = this.jobQ;
    if (!q || !q.length) {
      if (this.endAfterQ) {
        this.endAfterQ = false;
        this.replaying = false;
        this.replayStop = null;
        this.queuedNo = 0;
      }
      return;
    }
    var gap = Math.max(0.08, this.snapGap || 0.12);
    this.credit = (this.credit || 0) + dt * (q.length / gap);
    var n = Math.min(q.length, Math.floor(this.credit));
    if (n < 1) return;
    this.credit -= n;
    for (var i = 0; i < n; i++) this.apply(q.shift());
    this.aim();
  };

  Dag.prototype.apply = function (item) {
    var self = this;
    if (item.reset) {
      this.replayStop = item.reset;
      if (this.stopStarted) {
        var took = this.now - this.stopStarted;
        this.passTime = this.passTime ? this.passTime * 0.7 + took * 0.3 : took;
      }
      this.stopStarted = this.now;
      this.passDone = {};
      this.nodes.forEach(function (n) {
        if (n.perPass) { n.state = 'queued'; n.prog = 0; }
      });
      return;
    }
    var j = item.job;
    this.passDone = this.passDone || {};
    this.passDone[j.job] = 1;
    (j.wrote || []).forEach(function (ref) {
      var n = self.byId[ref];
      if (!n || !n.perPass) return;   // a source has no pass to be part of
      n.state = j.status === 'ok' ? 'ok' : 'failed';
      n.prog = 1;
      n.flash = 1;
      if (j.status === 'ok') self.fire(n, j.job);
    });
  };

  // Which table is being written next. The run order comes from the engine, so
  // this is the job the replay will report next -- not a guess from the shape of
  // the graph. Two tables at the same depth used to be a coin flip.
  Dag.prototype.aim = function () {
    var self = this, seq = this.runOrder || [], done = this.passDone || {};
    var jobs = (this.raw && this.raw.jobs) || [];
    for (var q = 0; q < seq.length; q++) {
      if (done[seq[q]]) continue;
      var id = seq[q];
      var job = jobs.filter(function (x) { return x.id === id; })[0];
      var live = ((job && job.to) || []).map(function (r) { return self.byId[r]; })
        .filter(function (n) { return n && n.perPass && n.state === 'queued'; });
      if (!live.length) continue;      // a source, or not in this alpha's lineage
      live.forEach(function (n) { if (n.state !== 'running') { n.state = 'running'; n.prog = 0; } });
      return;
    }
  };

  Dag.prototype.fire = function (node, jobId) {
    var self = this, chains = {};
    this.edges.forEach(function (e) {
      if (e.dst !== node.id) return;
      if (jobId && e.step && e.step.id !== jobId) return;
      (chains[e.src] = chains[e.src] || []).push(e);
    });
    Object.keys(chains).forEach(function (src) {
      // put the segments in the order they are travelled
      var segs = chains[src], by = {}, out = [];
      segs.forEach(function (e) { by[e.from.id] = e; });
      var cur = src;
      while (by[cur]) { out.push(by[cur]); cur = by[cur].to.id; }
      if (!out.length) out = segs;
      var dur = (0.42 + Math.random() * 0.12) / out.length;
      self.packets.push({ segs: out, i: 0, t: 0, dur: dur });
    });
  };

  // ------------------------------------------------------------------- draw
  Dag.prototype.resize = function () {
    var dpr = global.devicePixelRatio || 1;
    var w = this.cv.clientWidth, h = this.cv.clientHeight;
    if (!w || !h) return;
    var W = Math.round(w * dpr), H = Math.round(h * dpr);
    // Writing canvas.width clears the canvas, so doing it on every frame of a
    // slide leaves blank frames between draws -- which reads as a blink. Only
    // touch the backing store when the size has actually changed.
    if (this.cv.width === W && this.cv.height === H) return;
    this.cv.width = W;
    this.cv.height = H;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  Dag.prototype.at = function (n) {
    return { x: this.view.x + n.x * this.view.k, y: this.view.y + n.y * this.view.k };
  };

  Dag.prototype.tick = function (dt) {
    this.now += dt;
    this.stepGlide(dt);
    this.drain(dt);
    for (var i = this.packets.length - 1; i >= 0; i--) {
      var p = this.packets[i];
      p.t += dt / p.dur;
      while (p.t >= 1 && p.i < p.segs.length) { p.i++; p.t -= 1; }
      if (p.i >= p.segs.length) this.packets.splice(i, 1);
    }
    var self = this;
    this.nodes.forEach(function (n) {
      if (n.flash > 0) n.flash = Math.max(0, n.flash - dt * 1.6);
      // creeps while its job is in flight, and is snapped to full the moment the
      // job reports -- so the bar never claims to be further on than the work is
      if (n.state === 'running') {
        var rate = 1 / Math.max(0.12, self.passTime || 0.5);
        n.prog = Math.min(0.9, n.prog + dt * rate);
      }
    });
    this.draw();
  };

  Dag.prototype.edgePath = function (e) {
    var a = this.at(e.from), b = this.at(e.to), k = this.view.k;
    // every edge is one hop now: a long one was cut into hops at its waypoints
    return {
      x0: a.x + (e.from.dummy ? 0 : SLAB_W * k),
      y0: a.y + (e.from.dummy ? e.from.h * k / 2 : SLAB_H * k / 2),
      x1: b.x + (e.to.dummy ? 0 : 0),
      y1: b.y + (e.to.dummy ? e.to.h * k / 2 : SLAB_H * k / 2),
    };
  };

  // one place that knows the shape of an edge, so the line, the packets and the
  // label all agree about where it runs
  Dag.prototype.edgeAt = function (p, t) {
    var mt = 1 - t, mx = (p.x0 + p.x1) / 2;
    return {
      x: mt * mt * mt * p.x0 + 3 * mt * mt * t * mx + 3 * mt * t * t * mx + t * t * t * p.x1,
      y: mt * mt * mt * p.y0 + 3 * mt * mt * t * p.y0 + 3 * mt * t * t * p.y1 + t * t * t * p.y1,
    };
  };

  Dag.prototype.draw = function () {
    var c = this.ctx, k = this.view.k, self = this;
    var w = this.cv.clientWidth, h = this.cv.clientHeight;
    if (!w || !h) return;
    var dpr = global.devicePixelRatio || 1;
    if (Math.abs(this.cv.width - Math.round(w * dpr)) > 1 ||
        Math.abs(this.cv.height - Math.round(h * dpr)) > 1) {
      this.resize();
      this.recentre();
    }
    c.clearRect(0, 0, this.cv.width, this.cv.height);
    this.drawBands(w, h);

    this.edges.forEach(function (e) {
      var p = self.edgePath(e);
      var mx = (p.x0 + p.x1) / 2;
      c.beginPath();
      c.moveTo(p.x0, p.y0);
      c.bezierCurveTo(mx, p.y0, mx, p.y1, p.x1, p.y1);
      var hot = self.selected && (e.src === self.selected || e.dst === self.selected);
      c.setLineDash(e.dashed ? [4, 4] : []);
      c.strokeStyle = hot ? 'rgba(162,230,93,0.5)'
        : (e.dim ? 'rgba(255,255,255,0.045)' : 'rgba(255,255,255,0.16)');
      c.lineWidth = hot ? 1.6 : 1;
      c.stroke();
      c.setLineDash([]);
    });

    this.packets.forEach(function (pk) {
      var seg = pk.segs[pk.i];
      if (!seg) return;
      var at = self.edgeAt(self.edgePath(seg), pk.t);
      c.beginPath();
      c.arc(at.x, at.y, 2.6, 0, Math.PI * 2);
      c.fillStyle = 'rgba(162,230,93,' + (0.9 * (1 - Math.abs(0.5 - pk.t) * 1.2)).toFixed(3) + ')';
      c.fill();
    });

    this.nodes.forEach(function (n) { if (!n.dummy) self.slab(n); });
    var taken = [];   // the label rectangles already placed this frame
    this.edges.forEach(function (e) {
      if (e.via && !e.head) return;   // a long edge is named once, at its start
      self.edgeLabel(e, taken);
    });
  };

  Dag.prototype.slab = function (n) {
    var c = this.ctx, k = this.view.k, at = this.at(n);
    var W = SLAB_W * k, H = SLAB_H * k;
    if (at.x + W < -40 || at.x > this.cv.clientWidth + 40) return;
    var sk = SKIN[n.state] || SKIN.queued, lay = LAYER[n.layer] || LAYER.features;
    var on = this.selected === n.id, over = this.hover === n.id;

    c.save();
    if (n.dim) c.globalAlpha = 0.32;
    if (n.flash > 0) {
      c.shadowColor = sk.edge;
      c.shadowBlur = 22 * n.flash;
    }
    // A planned table has no rows and no columns yet: it is where the result will
    // land. Drawn as an outline, so it reads as a place rather than a thing.
    c.fillStyle = n.planned ? 'rgba(16,15,14,0.55)' : sk.bg;
    c.strokeStyle = on ? '#f4f2ed' : (over ? '#6f6a60' : sk.edge);
    c.lineWidth = on ? 1.8 : 1;
    if (n.planned) c.setLineDash([5 * k, 4 * k]);
    round(c, at.x, at.y, W, H, 6 * k);
    c.fill();
    c.shadowBlur = 0;
    c.stroke();
    c.setLineDash([]);

    // the layer's colour along the top: a table is what it holds, first of all
    c.fillStyle = lay.c;
    c.fillRect(at.x + 1, at.y + 1, W - 2, Math.max(1.5, 2.4 * k));

    if (k < 0.52) {
      c.textBaseline = 'middle';
      c.fillStyle = sk.ink;
      c.font = '600 ' + Math.max(8.5, 11 * k).toFixed(1) +
        'px ui-monospace, SFMono-Regular, Menlo, monospace';
      c.fillText(clip(c, n.table.name, W - 12), at.x + 6, at.y + H / 2);
      c.restore();
      return;
    }

    c.textBaseline = 'top';
    c.fillStyle = sk.ink;
    c.font = '600 ' + (12 * k).toFixed(1) + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
    c.fillText(clip(c, n.table.name, W - 18 * k), at.x + 9 * k, at.y + 10 * k);

    c.fillStyle = '#8d8982';
    c.font = (9.5 * k).toFixed(1) + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
    c.fillText(clip(c, n.planned ? 'not written yet'
                       : (n.stale ? '~ ' : '') + rows(n.rows) + ' · ' + n.cols + ' cols',
                    W - 18 * k),
               at.x + 9 * k, at.y + 27 * k);

    c.fillStyle = lay.c;
    c.font = '600 ' + (8.5 * k).toFixed(1) + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
    c.fillText(lay.k, at.x + 9 * k, at.y + 41 * k);

    c.fillStyle = '#6c6962';
    c.font = (8.5 * k).toFixed(1) + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
    var word = n.planned ? 'AFTER A BACKTEST'
      : n.stale && !this.replaying ? 'OUT OF DATE'
        : (this.replaying && n.perPass && this.stopsTotal)
          ? sk.word + ' ' + this.stopNo + '/' + this.stopsTotal : sk.word;
    if (n.planned) c.fillStyle = '#8d8982';
    else if (n.stale && !this.replaying) c.fillStyle = '#e8c069';
    c.fillText(word, at.x + W - 9 * k - c.measureText(word).width, at.y + 41 * k);

    // A source has no pass to report, so it reports its connection instead: what
    // it connects to, and whether anything is on a clock to fetch it again.
    if (n.conn) {
      var cy = at.y + H - 11 * k;
      var dot = n.conn.status === 'failed' ? '#c1503f'
        : n.conn.live ? '#a2e65d' : '#5c584e';
      c.beginPath();
      c.arc(at.x + 12 * k, cy, 2.6 * k, 0, Math.PI * 2);
      c.fillStyle = dot;
      c.fill();
      if (n.conn.live) {
        c.beginPath();
        c.arc(at.x + 12 * k, cy, (4.2 + Math.sin(this.now * 2.4) * 1.6) * k, 0, Math.PI * 2);
        c.strokeStyle = 'rgba(162,230,93,0.35)';
        c.lineWidth = 1;
        c.stroke();
      }
      c.fillStyle = '#8d8982';
      c.font = (8.5 * k).toFixed(1) + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
      var line = n.conn.connector + ' · ' +
        (n.conn.live ? everyWhen(n.conn.schedule) : 'only when you ask');
      c.fillText(clip(c, line, W - 30 * k), at.x + 20 * k, cy + 3 * k);
      c.restore();
      return;
    }

    // Only during a replay, and only for a table something is actually computing.
    // A permanently full bar under a table that was written last week says nothing.
    if (this.replaying && n.perPass) {
      var by = at.y + H - 13 * k, bw = W - 18 * k;
      c.fillStyle = '#070706';
      c.fillRect(at.x + 9 * k, by, bw, 4 * k);
      c.fillStyle = sk.bar;
      c.fillRect(at.x + 9 * k, by, bw * Math.max(0, Math.min(1, n.prog)), 4 * k);
    }
    c.restore();
  };

  // The step rides on the arrow it performs. That is the whole point of the shape:
  // a table, a named piece of work, another table.
  Dag.prototype.edgeLabel = function (e, taken) {
    var c = this.ctx, k = this.view.k;
    if (k < 0.5) return;
    var p = this.edgePath(e);
    var f = e.at == null ? 0.5 : e.at;
    var spot = this.edgeAt(p, f);
    var x = spot.x, y = spot.y;

    var txt0 = e.step.id;
    c.font = '600 ' + (8.5 * k).toFixed(1) + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
    var lw = c.measureText(txt0).width + 10 * k, lh = 13 * k;

    // A label printed over a table is worse than no label. Nudge it off any box it
    // lands on, and off any label already placed, before drawing it.
    var self = this;
    var clash = function (yy) {
      for (var i = 0; i < self.nodes.length; i++) {
        var a = self.at(self.nodes[i]);
        if (x + lw / 2 > a.x - 2 && x - lw / 2 < a.x + SLAB_W * k + 2 &&
            yy + lh / 2 > a.y - 2 && yy - lh / 2 < a.y + SLAB_H * k + 2) return true;
      }
      for (var t = 0; t < taken.length; t++) {
        var r = taken[t];
        if (x + lw / 2 > r.x0 - 3 && x - lw / 2 < r.x1 + 3 &&
            yy + lh / 2 > r.y0 - 2 && yy - lh / 2 < r.y1 + 2) return true;
      }
      return false;
    };
    if (clash(y)) {
      var step = 9 * k, found = false;
      for (var d = 1; d <= 9 && !found; d++) {
        if (!clash(y - d * step)) { y -= d * step; found = true; break; }
        if (!clash(y + d * step)) { y += d * step; found = true; break; }
      }
      if (!found) return;   // nowhere clear: better silent than printed over a table
    }
    taken.push({ x0: x - lw / 2, x1: x + lw / 2, y0: y - lh / 2, y1: y + lh / 2 });
    var txt = txt0, w = lw, h = lh;
    var hot = this.selected && (e.src === this.selected || e.dst === this.selected);
    c.save();
    if (e.dim) c.globalAlpha = 0.3;
    c.fillStyle = hot ? '#1c2413' : '#111109';
    c.strokeStyle = hot ? '#a2e65d66' : '#2a2a22';
    c.lineWidth = 1;
    round(c, x - w / 2, y - h / 2, w, h, 3 * k);
    c.fill();
    c.stroke();
    c.fillStyle = hot ? '#cfe6b4' : '#8d8982';
    c.textBaseline = 'middle';
    c.fillText(txt, x - w / 2 + 5 * k, y);
    c.restore();
  };

  function rows(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M rows';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k rows';
    return n + ' rows';
  }

  function round(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  // The run log stamps to the second, so anything under a second would print as
  // "0ms" -- a precision we do not have. Below a second, say nothing.
  function fmtMs(ms) {
    if (!ms || ms < 1000) return '';
    return ms < 60000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms / 60000) + 'm';
  }

  function clip(c, text, max) {
    var t = String(text);
    while (t.length > 3 && c.measureText(t).width > max) t = t.slice(0, -2);
    return t;
  }

  // ------------------------------------------------------------------- input
  Dag.prototype.hit = function (mx, my) {
    var k = this.view.k;
    for (var i = this.nodes.length - 1; i >= 0; i--) {
      var n = this.nodes[i];
      if (n.dummy) continue;
      var at = this.at(n);
      if (mx >= at.x && mx <= at.x + SLAB_W * k && my >= at.y && my <= at.y + SLAB_H * k) return n;
    }
    return null;
  };

  // The rail: one band per stage, floor to ceiling, named at the top. It is drawn
  // first so everything else sits on it, and it is what tells you that the columns
  // are not just an arrangement -- they are the stages the project declares.
  var MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace';
  var BAND_TINT = {
    source: '150,150,150', raw: '150,150,150', features: '127,196,180',
    weights: '232,192,105', pnl: '162,230,93',
  };

  Dag.prototype.setRailTop = function (px) { RAIL_TOP = px || 0; };

  Dag.prototype.drawBands = function (w, h) {
    if (!this.bands || !this.bands.length) return;
    var v = this.view, c = this.ctx;
    c.save();
    this.bands.forEach(function (b, i) {
      var x0 = b.x0 * v.k + v.x, x1 = b.x1 * v.k + v.x;
      if (x1 < -40 || x0 > w + 40) return;
      var on = this.hoverBand === b.id;
      var tint = BAND_TINT[b.kind] || '150,150,150';
      if (on) {
        c.fillStyle = 'rgba(28,30,24,0.45)';
        c.fillRect(x0, 0, x1 - x0, h);
      }
      c.fillStyle = 'rgba(' + tint + ',' + (on ? 0.12 : (i % 2 ? 0.03 : 0.014)) + ')';
      c.fillRect(x0, 0, x1 - x0, h);
      c.beginPath();
      c.moveTo(Math.round(x0) + 0.5, 0);
      c.lineTo(Math.round(x0) + 0.5, h);
      c.moveTo(Math.round(x1) - 0.5, 0);
      c.lineTo(Math.round(x1) - 0.5, h);
      c.strokeStyle = 'rgba(' + tint + ',' + (on ? 0.45 : 0.10) + ')';
      c.lineWidth = 1;
      c.stroke();
      // the label is pinned to the top of the canvas, not to the graph: the rail
      // has to stay readable however far you have panned down
      var cx = Math.max(x0 + 10, Math.min((x0 + x1) / 2, w - 10));
      // a band is only as wide as its columns, and a long description has to live
      // inside it -- spilling into the next stage would say the wrong thing
      var room = Math.max(60, Math.min(x1, w) - Math.max(x0, 0) - 16);
      c.textAlign = 'center';
      c.font = '600 10px ' + MONO;
      c.fillStyle = 'rgba(' + tint + ',' + (on ? 1 : 0.62) + ')';
      c.fillText(clip(c, String(b.id).toUpperCase(), room), cx, RAIL_TOP + 16);
      c.font = '10px ' + MONO;
      c.fillStyle = 'rgba(255,255,255,' + (on ? 0.55 : 0.26) + ')';
      c.fillText(clip(c, on && b.note ? b.note
                         : b.tables + (b.tables === 1 ? ' table' : ' tables'), room),
                 cx, RAIL_TOP + 30);
      if (on) {
        c.fillStyle = 'rgba(' + tint + ',0.8)';
        c.fillText(clip(c, 'click to edit this stage', room), cx, RAIL_TOP + 44);
      }
      c.textAlign = 'left';
    }, this);
    c.restore();
  };

  // You can drag as far as you like, but not so far that the graph is gone. At
  // least one slab stays on screen, so there is always something to drag back by.
  Dag.prototype.clampView = function () {
    if (!this.nodes.length) return;
    var w = this.cv.clientWidth || 800, h = this.cv.clientHeight || 400, k = this.view.k;
    var pad = 40;
    var xs = [], ys = [];
    this.nodes.forEach(function (n) {
      if (n.dummy) return;
      xs.push(n.x, n.x + SLAB_W);
      ys.push(n.y, n.y + SLAB_H);
    });
    if (!xs.length) return;
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    // the furthest left the content may sit is "its right edge just inside the
    // canvas", and the mirror on each other side
    this.view.x = Math.min(w - pad - x0 * k, Math.max(pad - x1 * k, this.view.x));
    this.view.y = Math.min(h - pad - y0 * k, Math.max(pad - y1 * k, this.view.y));

    // That keeps the *space* the graph occupies on screen, which is not the same
    // as keeping a box on screen: drag towards a corner and you can end up looking
    // at the empty part of the bounding rectangle. So if nothing is actually
    // visible, pull back until the nearest box is.
    var v = this.view, seen = false, best = null, bd = Infinity;
    var cx = w / 2, cy = h / 2;
    this.nodes.forEach(function (n) {
      if (n.dummy || seen) return;
      var sx = v.x + n.x * k, sy = v.y + n.y * k;
      if (sx + SLAB_W * k > pad && sx < w - pad && sy + SLAB_H * k > pad && sy < h - pad) {
        seen = true;
        return;
      }
      var d = Math.abs(sx - cx) + Math.abs(sy - cy);
      if (d < bd) { bd = d; best = n; }
    });
    if (seen || !best) return;
    v.x = Math.min(w - pad - best.x * k, Math.max(pad - (best.x + SLAB_W) * k, v.x));
    v.y = Math.min(h - pad - best.y * k, Math.max(pad - (best.y + SLAB_H) * k, v.y));
  };

  Dag.prototype.bandAt = function (px) {
    if (!this.bands) return null;
    var wx = (px - this.view.x) / this.view.k;
    for (var i = 0; i < this.bands.length; i++) {
      var b = this.bands[i];
      if (b.id && wx >= b.x0 && wx < b.x1) return b;
    }
    return null;
  };

  Dag.prototype._wire = function () {
    var self = this, drag = null;
    this.cv.addEventListener('pointerdown', function (e) {
      self.cv.setPointerCapture(e.pointerId);
      drag = { x: e.offsetX, y: e.offsetY, vx: self.view.x, vy: self.view.y, moved: 0 };
    });
    this.cv.addEventListener('pointermove', function (e) {
      if (drag) {
        var dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
        drag.moved += Math.abs(dx) + Math.abs(dy);
        if (drag.moved > 4) { self.userMoved = true; self.glide = null; }
        self.view.x = drag.vx + dx;
        self.view.y = drag.vy + dy;
        self.clampView();
        return;
      }
      var n = self.hit(e.offsetX, e.offsetY);
      var id = n ? n.id : null;
      var band = n ? null : self.bandAt(e.offsetX);
      var bid = band ? band.id : null;
      if (bid !== self.hoverBand) self.hoverBand = bid;
      if (id !== self.hover) {
        self.hover = id;
        if (self.onHover) self.onHover(n, e);
      }
      self.cv.style.cursor = (id || bid) ? 'pointer' : 'grab';
    });
    this.cv.addEventListener('pointerup', function (e) {
      var quiet = drag && drag.moved < 5;
      drag = null;
      if (!quiet) return;
      var n = self.hit(e.offsetX, e.offsetY);
      if (!n) {
        // clicking the band behind the graph opens the stage it names
        var band = self.bandAt(e.offsetX);
        if (band && self.onStage) { self.onStage(band); return; }
      }
      self.selected = n ? n.id : null;
      if (self.onSelect) self.onSelect(n ? n.table : null, n ? n.maker : null);
    });
    this.cv.addEventListener('pointerleave', function () {
      drag = null;
      // the band under the cursor has to let go too, or the last stage you passed
      // over stays lit after you have left the graph
      self.hoverBand = null;
      if (self.hover) { self.hover = null; if (self.onHover) self.onHover(null); }
    });
    this.cv.addEventListener('wheel', function (e) {
      e.preventDefault();
      var f = Math.exp(-e.deltaY * 0.0016);
      var k2 = Math.max(0.28, Math.min(2.2, self.view.k * f));
      var r = k2 / self.view.k;
      self.view.x = e.offsetX - (e.offsetX - self.view.x) * r;
      self.view.y = e.offsetY - (e.offsetY - self.view.y) * r;
      self.view.k = k2;
      self.clampView();
      self.userMoved = true;
      self.glide = null;
    }, { passive: false });
    this.cv.addEventListener('dblclick', function () { self.fit(true); });
  };

  Dag.prototype.select = function (id) {
    this.selected = id;
  };

  global.QanatDag = {
    mount: function (canvas) {
      var d = new Dag(canvas);
      d.resize();
      var last = performance.now();
      (function loop(t) {
        var dt = Math.min(0.05, (t - last) / 1000);
        last = t;
        d.tick(dt);
        requestAnimationFrame(loop);
      })(last);
      // The canvas changes size without the window changing size -- opening the
      // PnL section shortens it. Watching only `window.resize` leaves the backing
      // store stale, and a stale backing store draws the graph twice.
      if (global.ResizeObserver) {
        var seen = { w: 0, h: 0 };
        new global.ResizeObserver(function () {
          var w = canvas.clientWidth, h = canvas.clientHeight;
          if (!w || !h || (w === seen.w && h === seen.h)) return;
          seen = { w: w, h: h };
          d.resize();
          d.recentre();
        }).observe(canvas);
      } else {
        global.addEventListener('resize', function () { d.resize(); d.recentre(); });
      }
      return d;
    },
    SLAB: { W: SLAB_W, H: SLAB_H },
  };
})(window);
