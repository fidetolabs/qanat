/* `backtest_conditions`, rendered as the question it is.
 *
 * That tool exists for one reason, and `docs/agents.md` says it outright: "it is
 * the one tool whose job is to make the agent stop and ask." It returns a field
 * called `ask_the_person_for`. A console that answers a tool designed to ask by
 * printing its JSON has thrown the design away.
 *
 * So when it is called, the thread grows a form. The window, the rebalance, the
 * universe and the split are the four things that change the answer, and each one
 * arrives with what the project already knows -- the dates the data actually
 * covers, the gap the alpha itself asked for. Filling it in runs the replay.
 */
(function () {
  'use strict';

  function el(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function day(s) { return s ? String(s).slice(0, 10) : ''; }

  async function api(path, opts) {
    var r = await fetch(path, opts || undefined);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  var OPEN = false;

  //  Only one card at a time: the agent may call the tool more than once while it
  //  thinks, and a thread stacked with identical forms is worse than none.
  async function offer() {
    if (OPEN) return;
    var host = el('cr-thread');
    if (!host) return;
    var c;
    try {
      c = await api('/api/backtest/conditions');
    } catch (e) { return; }

    var alphas = (c.alphas || []).map(function (a) {
      return typeof a === 'string' ? { id: a, name: a } : a;
    });
    if (!alphas.length) return;
    OPEN = true;

    var span = c.window && c.window.data_available;
    var from = day(span && span.earliest) || '';
    var to = day(span && span.latest) || '';
    var reb = (alphas[0] && alphas[0].rebalance) || (c.rebalance && c.rebalance.default) || '5d';

    var html =
      '<div class="askcard" id="cond-card">' +
      '<div class="h">it needs these before the number means anything</div>' +
      '<div class="f"><label for="cd-alpha">alpha</label>' +
        '<select id="cd-alpha">' + alphas.map(function (a) {
          return '<option value="' + esc(a.id) + '">' + esc(a.name || a.id) + '</option>';
        }).join('') + '</select></div>' +
      '<div class="f"><label for="cd-from">from</label>' +
        '<input id="cd-from" value="' + esc(from) + '"></div>' +
      '<div class="f"><label for="cd-to">to</label>' +
        '<input id="cd-to" value="' + esc(to) + '"></div>' +
      '<div class="f"><label for="cd-reb">rebalance</label>' +
        '<input id="cd-reb" value="' + esc(reb) + '"></div>' +
      '<div class="f"><label for="cd-split">split</label>' +
        '<input id="cd-split" placeholder="first out-of-sample date"></div>' +
      '<p class="why">The data covers ' + esc(from) + ' → ' + esc(to) + '. A split reports the ' +
        'run twice: the half the settings were chosen on, and the half that was never ' +
        'allowed to argue back.</p>' +
      '<div class="go"><button type="button" class="btn go" id="cd-run">run it</button>' +
        '<button type="button" class="btn" id="cd-skip">not now</button>' +
        '<span class="faint" id="cd-say"></span></div>' +
      '</div>';

    var first = host.querySelector('.crempty');
    if (first) first.remove();
    host.insertAdjacentHTML('beforeend', html);
    host.scrollTop = host.scrollHeight;

    el('cd-skip').onclick = close;
    el('cd-run').onclick = run;
  }

  function close() {
    var card = el('cond-card');
    if (card) card.remove();
    OPEN = false;
  }

  async function run() {
    var say = el('cd-say'), btn = el('cd-run');
    var body = {
      alpha: el('cd-alpha').value,
      from: el('cd-from').value.trim(),
      to: el('cd-to').value.trim(),
      rebalance: el('cd-reb').value.trim() || null,
    };
    var split = el('cd-split').value.trim();
    if (split) body.split = split;
    if (!body.from || !body.to) { say.textContent = 'it needs a window'; return; }

    btn.disabled = true;
    say.textContent = 'replaying…';
    try {
      await api('/api/backtest', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (e) {
      btn.disabled = false;
      say.innerHTML = '<span class="down">' + esc(e.message) + '</span>';
      return;
    }
    close();
    // the trace carries the POST, so the stage moves to the results on its own
    if (window.QANAT) window.QANAT.poll();
    if (window.repaintBook) window.repaintBook();
  }

  window.Conditions = { offer: offer, close: close };
})();
