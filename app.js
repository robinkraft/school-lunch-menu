/* Love Elementary Lunch Menu — plain static site, no build step.
   Menus live in data/*.json (public district data). Picks are saved
   on each device (localStorage) in Phase 1; Phase 2 moves them to
   Supabase under a family code (see supabase-config.js). */
(function () {
  'use strict';

  var PICKS_KEY = 'love-lunch-picks-v1';
  var MONTHS_URL = 'data/months.json';
  var WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  var state = {
    meal: 'lunch', // Lunch selected by default; Breakfast tab comes first.
    manifest: null,
    data: {},      // month id -> { lunch: {...}|null, breakfast: {...}|null }
    picks: loadPicks()
  };

  function $(id) { return document.getElementById(id); }
  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }
  function parseDate(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function fmtDate(s) {
    var d = parseDate(s);
    return WEEKDAYS[d.getDay()] + ' ' + MONTHS[d.getMonth()] + ' ' + d.getDate();
  }
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function loadPicks() {
    try { return JSON.parse(localStorage.getItem(PICKS_KEY) || '{}'); } catch (e) { return {}; }
  }
  function savePicks() {
    try { localStorage.setItem(PICKS_KEY, JSON.stringify(state.picks)); } catch (e) { /* private mode etc. */ }
  }
  function isPicked(meal, date, item) {
    var m = state.picks[meal] || {};
    var list = m[date] || [];
    return list.indexOf(item) !== -1;
  }
  function togglePick(meal, date, item) {
    state.picks[meal] = state.picks[meal] || {};
    var list = state.picks[meal][date] || [];
    var i = list.indexOf(item);
    if (i === -1) list.push(item); else list.splice(i, 1);
    if (list.length) state.picks[meal][date] = list; else delete state.picks[meal][date];
    savePicks();
    render();
  }

  function fetchJson(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function loadAll(cacheBust) {
    var url = cacheBust ? MONTHS_URL + '?v=' + Date.now() : MONTHS_URL;
    return fetchJson(url).then(function (manifest) {
      state.manifest = manifest;
      var jobs = [];
      manifest.months.forEach(function (m) {
        state.data[m.id] = state.data[m.id] || {};
        ['lunch', 'breakfast'].forEach(function (meal) {
          if (m[meal]) {
            var fileUrl = cacheBust ? m[meal] + '?v=' + Date.now() : m[meal];
            jobs.push(fetchJson(fileUrl).then(function (d) { state.data[m.id][meal] = d; })
              .catch(function () { state.data[m.id][meal] = null; }));
          } else {
            state.data[m.id][meal] = null;
          }
        });
      });
      return Promise.all(jobs);
    });
  }

  // Next school meal on or after today (skips no-school days).
  function nextMeal(meal) {
    var today = todayStr();
    var months = (state.manifest || { months: [] }).months;
    for (var k = 0; k < months.length; k++) {
      var doc = (state.data[months[k].id] || {})[meal];
      if (!doc || !doc.days) continue;
      var dates = Object.keys(doc.days).sort();
      for (var i = 0; i < dates.length; i++) {
        if (dates[i] < today) continue;
        var day = doc.days[dates[i]];
        if (day.no_school || !day.items || !day.items.length) continue;
        return { date: dates[i], day: day };
      }
    }
    return null;
  }

  function renderHero() {
    var el = $('hero');
    var meal = state.meal;
    var next = nextMeal(meal);
    if (!next) {
      el.innerHTML = '<p class="empty">No ' + meal + ' menu posted yet for the days ahead.</p>';
      return;
    }
    var label = next.date === todayStr() ? "Today's " + meal : 'Next ' + meal + ' · ' + fmtDate(next.date);
    var html = '<p class="kicker">' + esc(label) + '</p>'
      + '<p class="items">' + next.day.items.map(esc).join(' <span aria-hidden="true">·</span> ') + '</p>';
    if (next.day.salad_bar) html += '<p class="salad">Salad bar: ' + esc(next.day.salad_bar) + '</p>';
    el.innerHTML = html;
  }

  function dayHtml(date, day, meal) {
    var isToday = date === todayStr();
    var html = '<div class="day"><div class="date">' + esc(fmtDate(date))
      + (isToday ? '<span class="today-tag">Today</span>' : '') + '</div>';
    if (day.no_school) {
      html += '<p class="noschool">No school' + (day.note ? ' — ' + esc(day.note) : '') + '</p>';
    } else {
      (day.items || []).forEach(function (item) {
        var picked = isPicked(meal, date, item);
        html += '<button class="pick" aria-pressed="' + picked + '" data-meal="' + meal
          + '" data-date="' + date + '" data-item="' + esc(item) + '">'
          + '<span class="circle" aria-hidden="true">' + (picked ? '✓' : '') + '</span>'
          + '<span>' + esc(item) + '</span>'
          + (picked ? '<span class="pick-tag">Kids&rsquo; pick</span>' : '')
          + '</button>';
      });
      if (meal === 'lunch' && day.salad_bar) {
        html += '<p class="salad">Salad bar: ' + esc(day.salad_bar) + '</p>';
      }
    }
    return html + '</div>';
  }

  function renderMonths() {
    var wrap = $('months');
    var today = todayStr();
    var months = (state.manifest || { months: [] }).months;
    var html = '';
    months.forEach(function (m, idx) {
      var doc = (state.data[m.id] || {})[state.meal];
      var pdf = state.meal === 'lunch' ? m.lunch_pdf : m.breakfast_pdf;
      html += '<section class="month" id="month-' + m.id + '"><div class="month-head"><h2>'
        + esc(m.label) + '</h2>'
        + (pdf ? '<a class="pdf-link" href="' + esc(pdf) + '" target="_blank" rel="noopener">View original PDF</a>' : '')
        + '</div>';
      if (!doc || !doc.days) {
        html += '<div class="not-posted"><p>The district hasn&rsquo;t posted '
          + esc(m.label.toLowerCase()) + '&rsquo;s ' + state.meal
          + ' menu yet.</p><button class="btn" data-check-now="1">Check now</button></div>';
      } else {
        var dates = Object.keys(doc.days).sort().filter(function (d) { return d >= today; });
        if (!dates.length) {
          html += '<div class="not-posted"><p>No more school days in ' + esc(m.label) + '.</p></div>';
        } else {
          dates.forEach(function (d) { html += dayHtml(d, doc.days[d], state.meal); });
        }
      }
      if (idx > 0) {
        html += '<button class="btn ghost jump" data-jump-month="1">Back to this month</button>';
      }
      html += '</section>';
    });
    wrap.innerHTML = html;
  }

  function renderFoot() {
    var months = (state.manifest || { months: [] }).months;
    var foot = '';
    for (var i = 0; i < months.length; i++) {
      var doc = (state.data[months[i].id] || {})[state.meal];
      if (doc && doc.footer) { foot = doc.footer; break; }
    }
    $('foot').textContent = foot + ' Menus are district-wide, so this is the Love Elementary menu. Tap an entrée to mark it as a kids’ pick.';
  }

  function render() {
    $('tab-breakfast').setAttribute('aria-selected', state.meal === 'breakfast' ? 'true' : 'false');
    $('tab-lunch').setAttribute('aria-selected', state.meal === 'lunch' ? 'true' : 'false');
    renderHero();
    renderMonths();
    renderFoot();
  }

  function setStatus(msg) { $('status').textContent = msg || ''; }

  function init() {
    $('tab-breakfast').addEventListener('click', function () { state.meal = 'breakfast'; render(); });
    $('tab-lunch').addEventListener('click', function () { state.meal = 'lunch'; render(); });
    $('months').addEventListener('click', function (ev) {
      var pick = ev.target.closest('.pick');
      if (pick) { togglePick(pick.dataset.meal, pick.dataset.date, pick.dataset.item); return; }
      var check = ev.target.closest('[data-check-now]');
      if (check) {
        var section = check.closest('.month');
        var monthId = section ? section.id.replace('month-', '') : null;
        var monthLabel = monthId || '';
        (state.manifest.months || []).forEach(function (m) { if (m.id === monthId) monthLabel = m.label; });
        setStatus('Checking the district site…');
        loadAll(true).then(function () {
          render();
          var month = null;
          (state.manifest.months || []).forEach(function (m) { if (m.id === monthId) month = m; });
          var doc = month ? (state.data[month.id] || {})[state.meal] : null;
          var posted = doc && doc.days && Object.keys(doc.days).length > 0;
          setStatus('Checked just now — ' + (posted ? monthLabel + '’s ' + state.meal + ' menu is now posted.' : 'still not posted.'));
        }).catch(function () { setStatus('Could not check just now — try again in a bit.'); });
        return;
      }
      var jump = ev.target.closest('[data-jump-month]');
      if (jump) {
        var first = document.querySelector('.month');
        if (first) first.scrollIntoView({ behavior: 'smooth' });
      }
    });
    loadAll(false).then(render).catch(function () {
      $('hero').innerHTML = '<p class="empty">Could not load the menu — check your connection and reload.</p>';
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
