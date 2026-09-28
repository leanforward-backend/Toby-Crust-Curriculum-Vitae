(() => {
  const TRACK_COLORS = { fde: 'var(--t-fde)', ai: 'var(--t-ai)', fullstack: 'var(--t-fullstack)', xr: 'var(--t-xr)' };
  const MODES = [['any', 'Any'], ['hybrid', 'Hybrid'], ['onsite', 'Onsite'], ['flexible', 'Flexible']];
  const SECTIONS = [['sydney', 'Sydney roles', 'Every match in Sydney'], ['us', 'US pathway', 'Roles that can lead to a job in America']];
  const MINS = [[0, 'All'], [50, '50+'], [60, '60+']];
  const POSTED = [['any', 'Any time'], ['7d', 'Last 7 days'], ['today', 'New today']];
  const TABS = [['inbox', 'Inbox'], ['saved', 'Saved'], ['applied', 'Applied'], ['dismissed', 'Dismissed'], ['all', 'All']];
  const DEFAULT_UI = { section: 'sydney', tab: 'inbox', tracks: [], mode: 'any', min: 0, posted: 'any', sort: 'score', q: '' };

  const state = {
    jobs: new Map(), status: {}, runs: [], scan: { running: false }, loaded: false, failed: false,
    profile: { tracks: {}, location: 'Sydney', years: 3, maxYears: 4 }, pending: new Set(),
  };
  let ui = loadUI();
  if (location.hash === '#us') ui = { ...ui, section: 'us' };

  function loadUI() {
    try { return { ...DEFAULT_UI, ...JSON.parse(localStorage.getItem('radar.ui') || '{}') }; } catch { return { ...DEFAULT_UI }; }
  }
  function saveUI() {
    try { localStorage.setItem('radar.ui', JSON.stringify(ui)); } catch { /* storage unavailable */ }
  }

  const $ = (id) => document.getElementById(id);
  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'style') n.style.cssText = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
    return n;
  }
  const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);

  // ---------- dates (Sydney calendar days) ----------
  const SYD = new Intl.DateTimeFormat('en-CA', { timeZone: 'Australia/Sydney', year: 'numeric', month: '2-digit', day: '2-digit' });
  const today = () => SYD.format(new Date());
  const utcDay = (ymd) => { const [y, m, d] = ymd.split('-').map(Number); return Date.UTC(y, m - 1, d); };
  function daysSince(ymd) {
    if (!ymd || !/^\d{4}-\d{2}-\d{2}/.test(ymd)) return null;
    return Math.round((utcDay(today()) - utcDay(ymd.slice(0, 10))) / 86400000);
  }
  function fmtDay(ymd) {
    return new Date(utcDay(ymd)).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', timeZone: 'UTC' });
  }
  function ago(ymd) {
    const d = daysSince(ymd);
    if (d == null) return '';
    if (d <= 0) return 'today';
    if (d === 1) return 'yesterday';
    return d < 30 ? `${d}d ago` : fmtDay(ymd);
  }

  const band = (s) => (s >= 60 ? 'strong' : s >= 50 ? 'good' : 'stretch');
  const BAND_LABEL = { strong: 'Strong', good: 'Good', stretch: 'Stretch' };
  const statusOf = (id) => state.status[id]?.state || null;
  const trackInfo = (k) => state.profile.tracks[k] || { label: k, cv: null };

  function inTab(j, tab) {
    const st = statusOf(j.id);
    if (tab === 'inbox') return !st;
    if (tab === 'saved') return st === 'saved';
    if (tab === 'applied') return st === 'applied' || st === 'interview';
    if (tab === 'dismissed') return st === 'dismissed';
    return true;
  }
  function passes(j, skip) {
    if (skip !== 'track' && ui.tracks.length && !ui.tracks.includes(j.track)) return false;
    if (ui.mode !== 'any' && j.mode !== ui.mode) return false;
    if ((j.score || 0) < ui.min) return false;
    if (ui.posted === 'today' && j.found !== today()) return false;
    if (ui.posted === '7d') { const d = daysSince(j.posted || j.found); if (d == null || d > 7) return false; }
    if (ui.q) {
      const hay = [j.title, j.company, j.location, j.why, j.aboutRole, j.aboutCompany, ...(j.skills || [])].join(' ').toLowerCase();
      if (!hay.includes(ui.q.toLowerCase())) return false;
    }
    return true;
  }
  const inSection = (j, section = ui.section) => section !== 'us' || !!j.usPath;
  const usRank = (j) => (j.usPath?.level === 'strong' ? 1 : 0);
  const byScore = (a, b) => (ui.section === 'us' ? usRank(b) - usRank(a) : 0)
    || (b.score || 0) - (a.score || 0) || String(b.posted || '').localeCompare(String(a.posted || ''));
  const byNew = (a, b) => String(b.found || '').localeCompare(String(a.found || ''))
    || String(b.posted || '').localeCompare(String(a.posted || '')) || (b.score || 0) - (a.score || 0);

  function setUI(patch) { ui = { ...ui, ...patch }; saveUI(); render(); }

  // ---------- controls ----------
  function chip(label, pressed, onClick, extra) {
    return el('button', { class: 'chip', type: 'button', 'aria-pressed': String(pressed), onclick: onClick, style: extra?.dot ? `--dot:${extra.dot}` : null },
      extra?.dot ? el('span', { class: 'dot' }) : null, label,
      extra?.count != null ? el('span', { class: 'c', text: String(extra.count) }) : null);
  }

  function renderSections(everything) {
    const active = everything.filter((j) => statusOf(j.id) !== 'dismissed');
    $('sections').replaceChildren(...SECTIONS.map(([k, label, hint]) => el('button', {
      class: `section${k === 'us' ? ' us' : ''}`, type: 'button', 'aria-pressed': String(ui.section === k), onclick: () => setUI({ section: k }),
    }, el('span', { class: 'sl', text: label }), el('span', { class: 'sc', text: String(active.filter((j) => inSection(j, k)).length) }),
    el('span', { class: 'sh', text: hint }))));
    $('usintro').hidden = ui.section !== 'us';
  }

  function renderControls(all) {
    $('tabs').replaceChildren(...TABS.map(([k, label]) => el('button', {
      class: 'tab', role: 'tab', type: 'button', 'aria-selected': String(ui.tab === k), onclick: () => setUI({ tab: k }),
    }, label, el('span', { class: 'c', text: String(all.filter((j) => inTab(j, k)).length) }))));

    const tabbed = all.filter((j) => inTab(j, ui.tab));
    const ft = $('f-track');
    ft.replaceChildren(ft.firstElementChild, ...Object.keys(TRACK_COLORS).map((k) => chip(trackInfo(k).label, ui.tracks.includes(k), () => {
      setUI({ tracks: ui.tracks.includes(k) ? ui.tracks.filter((x) => x !== k) : [...ui.tracks, k] });
    }, { dot: TRACK_COLORS[k], count: tabbed.filter((j) => j.track === k && passes(j, 'track')).length })));

    for (const [id, opts, key] of [['f-mode', MODES, 'mode'], ['f-min', MINS, 'min'], ['f-posted', POSTED, 'posted']]) {
      const g = $(id);
      g.replaceChildren(g.firstElementChild, ...opts.map(([k, label]) => chip(label, ui[key] === k, () => setUI({ [key]: k }))));
    }
    if (document.activeElement !== $('q')) $('q').value = ui.q;
    $('sort').value = ui.sort;
  }

  // ---------- summary ----------
  function renderSummary(all) {
    const t = today();
    const active = all.filter((j) => statusOf(j.id) !== 'dismissed');
    $('k-new').textContent = String(active.filter((j) => j.found === t).length);
    $('k-strong').textContent = String(active.filter((j) => (j.score || 0) >= 60).length);
    $('k-saved').textContent = String(all.filter((j) => statusOf(j.id) === 'saved').length);
    $('k-applied').textContent = String(all.filter((j) => ['applied', 'interview'].includes(statusOf(j.id))).length);

    const p = state.profile;
    $('eyebrow').textContent = `Toby Crust · ${p.location} · up to ${p.maxYears} years' experience`;
    $('maxYears').textContent = String(p.maxYears);

    const meta = $('meta');
    const last = state.runs[state.runs.length - 1];
    if (state.failed) {
      meta.textContent = 'Can’t reach the Job Radar server. Start it with: python3 job-radar/server.py';
    } else if (!state.loaded) {
      meta.textContent = 'Loading your job list…';
    } else {
      const bits = [el('span', null, 'Scans daily at 7am. ')];
      if (state.scan.running) bits.push(el('span', null, el('b', { text: 'Scanning now…' }), ' '));
      if (last?.runAt) {
        const when = new Date(last.runAt).toLocaleString('en-AU', { timeZone: 'Australia/Sydney', weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
        bits.push(el('span', null, 'Last scan ', el('b', { text: when }), ` found ${last.added ?? 0} new role${last.added === 1 ? '' : 's'}. `));
      }
      bits.push(el('span', null, el('b', { text: String(active.length) }), ' roles tracked.'));
      meta.replaceChildren(...bits);
    }
    const btn = $('refresh');
    btn.disabled = state.failed || state.scan.running;
    btn.textContent = state.scan.running ? 'Scanning…' : 'Scan now';

    // new roles per day, last 14 days
    const svg = $('spark');
    const NS = 'http://www.w3.org/2000/svg';
    const days = [];
    for (let i = 13; i >= 0; i--) days.push(SYD.format(new Date(Date.now() - i * 86400000)));
    const byDay = new Map(state.runs.map((r) => [r.date, r.added || 0]));
    const vals = days.map((d) => (byDay.has(d) ? byDay.get(d) : null));
    const max = Math.max(4, ...vals.filter((v) => v != null));
    const W = 280, H = 38, gap = 4, bw = (W - gap * 13) / 14;
    const mk = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
    svg.replaceChildren(mk('line', { x1: 0, x2: W, y1: H - 0.5, y2: H - 0.5, stroke: 'var(--rule)', 'stroke-width': 1 }));
    vals.forEach((v, i) => {
      const h = v == null ? 2 : Math.max(2, (v / max) * (H - 4));
      const r = mk('rect', { x: i * (bw + gap), y: H - 1 - h, width: bw, height: h, fill: v == null ? 'var(--rule-soft)' : i === 13 ? 'var(--accent)' : 'var(--ink-soft)' });
      const tt = document.createElementNS(NS, 'title');
      tt.textContent = `${fmtDay(days[i])}: ${v == null ? 'no scan' : `${v} new`}`;
      r.append(tt);
      svg.append(r);
    });
    $('spark-from').textContent = fmtDay(days[0]);
  }

  // ---------- list ----------
  function jobRow(j) {
    const st = statusOf(j.id);
    const tr = trackInfo(j.track);
    const b = band(j.score || 0);
    const url = safeUrl(j.url);
    const busy = state.pending.has(j.id);
    const act = (key, label) => el('button', {
      class: 'btn', type: 'button', 'data-act': key, 'aria-pressed': String(st === key), disabled: busy,
      onclick: () => setStatus(j.id, key),
    }, label);
    const stLabel = st === 'applied' && state.status[j.id]?.updatedAt
      ? `Applied ${fmtDay(SYD.format(new Date(state.status[j.id].updatedAt)))}`
      : st && st[0].toUpperCase() + st.slice(1);
    const unseen = daysSince(j.lastSeen);
    const us = j.usPath;
    const usBlock = us && ui.section === 'us'
      ? el('div', { class: `uspath ${us.level}` },
        el('span', { class: 'uslabel', text: us.level === 'strong' ? 'Strong US path' : 'Possible US path' }),
        el('ul', null, us.reasons.map((r) => el('li', { text: r }))))
      : null;
    return el('li', { class: `job${st === 'dismissed' ? ' is-dismissed' : ''}` },
      el('div', { class: `score ${b}` },
        el('span', { class: 'num', text: String(j.score ?? '–') }),
        el('span', { class: 'band', text: BAND_LABEL[b] }),
        el('span', { class: 'meter', 'aria-hidden': 'true' }, el('i', { style: `width:${Math.max(0, Math.min(100, j.score || 0))}%` }))),
      el('div', { class: 'body' },
        el('div', { class: 'toprow' },
          el('span', { class: 'track', style: `--tc:${TRACK_COLORS[j.track] || 'var(--ink-soft)'}`, text: tr.label }),
          j.found === today() && !st ? el('span', { class: 'pill new', text: 'New today' }) : null,
          st ? el('span', { class: `pill ${st}`, text: stLabel }) : null,
          us && ui.section !== 'us' ? el('span', { class: 'pill us', title: us.reasons.join(' '), text: 'US path' }) : null),
        el('h3', null, url ? el('a', { href: url, target: '_blank', rel: 'noopener noreferrer', text: j.title }) : j.title),
        el('div', { class: 'sub' },
          el('span', { class: 'co', text: j.company || 'Unknown company' }),
          j.location ? el('span', { text: j.location }) : null,
          j.mode ? el('span', { text: j.mode[0].toUpperCase() + j.mode.slice(1) }) : null,
          j.posted || j.found ? el('span', { text: `Posted ${ago(j.posted || j.found)}` }) : null,
          j.salary ? el('span', { class: 'sal', text: j.salary }) : null,
          j.source ? el('span', { text: `via ${j.source}` }) : null),
        j.aboutRole || j.aboutCompany ? el('dl', { class: 'about' },
          j.aboutRole ? [el('dt', { text: 'The role' }), el('dd', { text: j.aboutRole })] : null,
          j.aboutCompany ? [el('dt', { text: 'The company' }), el('dd', { text: j.aboutCompany })] : null) : null,
        el('div', { class: 'cvpick' },
          el('span', { class: 'cvtag', text: 'Send' }),
          tr.cv ? el('a', { href: tr.cv, target: '_blank', text: `${tr.cvName || `${tr.label} CV`} ↗` }) : el('b', { text: tr.cvName || `${tr.label} CV` }),
          j.cvWhy ? el('span', { class: 'cvwhy', text: j.cvWhy }) : null),
        usBlock,
        j.why ? el('p', { class: 'why', text: j.why }) : null,
        j.watch ? el('p', { class: 'watch' }, el('b', { text: 'Watch' }), j.watch) : null,
        (j.skills || []).length ? el('div', { class: 'tags' }, j.skills.slice(0, 8).map((s) => el('span', { text: s }))) : null),
      el('div', { class: 'act' },
        el('div', { class: 'btns' }, act('saved', 'Save'), act('applied', 'Applied'), act('dismissed', 'Dismiss')),
        url ? el('a', { class: 'open', href: url, target: '_blank', rel: 'noopener noreferrer', text: 'Open posting ↗' }) : null,
        unseen != null && unseen >= 3 ? el('div', { class: 'seen', text: `Not seen in ${unseen} days, may be closed` }) : null));
  }

  function render() {
    const everything = [...state.jobs.values()];
    const all = everything.filter((j) => inSection(j));
    renderSections(everything);
    renderControls(all);
    renderSummary(all);
    const list = all.filter((j) => inTab(j, ui.tab) && passes(j)).sort(ui.sort === 'new' ? byNew : byScore);
    const filtersOn = ui.tracks.length || ui.mode !== 'any' || ui.min || ui.posted !== 'any' || ui.q;
    $('count').replaceChildren(
      el('span', { text: state.loaded ? `${list.length} role${list.length === 1 ? '' : 's'} shown` : '' }),
      filtersOn ? el('button', { class: 'linkbtn', type: 'button', text: 'Clear filters', onclick: () => setUI({ tracks: [], mode: 'any', min: 0, posted: 'any', q: '' }) }) : el('span'));
    $('jobs').replaceChildren(...list.map(jobRow));

    const empty = $('empty');
    empty.hidden = list.length > 0;
    if (list.length) return;
    let head = 'Nothing here yet', body = 'Roles you mark will show up here.';
    if (state.failed) { head = 'Job Radar server isn’t running'; body = 'Start it with python3 job-radar/server.py, then reload this page.'; }
    else if (!state.loaded) { head = 'Loading roles…'; body = ''; }
    else if (!all.length && ui.section === 'us') { head = 'No US routes yet'; body = 'None of your current matches has a route to the US. New ones are checked every morning.'; }
    else if (!all.length) body = 'Run a scan to fill this list.';
    else if (filtersOn) { head = 'No roles match these filters'; body = 'Loosen a filter or clear them all.'; }
    else if (ui.tab === 'inbox') { head = 'Inbox zero'; body = 'Every role has been saved, applied to or dismissed. New ones arrive each morning.'; }
    empty.replaceChildren(el('strong', { text: head }), body ? el('span', { text: body }) : null);
  }

  // ---------- server ----------
  async function api(path, body) {
    const res = await fetch(path, body === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok && res.status !== 409) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  async function load() {
    try {
      const d = await api('/api/jobs');
      state.jobs = new Map(d.jobs.map((j) => [j.id, j]));
      state.status = d.status || {};
      state.runs = d.runs || [];
      state.scan = d.scan || { running: false };
      state.profile = d.profile || state.profile;
      state.loaded = true;
      state.failed = false;
    } catch {
      state.failed = true;
    }
    render();
    if (state.scan.running) pollScan();
  }

  let toastTimer;
  function toast(msg, undo) {
    const t = $('toast');
    t.replaceChildren(el('span', { text: msg }), undo ? el('button', { type: 'button', text: 'Undo', onclick: () => { t.hidden = true; undo(); } }) : null);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
  }

  async function writeStatus(id, next) {
    state.pending.add(id); render();
    try {
      const d = await api('/api/status', { id, state: next });
      state.status = d.status;
      return true;
    } catch (e) {
      toast(`Couldn’t save that change. ${e.message}`);
      return false;
    } finally {
      state.pending.delete(id); render();
    }
  }

  async function setStatus(id, key) {
    if (state.pending.has(id)) return;
    const prev = statusOf(id);
    const next = prev === key ? null : key;
    const ok = await writeStatus(id, next);
    if (ok && next === 'dismissed') toast('Dismissed. It won’t be suggested again.', () => writeStatus(id, prev));
  }

  let polling = false;
  async function pollScan() {
    if (polling) return;
    polling = true;
    try {
      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await api('/api/scan');
        state.scan = s;
        if (!s.running) break;
        render();
      }
      await load();
      toast(state.scan.lastExit === 0 ? 'Scan finished.' : 'The scan stopped with an error. See job-radar/data/scan.log.');
    } catch {
      toast('Lost contact with the server during the scan.');
    } finally {
      polling = false;
    }
  }

  $('refresh').addEventListener('click', async () => {
    try {
      state.scan = await api('/api/scan', {});
      render();
      pollScan();
    } catch (e) {
      toast(`Couldn’t start a scan. ${e.message}`);
    }
  });
  let qTimer;
  $('q').addEventListener('input', (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => setUI({ q: e.target.value.trim() }), 150); });
  $('sort').addEventListener('change', (e) => setUI({ sort: e.target.value }));
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !polling) load(); });

  render();
  load();
})();
