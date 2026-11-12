'use strict';

// Front end for the Remixfox API (/api, see /api/docs).

const $ = (sel) => document.querySelector(sel);

const state = {
  reels: [],
  competitors: [],
  selectedId: null,
  editingId: null,
  view: 'home',
  loaded: false,
  gridShown: false,
  transcribing: new Set(), // reel ids whose transcript is being fetched
  plan: [], // reels in the content plan
  ideas: [], // Home's swipe deck: reels neither planned nor skipped, most viewed first
  swipes: [], // { reel, action } for undo
  deciding: false,
  sort: 'views', // the feed: 'views' (most viewed first) or 'latest'
  me: null, // the signed-in account (GET /api/me)
  offer: null, // the plans on offer (GET /api/billing/plans)
  interval: null, // the billing period picked in Settings: 'month' or 'year'
  accounts: [], // Reels: competitor ids whose reels to show (none: the 20 latest of all)
  stats: null, // the admin panel (GET /api/admin/stats)
};

// Content plan statuses, as emoji the user picks from.
const PLAN = {
  todo: { emoji: '❌', label: 'Not started', hint: 'Nothing is ready yet' },
  ready: { emoji: '⏳', label: 'Ready to film', hint: 'Script edited, waiting to be filmed' },
  posted: { emoji: '✅', label: 'Posted', hint: 'Filmed and posted' },
};
const FIELDS = {
  hook: { label: 'Hook', placeholder: 'The first line that stops the scroll', rows: 2, max: 5000 },
  script: { label: 'Main script', placeholder: 'What you say, shot by shot', rows: 5, max: 20000 },
  cta: { label: 'Call to action', placeholder: 'What viewers should do next', rows: 2, max: 5000 },
};

// ---------- API ----------
async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail;
    const error = new Error(typeof detail === 'string' ? detail : `Something went wrong (${res.status})`);
    error.status = res.status;
    if (res.status === 401) location.replace('/welcome.html'); // signed out, or the session expired
    if (res.status === 402) showSubscription();
    throw error;
  }
  return data;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------- Helpers ----------
function formatViews(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return Math.round(n / 1e3) + 'K';
  return String(n);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return '';
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const dateFormat = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
function formatDate(iso) {
  return iso ? dateFormat.format(new Date(iso)) : '';
}

const clockFormat = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
function formatClock(iso) {
  return clockFormat.format(new Date(iso));
}

function formatUsd(n) {
  return `$${n < 0.1 ? n.toFixed(3).replace(/0$/, '') : n.toFixed(2)}`;
}

const languageNames = (() => {
  try { return new Intl.DisplayNames(['en-GB'], { type: 'language' }); } catch (e) { return null; }
})();
function languageName(code) {
  if (!code) return 'another language';
  try { return languageNames ? languageNames.of(code) : code; } catch (e) { return code; }
}

function iconHtml(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function viewsHtml(n) {
  return `${iconHtml('play')}${formatViews(n)}`;
}

// A reel's title: the AI's hook (the opening words), else the caption's first line.
function ideaTitle(reel) {
  return (reel.translation_hook || '').trim() || reelTitle(reel);
}

// In the content plan the title is your own hook, once you have one.
function planTitle(reel) {
  return (reel.hook || '').trim() || ideaTitle(reel);
}

function reelTitle(reel) {
  const firstLine = (reel.caption_translation || reel.caption || '').split('\n')[0].trim();
  if (!firstLine) return `Reel by @${reel.username}`;
  return firstLine.length > 80 ? firstLine.slice(0, 79) + '…' : firstLine;
}

const relativeTime = new Intl.RelativeTimeFormat('en-GB', { numeric: 'auto' });
function timeAgo(iso) {
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  const units = [['day', 86400], ['hour', 3600], ['minute', 60]];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return relativeTime.format(Math.round(seconds / size), unit);
  }
  return 'just now';
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

// Sets HTML only when it changed, so nothing is rebuilt (or flickers) needlessly.
function setHtml(el, html) {
  if (el._html !== html) {
    el._html = html;
    el.innerHTML = html;
  }
}

// ---------- Toast (click to close) ----------
let toastTimer;
function toast(message, { error = false } = {}) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('is-error', error);
  el.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, Math.min(8000, Math.max(2600, message.length * 50)));
}
function hideToast() {
  clearTimeout(toastTimer);
  $('#toast').classList.remove('is-visible');
}

function storageGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ }
}

// Accepts "username", "@username" or an instagram.com profile link (the API checks it too).
function parseInstagramHandle(raw) {
  const value = raw.trim();
  if (!value) return null;
  let handle = value;
  const urlMatch = value.match(/^(?:https?:\/\/)?(?:www\.|m\.)?instagram\.com\/([^/?#]+)/i);
  if (urlMatch) handle = urlMatch[1];
  handle = handle.replace(/^@/, '');
  return /^[A-Za-z0-9._]{1,30}$/.test(handle) ? handle.toLowerCase() : null;
}

// ---------- Covers: shown only once loaded, then faded in ----------
function setCover(el, url) {
  const src = url || '';
  if (el.dataset.src === src) return;
  el.dataset.src = src;
  el.classList.remove('is-loaded');
  if (!src) {
    el.style.backgroundImage = '';
    return;
  }
  const img = new Image();
  img.onload = () => {
    if (el.dataset.src !== src) return; // another reel was chosen meanwhile
    el.style.backgroundImage = `url("${src.replace(/["\\\n]/g, '')}")`;
    void el.offsetWidth; // apply the image first, so the opacity change animates
    el.classList.add('is-loaded');
  };
  img.src = src;
}

// The preview fades the new cover in over the old one, so it never flashes dark.
function crossfadeCover(container, url) {
  const src = url || '';
  if (container.dataset.src === src) return;
  container.dataset.src = src;
  const [under, top] = container.querySelectorAll('.cover-img');
  if (!src) {
    under.classList.remove('is-loaded');
    top.classList.remove('is-loaded');
    return;
  }
  const img = new Image();
  img.onload = () => {
    if (container.dataset.src !== src) return;
    under.classList.remove('is-loaded');
    under.style.backgroundImage = `url("${src.replace(/["\\\n]/g, '')}")`;
    container.insertBefore(under, top.nextSibling); // it becomes the top layer
    void under.offsetWidth;
    under.classList.add('is-loaded');
    setTimeout(() => { if (container.dataset.src === src) top.classList.remove('is-loaded'); }, 400);
  };
  img.src = src;
}

// ---------- Ranking by views (among the reels in the feed) ----------
function ranks() {
  const order = [...state.reels].sort((a, b) => b.views - a.views || a.id - b.id);
  return { byId: new Map(order.map((r, i) => [r.id, i + 1])), total: order.length, top: order[0] ? order[0].views : 0 };
}

const MEDALS = ['🥇', '🥈', '🥉'];
function rankBadge(rank, total, views) {
  if (!rank) return '';
  const last = total > 3 && rank === total;
  const label = rank <= 3 ? `${MEDALS[rank - 1]} #${rank}` : last ? `#${rank} · lowest` : `#${rank}`;
  const tone = rank <= 3 ? ' is-top' : last ? ' is-last' : '';
  return `<span class="rank-badge${tone}" title="#${rank} of ${total} by views (${views.toLocaleString('en-GB')})">${label}</span>`;
}

function displayReels() {
  if (state.sort === 'latest') return state.reels;
  const { byId } = ranks();
  return [...state.reels].sort((a, b) => byId.get(a.id) - byId.get(b.id));
}

function syncSortButtons() {
  document.querySelectorAll('#sort-toggle [data-sort]').forEach((btn) => {
    btn.setAttribute('aria-pressed', String(btn.dataset.sort === state.sort));
  });
}

function setSort(sort) {
  state.sort = sort;
  storageSet('sort', sort);
  syncSortButtons();
  renderGrid();
}

// ---------- Reels ----------
function findReel(id) {
  return state.reels.find((r) => r.id === id) || state.plan.find((r) => r.id === id) ||
    state.ideas.find((r) => r.id === id) || null;
}

// One place to apply a changed reel: feed, preview, content plan and editor.
function upsertReel(reel) {
  state.reels = state.reels.map((r) => (r.id === reel.id ? reel : r));
  const inPlan = state.plan.some((r) => r.id === reel.id);
  if (reel.plan_status) {
    state.plan = inPlan ? state.plan.map((r) => (r.id === reel.id ? reel : r)) : [reel, ...state.plan];
  } else {
    state.plan = state.plan.filter((r) => r.id !== reel.id);
  }
  sortPlan();
  state.ideas = state.ideas.map((r) => (r.id === reel.id ? reel : r));
  renderGrid();
  renderPreview();
  renderPlan();
  if ($('#editor').open && state.editingId === reel.id) renderEditorPlan(reel);
}

// The reel the preview opens on: the last one chosen here, else the last one
// edited (to pick up where you left off), else the newest.
function defaultSelection() {
  const saved = Number(storageGet('selectedReel'));
  if (findReel(saved)) return saved;
  const edited = state.reels
    .filter((r) => r.edited_at)
    .sort((a, b) => new Date(b.edited_at) - new Date(a.edited_at))[0];
  return (edited || state.reels[0] || { id: null }).id;
}

function reelsQuery() {
  if (!state.accounts.length) return '/reels?limit=20';
  return `/reels?limit=500&${state.accounts.map((id) => `competitor_id=${id}`).join('&')}`;
}

async function loadReels() {
  try {
    state.reels = await api(reelsQuery());
  } catch (e) {
    return false;
  }
  state.loaded = true;
  if (!findReel(state.selectedId)) state.selectedId = defaultSelection();
  renderGrid();
  renderPreview();
  return true;
}

// The free server sleeps when idle: keep trying quietly, with a note instead of errors.
async function firstLoad() {
  const note = $('#loading-note');
  const slow = setTimeout(() => { note.hidden = false; }, 2500);
  for (let attempt = 0; !(await loadMe()) || !(await loadReels()); attempt++) {
    note.hidden = false;
    await sleep(Math.min(2000 * 2 ** attempt, 10000));
  }
  clearTimeout(slow);
  note.hidden = true;
}

function cardBadges(reel, rank) {
  return rankBadge(rank.byId.get(reel.id), rank.total, reel.views) +
    (reel.translated ? '<span class="card-badge is-done">Translated</span>' : '');
}

function createCard(reel) {
  const li = document.createElement('li');
  li.className = 'reel';
  li.dataset.id = reel.id;
  li.innerHTML = `
    <button class="reel-cover cover" type="button">
      <span class="cover-img"></span>
      <span class="card-badge-slot"></span>
      <span class="views"></span>
      <span class="pop-bar" aria-hidden="true"><i></i></span>
    </button>
    <p class="reel-title"></p>
    <button class="card-plan" type="button" data-plan="${reel.id}"></button>
    <button class="btn btn-remix" type="button" data-remix="${reel.id}">${iconHtml('zap')}Remix</button>`;
  li.addEventListener('animationend', () => li.classList.remove('is-entering'), { once: true });
  return li;
}

function updateCard(li, reel, rank) {
  li.classList.toggle('is-selected', reel.id === state.selectedId);
  const cover = li.querySelector('.reel-cover');
  const label = `Preview: ${reelTitle(reel)}, ${formatViews(reel.views)} views`;
  if (cover.getAttribute('aria-label') !== label) cover.setAttribute('aria-label', label);
  setCover(li.querySelector('.cover-img'), reel.cover_url);
  setHtml(li.querySelector('.card-badge-slot'), cardBadges(reel, rank));
  const title = li.querySelector('.reel-title');
  const text = ideaTitle(reel);
  if (title.textContent !== text) {
    title.textContent = text;
    title.title = text;
  }
  setHtml(li.querySelector('.views'), viewsHtml(reel.views));
  // How this reel's views compare with the most viewed one.
  li.querySelector('.pop-bar i').style.width = `${rank.top ? Math.max(4, Math.round(100 * reel.views / rank.top)) : 0}%`;
  const plan = li.querySelector('.card-plan');
  const status = PLAN[reel.plan_status];
  setHtml(plan, status ? `<span aria-hidden="true">${status.emoji}</span>` : iconHtml('plus'));
  plan.classList.toggle('is-planned', Boolean(status));
  const tip = status ? `In the content plan: ${status.label}` : 'Add to content plan';
  if (plan.title !== tip) {
    plan.title = tip;
    plan.setAttribute('aria-label', tip);
  }
}

// Updates the cards in place (keyed by reel id) instead of rebuilding the grid.
function renderGrid() {
  const grid = $('#reel-grid');
  grid.querySelectorAll('.skeleton').forEach((el) => el.remove());
  const existing = new Map();
  const before = new Map();
  grid.querySelectorAll('.reel[data-id]').forEach((li) => {
    existing.set(Number(li.dataset.id), li);
    before.set(li, li.getBoundingClientRect());
  });
  const firstTime = !state.gridShown;
  const rank = ranks();
  displayReels().forEach((reel, index) => {
    let li = existing.get(reel.id);
    if (li) {
      existing.delete(reel.id);
    } else {
      li = createCard(reel);
      li.classList.add('is-entering');
      li.style.setProperty('--i', firstTime ? index : 0);
    }
    updateCard(li, reel, rank);
    if (grid.children[index] !== li) grid.insertBefore(li, grid.children[index] || null);
  });
  existing.forEach((li) => li.remove());
  state.gridShown = true;
  $('#reels-empty').hidden = state.reels.length > 0;
  renderFeedSubtitle();
  glide(before);
}

function renderFeedSubtitle() {
  const n = state.reels.length;
  const picked = state.competitors.filter((c) => state.accounts.includes(c.id));
  const from = picked.length === 1 ? `all ${plural(n, 'reel')} of @${picked[0].username}`
    : picked.length ? `${plural(n, 'reel')} from ${picked.length} accounts` : `${n} latest`;
  $('#feed-subtitle').textContent = state.sort === 'views' ? `${from}, most viewed first` : `${from}, newest first`;
}

// Cards that changed place slide there instead of jumping (FLIP).
function glide(before) {
  if (!before.size || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const zoom = document.documentElement.classList.contains('fit')
    ? parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1
    : 1;
  before.forEach((old, el) => {
    if (!el.isConnected) return;
    const now = el.getBoundingClientRect();
    const dx = (old.left - now.left) / zoom;
    const dy = (old.top - now.top) / zoom;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    el.animate(
      [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }],
      { duration: 480, easing: 'cubic-bezier(0.2, 0.7, 0.2, 1)' },
    );
  });
}

function selectReel(id) {
  state.selectedId = id;
  storageSet('selectedReel', String(id));
  document.querySelectorAll('.reel[data-id]').forEach((el) => {
    el.classList.toggle('is-selected', Number(el.dataset.id) === id);
  });
  renderPreview();
}

function renderPreview() {
  const reel = findReel(state.selectedId);
  const cover = $('#preview-cover');
  cover.classList.toggle('is-loading', !state.loaded);
  crossfadeCover(cover, reel && reel.cover_url);

  // The whole cover opens the original reel on Instagram.
  const open = $('#preview-open');
  if (reel && reel.url) {
    open.href = reel.url;
    open.setAttribute('aria-label', `Open the original reel on Instagram: ${reelTitle(reel)}`);
  } else {
    open.removeAttribute('href');
    open.setAttribute('aria-label', reel ? reelTitle(reel) : 'No reel selected');
  }
  open.classList.toggle('has-link', Boolean(reel && reel.url));

  const views = $('#preview-views');
  views.hidden = !reel;
  setHtml(views, reel ? viewsHtml(reel.views) : '');

  const engagement = $('#preview-engagement');
  const rank = ranks();
  const place = reel && rank.byId.get(reel.id);
  engagement.hidden = !reel || (!reel.likes && !reel.comments && !place);
  setHtml(engagement, engagement.hidden ? '' :
    (place ? `<span title="Rank by views among the ${rank.total} latest reels">#${place} of ${rank.total}</span>` : '') +
    `<span title="Likes">${iconHtml('heart')}${formatViews(reel.likes)}</span>` +
    `<span title="Comments">${iconHtml('comment')}${formatViews(reel.comments)}</span>`);
  const duration = $('#preview-duration');
  duration.hidden = !reel || !reel.duration_seconds;
  setHtml(duration, duration.hidden ? '' : `${iconHtml('clock')}${formatDuration(reel.duration_seconds)}`);
  duration.title = 'Length';

  const toggle = $('#plan-toggle');
  const status = reel && PLAN[reel.plan_status];
  toggle.classList.toggle('is-planned', Boolean(status));
  toggle.querySelector('use').setAttribute('href', status ? '#i-plan' : '#i-plus');
  toggle.querySelector('span').textContent = status ? `${status.emoji} In content plan` : 'Add to content plan';
  toggle.title = status ? 'Open it in the content plan' : 'Add this reel to the content plan';

  document.querySelectorAll('[data-needs-reel]').forEach((btn) => { btn.disabled = !reel; });
}

// ---------- Editor (hook, script, CTA) ----------
function openEditor(id) {
  const reel = findReel(id);
  if (!reel) return;
  state.editingId = id;
  const form = $('#editor-form');
  const fromAI = !hasOwnVersion(reel) && reel.translation_status === 'done';
  fillVersion(fromAI ? aiVersion(reel) : { hook: reel.hook, script: reel.script, cta: reel.cta }, fromAI);
  form.dataset.touched = '';
  renderEditorOriginal(reel);
  renderEditorVersion(reel);
  $('#editor').showModal();
  $('#editor').scrollTop = 0;
  // On phones, focusing a field would pop the keyboard up over the reel.
  if (phone.matches) document.activeElement.blur();
  else form.elements.hook.focus();
}

function hasOwnVersion(reel) {
  return Boolean(reel.hook || reel.script || reel.cta);
}

function aiVersion(reel) {
  return { hook: reel.translation_hook || '', script: reel.translation_script || '', cta: reel.translation_cta || '' };
}

function fillVersion(version, fromAI) {
  const form = $('#editor-form');
  Object.keys(FIELDS).forEach((name) => { form.elements[name].value = version[name] || ''; });
  form.dataset.fromAi = fromAI ? '1' : '';
  delete form.dataset.remixed;
}

function renderEditorPlan(reel) {
  setHtml($('#editor-plan'), planPickerHtml(reel));
}

// The right column: your version, which is what you edit.
function renderEditorVersion(reel) {
  renderEditorPlan(reel);
  const form = $('#editor-form');
  const into = (state.me && state.me.languages && state.me.languages[reel.translated_into || 'en']) || languageName(reel.translated_into || 'en');
  const how = reel.translated
    ? `translated from ${languageName(reel.transcript_language)} into ${into}`
    : `${languageName(reel.transcript_language || 'en')}, word for word`;
  $('#reset-version').hidden = reel.translation_status !== 'done';
  let note;
  if (form.dataset.remixed) note = 'Remixed in your voice. Edit it and press Save; press Remix again for another take.';
  else if (form.dataset.fromAi) note = `Started from the AI’s hook, script and CTA (${how}). Edit it and press Save.`;
  else if (hasOwnVersion(reel)) note = `Your saved version${reel.edited_at ? `, saved ${timeAgo(reel.edited_at)}` : ''}.`;
  else if (reel.translation_status === 'pending') note = 'The AI is still splitting the transcript; its text appears here when ready.';
  else if (reel.transcript == null) note = 'Get the transcript with the button by the original; the AI then drafts the hook, script and CTA here.';
  else if (reel.translation_status === 'failed') note = `The AI could not split this transcript (${reel.translation_error}). Write your version here.`;
  else note = 'Write your hook, main script and call to action.';
  $('#version-note').textContent = note;
  renderRemixButton(reel);
  watchTranslation(reel);
}

// The left column: the original reel's numbers, transcript and caption.
function renderEditorOriginal(reel) {
  const posted = formatDate(reel.posted_at);
  $('#editor-meta').textContent = `@${reel.username}${posted ? ` · Posted ${posted}` : ''}`;
  const open = $('#editor-open');
  open.hidden = !reel.url;
  if (reel.url) open.href = reel.url;

  const stats = [
    ['play', 'Views', formatViews(reel.views)],
    ['heart', 'Likes', formatViews(reel.likes)],
    ['comment', 'Comments', formatViews(reel.comments)],
    ['clock', 'Length', formatDuration(reel.duration_seconds) || '–'],
  ];
  $('#editor-stats').innerHTML = stats.map(([icon, label, value]) => `
    <div class="reel-stat"><dt>${iconHtml(icon)}${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');

  const body = $('#editor-transcript');
  const fetchRow = $('#transcript-fetch');
  const busy = state.transcribing.has(reel.id);
  body.classList.remove('is-muted');
  $('#copy-transcript').hidden = !reel.transcript;
  if (reel.transcript) {
    body.textContent = reel.transcript;
  } else if (reel.transcript === '') {
    body.textContent = 'No speech in this reel (music or text only).';
    body.classList.add('is-muted');
  } else {
    body.textContent = 'Everything the author says in the reel, word for word.';
    body.classList.add('is-muted');
  }
  fetchRow.hidden = reel.transcript != null;
  const button = $('#get-transcript');
  button.disabled = busy;
  button.querySelector('.icon').classList.toggle('is-spinning', busy);
  button.querySelector('.icon use').setAttribute('href', busy ? '#i-refresh' : '#i-mic');
  button.querySelector('span').textContent = busy ? 'Transcribing…' : 'Get transcript';
  $('#transcript-note').textContent = busy
    ? 'Takes about 30 seconds'
    : transcriptNote(reel);

  renderCaption(reel, false);
}

// The caption in the user's language, with the original a click away.
function renderCaption(reel, original) {
  const translated = reel.caption_translation && reel.caption_translation !== reel.caption;
  const showOriginal = original || !translated;
  $('#editor-caption').textContent = (showOriginal ? reel.caption : reel.caption_translation) || 'This reel has no caption.';
  $('#editor-caption-title').textContent = translated && !showOriginal ? 'Caption, translated' : 'Caption';
  const toggle = $('#caption-toggle');
  toggle.hidden = !translated;
  toggle.textContent = showOriginal ? 'Show the translation' : 'Show the original';
  if (showOriginal) toggle.dataset.original = '1';
  else delete toggle.dataset.original;
}

// The owner sees what a transcript costs in Apify credit; customers see their plan's videos.
function transcriptNote(reel) {
  const me = state.me;
  if (!me || me.role === 'owner') return `About ${formatUsd(reel.transcript_cost_usd || 0)} of Apify credit, charged once`;
  if (!me.allowance) return 'Comes with every plan';
  const left = Math.max(0, me.allowance.videos - me.usage.videos);
  return left ? `Uses 1 of your ${plural(left, 'video')} left this month` : 'This month’s videos are used up';
}

// While the editor shows a reel waiting for the AI, check for its result.
let translationTimer = null;
function watchTranslation(reel) {
  clearTimeout(translationTimer);
  if (reel.translation_status !== 'pending') return;
  translationTimer = setTimeout(async () => {
    if (!$('#editor').open || state.editingId !== reel.id) return;
    try {
      const fresh = await api(`/reels/${reel.id}`);
      upsertReel(fresh);
      if (!$('#editor').open || state.editingId !== fresh.id) return;
      const form = $('#editor-form');
      const empty = Object.keys(FIELDS).every((name) => !form.elements[name].value.trim());
      if (fresh.translation_status === 'done' && empty && !form.dataset.touched) {
        fillVersion(hasOwnVersion(fresh) ? fresh : aiVersion(fresh), !hasOwnVersion(fresh));
      }
      renderEditorVersion(fresh);
    } catch (e) {
      watchTranslation(reel);
    }
  }, 5000);
}

// "Remix in my voice": the AI rewrites the reel for the user's niche and tone (Settings → Your
// voice). Nothing is saved: the fields fill, and the user edits and presses Save.
function renderRemixButton(reel) {
  const button = $('#remix-ai');
  const ready = reel.transcript != null && (reel.transcript || reel.caption_translation || reel.caption);
  button.disabled = !ready || button.classList.contains('is-busy');
  const me = state.me;
  const left = me && me.allowance && me.allowance.remixes != null
    ? Math.max(0, me.allowance.remixes - me.usage.remixes) : null;
  $('#remix-note').textContent = !ready
    ? 'Get the transcript first: the AI remixes what the author says.'
    : !(me && me.remix_profile)
      ? 'Tell the AI about you in Settings → Your voice, so the remix sounds like you.'
      : left != null ? `A new take for your niche and tone. ${left} ${left === 1 ? 'remix' : 'remixes'} left ${me.allowance.trial ? 'in your trial' : 'this month'}.`
        : 'A new hook, script and call to action for your niche and tone.';
}

async function remixReel() {
  const reel = findReel(state.editingId);
  if (!reel) return;
  const form = $('#editor-form');
  const hasText = Object.keys(FIELDS).some((name) => form.elements[name].value.trim());
  if (hasText && !confirm('Replace the text in your version with an AI remix in your voice? Nothing is saved until you press Save.')) return;
  const button = $('#remix-ai');
  const label = button.querySelector('span');
  button.classList.add('is-busy');
  button.disabled = true;
  label.textContent = 'Remixing… about 20 seconds';
  try {
    const remix = await api(`/reels/${reel.id}/remix`, { method: 'POST' });
    if (state.editingId !== reel.id || !$('#editor').open) return;
    fillVersion({ hook: remix.hook, script: remix.script, cta: remix.cta }, false);
    form.dataset.remixed = '1';
    form.dataset.touched = '1';
    form.querySelectorAll('textarea').forEach((field) => {
      field.classList.remove('is-filled');
      void field.offsetWidth;
      field.classList.add('is-filled');
    });
    renderEditorVersion(reel);
    toast('Remixed in your voice. Edit it, then press Save.');
    loadMe().then(() => { if (state.editingId === reel.id) renderRemixButton(reel); });
  } catch (err) {
    toast(err.message, { error: true });
  } finally {
    button.classList.remove('is-busy');
    label.textContent = 'Remix in my voice';
    renderRemixButton(findReel(state.editingId) || reel);
  }
}

// "Start again from the AI": replace the fields with the AI's hook, script and CTA.
function resetVersion() {
  const reel = findReel(state.editingId);
  if (!reel || reel.translation_status !== 'done') return;
  const form = $('#editor-form');
  const hasText = Object.keys(FIELDS).some((name) => form.elements[name].value.trim());
  if (hasText && !confirm('Replace the text in your version with the AI’s hook, script and CTA?')) return;
  fillVersion(aiVersion(reel), true);
  renderEditorVersion(reel);
}

async function getTranscript() {
  const id = state.editingId;
  const reel = findReel(id);
  if (!reel || state.transcribing.has(id)) return;
  state.transcribing.add(id);
  renderEditorOriginal(reel);
  try {
    const updated = await api(`/reels/${id}/transcript`, { method: 'POST' });
    state.reels = state.reels.map((r) => (r.id === updated.id ? updated : r));
    renderGrid();
    toast(updated.transcript ? 'Transcript saved. The AI splits it next.' : 'No speech found in this reel');
    loadMe(); // one more video used this month
  } catch (err) {
    toast(err.message, { error: true });
  } finally {
    state.transcribing.delete(id);
    const current = findReel(id);
    if (state.editingId === id && current && $('#editor').open) {
      renderEditorOriginal(current);
      renderEditorVersion(current);
    }
    if (state.selectedId === id) renderPreview();
  }
}

async function copyTranscript() {
  const reel = findReel(state.editingId);
  if (!reel || !reel.transcript) return;
  try {
    await navigator.clipboard.writeText(reel.transcript);
    toast('Transcript copied');
  } catch (e) {
    toast('Could not copy. Select the text instead.');
  }
}

async function saveEditor(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const updated = await api(`/reels/${state.editingId}`, {
      method: 'PATCH',
      body: {
        hook: form.elements.hook.value,
        script: form.elements.script.value,
        cta: form.elements.cta.value,
      },
    });
    upsertReel(updated);
    $('#editor').close();
    toast(updated.plan_status ? 'Saved in your content plan' : 'Saved');
  } catch (err) {
    toast(err.message, { error: true });
  } finally {
    submit.disabled = false;
  }
}

// ---------- Content plan: a Kanban board ----------
// Columns ❌ not started, ⏳ ready to film, ✅ posted. Drag a card to another
// column to change its status; click it to edit your version.
// Newest status change first in each column, so a card dropped in a column lands on top.
function sortPlan() {
  const when = (r) => new Date(r.plan_status_at || r.planned_at);
  state.plan.sort((a, b) => when(b) - when(a) || b.id - a.id);
}

async function loadPlan() {
  try {
    state.plan = await api('/plan');
  } catch (e) {
    return;
  }
  sortPlan();
  renderPlan();
}

function planPickerHtml(reel) {
  if (!reel.plan_status) {
    return `<button class="btn btn-chip" type="button" data-add-plan="${reel.id}">${iconHtml('plus')}Add to content plan</button>`;
  }
  return `<div class="plan-status" role="group" aria-label="Content plan status">${Object.entries(PLAN).map(([key, p]) => `
    <button class="status-btn${reel.plan_status === key ? ' is-active' : ''}" type="button" data-set-status="${key}"
            data-reel="${reel.id}" aria-pressed="${reel.plan_status === key}" title="${p.hint}">
      <span class="status-emoji" aria-hidden="true">${p.emoji}</span>${p.label}
    </button>`).join('')}
    <button class="link-btn" type="button" data-remove-plan="${reel.id}">${iconHtml('trash')}Remove</button></div>`;
}

function renderPlan() {
  const counts = { todo: 0, ready: 0, posted: 0 };
  state.plan.forEach((reel) => { counts[reel.plan_status] += 1; });
  const open = counts.todo + counts.ready;
  const navCount = $('#plan-count');
  navCount.hidden = !open;
  navCount.textContent = String(open);
  $('#plan-subtitle').textContent = state.plan.length
    ? `${plural(state.plan.length, 'reel')} · drag a card to another column (on a phone, press and hold), click it to edit`
    : 'Reels you plan to film';

  const before = new Map();
  const existing = new Map();
  document.querySelectorAll('.kanban-card').forEach((card) => {
    // A dropped card glides from where it was let go.
    const dropped = state.dropRect && state.dropRect.card === card;
    before.set(card, dropped ? state.dropRect.rect : card.getBoundingClientRect());
    existing.set(Number(card.dataset.id), card);
  });
  const rank = ranks();
  Object.keys(PLAN).forEach((status) => {
    const list = document.querySelector(`.kanban-list[data-status="${status}"]`);
    const reels = state.plan.filter((r) => r.plan_status === status);
    document.querySelector(`.kanban-col[data-status="${status}"] .kanban-count`).textContent = String(reels.length);
    reels.forEach((reel, index) => {
      let card = existing.get(reel.id);
      if (card) existing.delete(reel.id);
      else card = createKanbanCard(reel);
      updateKanbanCard(card, reel, rank);
      if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
    });
  });
  existing.forEach((card) => card.remove());
  $('#plan-empty').hidden = state.plan.length > 0;
  glide(before);
  if (state.dropRect && state.dropRect.card.isConnected) raiseWhileGliding(state.dropRect.card);
}

function createKanbanCard(reel) {
  const card = document.createElement('li');
  card.className = 'kanban-card';
  card.dataset.id = reel.id;
  card.tabIndex = 0;
  card.innerHTML = `
    <span class="kanban-thumb cover"><span class="cover-img"></span></span>
    <div class="kanban-body">
      <p class="kanban-title"></p>
      <p class="kanban-meta"></p>
    </div>
    <button class="kanban-remove" type="button" data-remove-plan="${reel.id}"
            aria-label="Remove from the content plan" title="Remove from the content plan">${iconHtml('close')}</button>`;
  card.addEventListener('animationend', () => card.classList.remove('is-highlight'));
  bindKanbanDrag(card);
  return card;
}

// Drag a card to another column. With a mouse it lifts as soon as it moves; on a touch
// screen, press and hold for a moment (a quick swipe still scrolls the page). The card
// follows the pointer, a slot opens at the top of the column it will land in, the page
// scrolls near the edges, and on release the card glides into place.
const drag = { card: null };

function bindKanbanDrag(card) {
  card.addEventListener('pointerdown', (e) => {
    if (drag.card || (e.pointerType === 'mouse' && e.button !== 0) || e.target.closest('.kanban-remove')) return;
    const touch = e.pointerType !== 'mouse';
    const start = { x: e.clientX, y: e.clientY };
    let pressTimer = null;
    const cleanup = () => {
      clearTimeout(pressTimer);
      window.removeEventListener('pointermove', early);
      window.removeEventListener('pointerup', cleanup);
      window.removeEventListener('pointercancel', cleanup);
    };
    // Before the drag starts: a mouse that moves 6px starts it; a finger that moves cancels it (scrolling).
    const early = (ev) => {
      const moved = Math.hypot(ev.clientX - start.x, ev.clientY - start.y);
      if (!touch && moved > 6) {
        cleanup();
        startDrag(card, start, ev);
      } else if (touch && moved > 10) {
        cleanup();
      }
    };
    window.addEventListener('pointermove', early);
    window.addEventListener('pointerup', cleanup);
    window.addEventListener('pointercancel', cleanup);
    if (touch) {
      pressTimer = setTimeout(() => {
        cleanup();
        // A small buzz, where allowed (browsers refuse it before the first tap on the page).
        if (navigator.vibrate && navigator.userActivation?.hasBeenActive) navigator.vibrate(12);
        startDrag(card, start, { clientX: start.x, clientY: start.y });
      }, 280);
    }
  });
}

function dragScroller(card) {
  const feed = card.closest('.feed');
  return feed && feed.scrollHeight > feed.clientHeight + 4 && getComputedStyle(feed).overflowY !== 'visible'
    ? feed : document.scrollingElement;
}

function startDrag(card, start, ev) {
  const origin = card.getBoundingClientRect();
  Object.assign(drag, {
    card, start, scroller: dragScroller(card), col: null, slot: null, pointer: { x: ev.clientX, y: ev.clientY },
    grab: { x: start.x - origin.left, y: start.y - origin.top }, size: { w: origin.width, h: origin.height },
    frame: 0, from: card.closest('.kanban-col'),
  });
  card.getAnimations().forEach((a) => a.cancel()); // a glide still running would hold the card in place
  card.classList.add('is-dragging');
  drag.from.classList.add('is-source'); // so the card is drawn above the other columns
  document.documentElement.classList.add('is-kanban-dragging');
  document.addEventListener('pointermove', onDragMove);
  document.addEventListener('pointerup', onDragEnd);
  document.addEventListener('pointercancel', onDragCancel);
  document.addEventListener('keydown', onDragKey);
  document.addEventListener('touchmove', stopTouchScroll, { passive: false });
  onDragMove(ev);
  drag.frame = requestAnimationFrame(autoScroll);
}

function stopTouchScroll(e) {
  if (drag.card) e.preventDefault(); // the finger drags the card, not the page
}

// Keeps a value between low and high; with no room between them, takes the middle.
const clamp = (value, low, high) => (high < low ? (low + high) / 2 : Math.max(low, Math.min(value, high)));

// Where the card may go: the board, within the visible part of the panel (or the page) and
// above the phone's tab bar, so it never leaves the frame or gets cut off by it.
function dragBounds() {
  const board = $('#kanban').getBoundingClientRect();
  const view = drag.scroller === document.scrollingElement
    ? { top: 0, bottom: window.innerHeight } : drag.scroller.getBoundingClientRect();
  let bottom = Math.min(view.bottom, window.innerHeight);
  const tabs = $('.nav');
  if (getComputedStyle(tabs).position === 'fixed') bottom = Math.min(bottom, tabs.getBoundingClientRect().top);
  const visible = { top: Math.max(view.top, 0), bottom };
  return {
    left: board.left, right: board.right, visible,
    top: Math.max(board.top, visible.top), bottom: Math.min(board.bottom, visible.bottom),
  };
}

// The card follows the pointer (held where it was grabbed), kept inside the bounds.
function placeDraggedCard(bounds) {
  const { card, pointer, grab, size, start } = drag;
  // Room for the lift: the card grows 4% and tilts up to 4° (sin 4° ≈ 0.07, half on each side).
  const roomX = size.w * 0.02 + size.h * 0.04 + 3;
  const roomY = size.h * 0.02 + size.w * 0.04 + 3;
  const left = clamp(pointer.x - grab.x, bounds.left + roomX, bounds.right - size.w - roomX);
  const top = clamp(pointer.y - grab.y, bounds.top + roomY, bounds.bottom - size.h - roomY);
  card.style.transform = 'none';
  const home = card.getBoundingClientRect(); // its place in the column, which moves as the page scrolls
  const zoom = uiZoom();
  const tilt = clamp((left - (start.x - grab.x)) / zoom / 40, -4, 4);
  card.style.transform = `translate(${(left - home.left) / zoom}px, ${(top - home.top) / zoom}px) rotate(${tilt}deg) scale(1.04)`;
}

function onDragMove(ev) {
  if (!drag.card) return;
  drag.pointer = { x: ev.clientX, y: ev.clientY };
  const bounds = dragBounds();
  placeDraggedCard(bounds);
  updateDropTarget(bounds);
}

// The column the card will land in: the one under the pointer or, with the pointer outside
// the board, the one nearest to it (the card stays inside the board, over that column).
function updateDropTarget(bounds) {
  const x = clamp(drag.pointer.x, bounds.left, bounds.right);
  const y = clamp(drag.pointer.y, bounds.top, bounds.bottom);
  let col = null;
  let nearest = Infinity;
  document.querySelectorAll('#kanban .kanban-col').forEach((c) => {
    const r = c.getBoundingClientRect();
    const distance = Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom));
    if (distance < nearest) [col, nearest] = [c, distance];
  });
  if (col === drag.col) return;
  if (drag.col) drag.col.classList.remove('is-over');
  if (drag.slot) closeSlot(drag.slot);
  drag.col = col;
  drag.slot = null;
  if (!col) return;
  col.classList.add('is-over');
  if (col !== drag.from) {
    const slot = document.createElement('li');
    slot.className = 'kanban-slot';
    slot.style.setProperty('--slot-height', `${drag.card.offsetHeight}px`);
    col.querySelector('.kanban-list').prepend(slot);
    drag.slot = slot;
  }
}

function closeSlot(slot) {
  slot.classList.add('is-closing');
  setTimeout(() => slot.remove(), 180);
}

// Near the top or bottom edge, the page (or the panel) scrolls so far columns can be reached.
function autoScroll() {
  if (!drag.card) return;
  const box = dragBounds().visible;
  const edge = 70;
  const y = drag.pointer.y;
  const speed = y < box.top + edge ? -Math.ceil((box.top + edge - y) / 6)
    : y > box.bottom - edge ? Math.ceil((y - (box.bottom - edge)) / 6) : 0;
  const was = drag.scroller.scrollTop;
  if (speed) drag.scroller.scrollTop += speed;
  if (drag.scroller.scrollTop !== was) {
    const bounds = dragBounds();
    placeDraggedCard(bounds);
    updateDropTarget(bounds);
  }
  drag.frame = requestAnimationFrame(autoScroll);
}

// A dropped card is drawn above the other columns while it glides into place.
function raiseWhileGliding(card) {
  const lifted = [card, card.closest('.kanban-col')];
  lifted.forEach((el) => el.classList.add('is-raised'));
  setTimeout(() => lifted.forEach((el) => el.classList.remove('is-raised')), 520);
}

function finishDrag() {
  cancelAnimationFrame(drag.frame);
  document.removeEventListener('pointermove', onDragMove);
  document.removeEventListener('pointerup', onDragEnd);
  document.removeEventListener('pointercancel', onDragCancel);
  document.removeEventListener('keydown', onDragKey);
  document.removeEventListener('touchmove', stopTouchScroll);
  document.documentElement.classList.remove('is-kanban-dragging');
  const { card, col, slot } = drag;
  drag.from.classList.remove('is-source');
  if (col) col.classList.remove('is-over');
  if (slot) slot.remove();
  card.dataset.dragged = '1'; // the click that follows is not an "open"
  setTimeout(() => { delete card.dataset.dragged; }, 0);
  state.dropRect = { card, rect: card.getBoundingClientRect() };
  card.style.transition = 'none'; // the glide below animates it; no CSS transition on top
  card.classList.remove('is-dragging');
  card.style.transform = '';
  void card.offsetWidth;
  card.style.transition = '';
  drag.card = null;
  return { card, col };
}

function onDragEnd() {
  if (!drag.card) return;
  const { card, col } = finishDrag();
  moveCard(card, col && col.dataset.status);
}

function onDragCancel() {
  if (!drag.card) return;
  finishDrag();
  renderPlan(); // glides back to its place
  state.dropRect = null;
}

function onDragKey(e) {
  if (e.key === 'Escape') onDragCancel();
}

// Put a card in another column now (it glides there and settles), then save.
function moveCard(card, status) {
  const reel = state.plan.find((r) => r.id === Number(card.dataset.id));
  if (reel && status && status !== reel.plan_status) {
    upsertReel({ ...reel, plan_status: status, plan_status_at: new Date().toISOString() });
    card.classList.add('is-landing');
    card.addEventListener('animationend', () => card.classList.remove('is-landing'), { once: true });
    setPlanStatus(reel.id, status);
  } else {
    renderPlan(); // glides back to its place
  }
  state.dropRect = null;
}

function updateKanbanCard(card, reel, rank) {
  setCover(card.querySelector('.cover-img'), reel.cover_url);
  const title = planTitle(reel);
  const titleEl = card.querySelector('.kanban-title');
  if (titleEl.textContent !== title) titleEl.textContent = title;
  const place = rank.byId.get(reel.id);
  setHtml(card.querySelector('.kanban-meta'), [
    `@${escapeHtml(reel.username)}`,
    `${iconHtml('play')}${formatViews(reel.views)}`,
    place && `#${place}`,
  ].filter(Boolean).join(' · '));
  card.setAttribute('aria-label', `${title}. Open to edit.`);
}

function bindKanban() {
  $('#kanban').addEventListener('click', (e) => {
    const remove = e.target.closest('[data-remove-plan]');
    if (remove) {
      removeFromPlan(Number(remove.dataset.removePlan));
      return;
    }
    const card = e.target.closest('.kanban-card');
    if (card && !card.dataset.dragged) openEditor(Number(card.dataset.id));
  });
  $('#kanban').addEventListener('keydown', (e) => {
    const card = e.target.closest('.kanban-card');
    if (!card) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openEditor(Number(card.dataset.id));
    } else if (e.shiftKey && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
      e.preventDefault();
      const order = Object.keys(PLAN);
      const reel = state.plan.find((r) => r.id === Number(card.dataset.id));
      const next = order[order.indexOf(reel.plan_status) + (e.key === 'ArrowRight' ? 1 : -1)];
      if (!next) return;
      state.dropRect = { card, rect: card.getBoundingClientRect() };
      moveCard(card, next);
      card.focus();
    }
  });
}

async function addToPlan(id) {
  try {
    upsertReel(await api(`/reels/${id}/plan`, { method: 'POST' }));
    toast(`Added to your content plan ${PLAN.todo.emoji}`);
  } catch (err) {
    toast(err.message, { error: true });
  }
}

async function setPlanStatus(id, status) {
  try {
    const reel = await api(`/reels/${id}`, { method: 'PATCH', body: { plan_status: status } });
    upsertReel(reel);
    toast(`${PLAN[status].emoji} ${PLAN[status].label}`);
  } catch (err) {
    toast(err.message, { error: true });
  }
}

async function removeFromPlan(id) {
  try {
    upsertReel(await api(`/reels/${id}/plan`, { method: 'DELETE' }));
    toast('Removed from the content plan. Your text is kept.');
  } catch (err) {
    toast(err.message, { error: true });
  }
}

function showInPlan(id) {
  setView('plan');
  renderPlan();
  const card = document.querySelector(`.kanban-card[data-id="${id}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.classList.add('is-highlight');
}

// ---------- Home: swipe ideas (take or skip) ----------
// The most viewed reels you have not planned or skipped, one card at a time.
// Right (→) takes the idea into the content plan, left (←) skips it, ↑ takes
// it and opens the editor; Backspace undoes the last swipe.
async function loadIdeas() {
  try {
    const reels = await api('/reels?sort=views&limit=100');
    state.ideas = reels.filter((r) => !r.plan_status && !r.skipped_at);
  } catch (e) {
    return;
  }
  renderDeck();
}

function renderDeck() {
  const deck = $('#deck');
  const existing = new Map();
  deck.querySelectorAll('.idea-card:not(.is-leaving)').forEach((card) => existing.set(Number(card.dataset.id), card));
  const rank = ranks();
  state.ideas.slice(0, 3).forEach((reel, depth) => {
    let card = existing.get(reel.id);
    if (card) existing.delete(reel.id);
    else {
      card = createIdeaCard(reel, rank);
      deck.appendChild(card);
    }
    card.dataset.depth = String(depth);
    card.style.zIndex = String(10 - depth);
  });
  existing.forEach((card) => card.remove());

  const left = state.ideas.length;
  const fresh = state.loaded && !state.reels.length; // a new account: nothing fetched yet
  $('#deck-empty').hidden = left > 0;
  $('#deck-empty').textContent = fresh
    ? 'No ideas yet. Add a competitor on the Competitors screen and their most viewed reels will show up here.'
    : 'No ideas left. Fetch new reels on the Competitors page, or undo a skip.';
  $('#home-subtitle').textContent = left
    ? `${plural(left, 'idea')} left · swipe right to take, left to skip`
    : fresh ? 'Add a competitor to get ideas' : 'No ideas left';
  ['#swipe-skip', '#swipe-take', '#swipe-edit'].forEach((sel) => { $(sel).disabled = !left; });
  $('#swipe-undo').disabled = !state.swipes.length;
  if (left && state.view === 'home' && state.selectedId !== state.ideas[0].id) selectReel(state.ideas[0].id);
}

function createIdeaCard(reel, rank) {
  const card = document.createElement('article');
  card.className = 'idea-card';
  card.dataset.id = reel.id;
  const place = rank.byId.get(reel.id);
  card.innerHTML = `
    <div class="idea-cover cover"><span class="cover-img"></span>
      <span class="idea-views">${viewsHtml(reel.views)}${place ? ` · #${place} of ${rank.total}` : ''}</span>
    </div>
    <div class="idea-body">
      <p class="idea-label">Hook</p>
      <h2 class="idea-title"></h2>
      <p class="idea-meta">@${escapeHtml(reel.username)} · ${iconHtml('heart')}${formatViews(reel.likes)} · ${iconHtml('comment')}${formatViews(reel.comments)}${reel.duration_seconds ? ` · ${formatDuration(reel.duration_seconds)}` : ''}</p>
    </div>
    <span class="idea-stamp is-take" aria-hidden="true">TAKE</span>
    <span class="idea-stamp is-skip" aria-hidden="true">SKIP</span>`;
  card.querySelector('.idea-title').textContent = ideaTitle(reel);
  setCover(card.querySelector('.cover-img'), reel.cover_url);
  bindSwipe(card);
  return card;
}

function uiZoom() {
  return document.documentElement.classList.contains('fit')
    ? parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1
    : 1;
}

// Drag the top card: past the threshold it flies out, otherwise it springs back.
function bindSwipe(card) {
  let startX = 0;
  let startY = 0;
  let dx = 0;
  let dragging = false;
  card.addEventListener('pointerdown', (e) => {
    if (card.dataset.depth !== '0' || state.deciding) return;
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    dx = 0;
    card.setPointerCapture(e.pointerId);
    card.classList.add('is-dragging');
  });
  card.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const zoom = uiZoom();
    dx = (e.clientX - startX) / zoom;
    const dy = (e.clientY - startY) / zoom;
    card.style.transform = `translate(${dx}px, ${dy * 0.25}px) rotate(${dx / 18}deg)`;
    card.style.setProperty('--take', String(Math.max(0, Math.min(1, dx / 110))));
    card.style.setProperty('--skip', String(Math.max(0, Math.min(1, -dx / 110))));
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    card.classList.remove('is-dragging');
    if (Math.abs(dx) > 110) {
      decide(dx > 0 ? 'take' : 'skip');
    } else {
      card.style.transform = '';
      card.style.setProperty('--take', '0');
      card.style.setProperty('--skip', '0');
    }
  };
  card.addEventListener('pointerup', end);
  card.addEventListener('pointercancel', end);
}

function flyOut(card, direction) {
  card.classList.add('is-leaving');
  card.style.setProperty(direction > 0 ? '--take' : '--skip', '1');
  card.style.transform = `translate(${direction * 640}px, -40px) rotate(${direction * 26}deg)`;
  card.style.opacity = '0';
  setTimeout(() => card.remove(), 420);
}

async function decide(action, { edit = false } = {}) {
  const reel = state.ideas[0];
  if (!reel || state.deciding) return;
  state.deciding = true;
  const card = document.querySelector(`.idea-card[data-id="${reel.id}"]`);
  if (card) flyOut(card, action === 'take' ? 1 : -1);
  state.ideas.shift();
  state.swipes.push({ reel, action });
  renderDeck();
  try {
    const updated = await api(`/reels/${reel.id}/${action === 'take' ? 'plan' : 'skip'}`, { method: 'POST' });
    upsertReel(updated);
    toast(action === 'take' ? '✓ Added to your content plan, under “Not started”' : 'Skipped');
    if (edit) openEditor(updated.id);
  } catch (err) {
    state.swipes.pop();
    state.ideas.unshift(reel);
    renderDeck();
    toast(err.message, { error: true });
  } finally {
    state.deciding = false;
  }
}

async function undoSwipe() {
  const last = state.swipes.pop();
  if (!last || state.deciding) return;
  try {
    const updated = await api(`/reels/${last.reel.id}/${last.action === 'take' ? 'plan' : 'skip'}`, { method: 'DELETE' });
    upsertReel(updated);
    state.ideas.unshift(updated);
    renderDeck();
    const card = document.querySelector(`.idea-card[data-id="${updated.id}"]`);
    if (card) card.classList.add(last.action === 'take' ? 'is-back-from-right' : 'is-back-from-left');
    toast(last.action === 'take' ? 'Undone: taken out of the content plan' : 'Undone: the idea is back');
  } catch (err) {
    state.swipes.push(last);
    toast(err.message, { error: true });
  }
}

// ---------- Competitors ----------
async function loadCompetitors() {
  try {
    state.competitors = await api('/competitors');
  } catch (e) {
    return;
  }
  renderCompetitors();
}

function competitorStatus(c) {
  const job = c.latest_job;
  if (job && (job.status === 'queued' || job.status === 'running')) {
    return { text: job.message || 'Fetching reels…', tone: 'busy' };
  }
  if (job && job.status === 'failed') return { text: job.message, tone: 'error' };
  const fetched = c.last_fetched_at ? ` · Fetched ${timeAgo(c.last_fetched_at)}` : '';
  return { text: `${plural(c.reel_count, 'reel')}${fetched}`, tone: '' };
}

// Reels: chips to show every account's latest reels, or only the accounts picked.
function renderAccountFilter() {
  const known = new Set(state.competitors.map((c) => c.id));
  const kept = state.accounts.filter((id) => known.has(id));
  if (state.competitors.length && kept.length !== state.accounts.length) {
    setAccounts(kept);
    loadReels();
  }
  const box = $('#account-filter');
  box.hidden = state.competitors.length < 1;
  const chip = (id, label, on, title) => `<button class="filter-chip" type="button" data-account="${id}" aria-pressed="${on}" title="${escapeHtml(title)}">${label}</button>`;
  setHtml(box, chip('all', 'All accounts', !state.accounts.length, 'The 20 latest reels from every account')
    + state.competitors.map((c) => chip(c.id, `@${escapeHtml(c.username)} <small>${c.reel_count}</small>`,
      state.accounts.includes(c.id), `Only @${c.username}'s reels; pick more to compare them`)).join(''));
  if (state.gridShown) renderFeedSubtitle();
}

function setAccounts(ids) {
  state.accounts = ids;
  storageSet('accounts', JSON.stringify(ids));
}

function toggleAccount(value) {
  if (value === 'all') setAccounts([]);
  else {
    const id = Number(value);
    setAccounts(state.accounts.includes(id) ? state.accounts.filter((x) => x !== id) : [...state.accounts, id]);
  }
  renderAccountFilter();
  loadReels();
}

function renderCompetitors() {
  renderAccountFilter();
  setHtml($('#competitor-list'), state.competitors.map((c) => {
    const status = competitorStatus(c);
    const busy = status.tone === 'busy';
    const job = c.latest_job;
    return `
    <li class="competitor">
      <span class="avatar" aria-hidden="true">${escapeHtml(c.username[0])}</span>
      <div class="competitor-info">
        <span class="competitor-name">
          <a href="${escapeHtml(c.profile_url)}" target="_blank" rel="noopener">@${escapeHtml(c.username)}</a>
        </span>
        <span class="meta${status.tone ? ` is-${status.tone}` : ''}">${escapeHtml(status.text)}</span>
      </div>
      ${job ? `<button class="link-btn" type="button" data-progress="${job.id}">${iconHtml('list')}Steps</button>` : ''}
      <button class="btn btn-chip" type="button" data-fetch="${c.id}"${busy ? ' disabled' : ''}>
        ${iconHtml(busy ? 'refresh' : 'download').replace('class="icon"', `class="icon${busy ? ' is-spinning' : ''}"`)}${busy ? 'Fetching' : 'Fetch reels'}
      </button>
      <button class="icon-btn" type="button" data-delete="${c.id}" aria-label="Remove @${escapeHtml(c.username)}">
        ${iconHtml('trash')}
      </button>
    </li>`;
  }).join(''));
  $('#competitors-empty').hidden = state.competitors.length > 0;
  $('#competitors-subtitle').textContent = state.competitors.length
    ? `${plural(state.competitors.length, 'account')} you track`
    : 'Accounts you track';
}

async function addCompetitor(e) {
  e.preventDefault();
  const input = $('#competitor-input');
  const handle = parseInstagramHandle(input.value);
  if (!handle) {
    input.setAttribute('aria-invalid', 'true');
    toast('Enter an Instagram username or profile link', { error: true });
    input.focus();
    return;
  }
  input.removeAttribute('aria-invalid');
  const submit = e.currentTarget.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const competitor = await api('/competitors', { method: 'POST', body: { handle } });
    input.value = '';
    if (competitor.latest_job) track([competitor.latest_job.id]);
    loadCompetitors();
  } catch (err) {
    if (err.status === 422) input.setAttribute('aria-invalid', 'true');
    toast(err.message, { error: true });
  } finally {
    submit.disabled = false;
  }
}

async function fetchCompetitor(id) {
  try {
    const job = await api(`/competitors/${id}/fetch`, { method: 'POST' });
    track([job.id]);
    loadCompetitors();
  } catch (err) {
    toast(err.message, { error: true });
  }
}

async function deleteCompetitor(id) {
  const c = state.competitors.find((x) => x.id === id);
  if (!c || !confirm(`Remove @${c.username} and all of their reels?`)) return;
  try {
    await api(`/competitors/${id}`, { method: 'DELETE' });
    toast(`@${c.username} removed`);
    // Its reels leave the feed, the ideas, the content plan and the preview at once.
    const theirs = (r) => r.competitor_id !== id;
    setAccounts(state.accounts.filter((x) => x !== id));
    state.plan = state.plan.filter(theirs);
    state.ideas = state.ideas.filter(theirs);
    state.reels = state.reels.filter(theirs);
    state.swipes = state.swipes.filter((swipe) => theirs(swipe.reel));
    if (!findReel(state.selectedId)) state.selectedId = defaultSelection();
    renderGrid();
    renderPreview();
    renderPlan();
    renderDeck();
    await Promise.all([loadCompetitors(), loadReels(), loadPlan(), loadIdeas(), loadMe()]);
  } catch (err) {
    toast(err.message, { error: true });
  }
}

async function refreshAll() {
  const btn = $('#refresh');
  btn.classList.add('is-spinning');
  try {
    const jobs = await api('/refresh', { method: 'POST' });
    if (!jobs.length) {
      await loadReels();
      btn.classList.remove('is-spinning');
      toast('Add a competitor to fetch their latest reels');
      return;
    }
    track(jobs.map((job) => job.id));
    loadCompetitors();
  } catch (err) {
    btn.classList.remove('is-spinning');
    toast(err.message, { error: true });
  }
}

// ---------- Activity: every step of "Fetch reels", live ----------
// Polls GET /api/jobs/{id} and shows its steps (database, queue, Apify, saving,
// transcripts, the AI) with timings, progress and the server's debug log.
const activity = {
  order: [], // job ids, oldest first
  jobs: new Map(), // id -> latest detail
  current: null,
  timer: null,
  logJob: null,
  logCount: 0,
  ticker: null,
};

const STEP_WAITING = {
  database: 'Saving the competitor',
  queue: 'Waiting for its turn',
  apify: 'Waits for the queue',
  save: 'Waits for Apify',
  transcripts: 'Waits for the reels',
  ai: 'Waits for the transcripts',
};

function isRunning(job) {
  return job && (job.status === 'queued' || job.status === 'running');
}

function track(jobIds, { reveal = true } = {}) {
  clearTimeout(activity.closeTimer);
  jobIds.forEach((id) => { if (!activity.order.includes(id)) activity.order.push(id); });
  if (jobIds.length) activity.current = jobIds[jobIds.length - 1];
  if (reveal) showActivity();
  else showPill();
  pollActivity();
}

async function pollActivity() {
  clearTimeout(activity.timer);
  const live = activity.order.filter((id) => {
    const job = activity.jobs.get(id);
    return !job || job.in_progress;
  });
  const results = await Promise.allSettled(live.map((id) => api(`/jobs/${id}`)));
  const finished = [];
  let progressed = false;
  results.forEach((result, i) => {
    const id = live[i];
    if (result.status === 'rejected') {
      if (result.reason.status === 404) { // its competitor was removed
        activity.order = activity.order.filter((x) => x !== id);
        activity.jobs.delete(id);
      }
      return;
    }
    const before = activity.jobs.get(id);
    const after = result.value;
    activity.jobs.set(id, after);
    if (before && isRunning(before) && !isRunning(after)) finished.push(after);
    if (isRunning(after)) activity.watched = true; // ran while this page was open
    if (!before || progressCount(before) !== progressCount(after)) progressed = true;
  });
  if (!activity.jobs.has(activity.current)) activity.current = activity.order[activity.order.length - 1] || null;
  renderActivity();

  // Reels appear in the grid as they are saved, transcribed and split.
  if (finished.length || progressed) await Promise.all([loadReels(), loadCompetitors()]);
  else if (state.view === 'competitors') loadCompetitors();
  finished.filter((job) => job.status === 'failed')
    .forEach((job) => toast(`@${job.username}: ${job.message}`, { error: true }));

  const jobs = activity.order.map((id) => activity.jobs.get(id)).filter(Boolean);
  const running = jobs.some(isRunning);
  $('#refresh').classList.toggle('is-spinning', running);
  if (jobs.some((job) => job.in_progress)) activity.timer = setTimeout(pollActivity, running ? 1200 : 3000);
  else if (activity.watched && jobs.length && jobs.every((job) => job.status === 'succeeded')) autoCloseActivity();
}

// A fetch that ran while you watched, done with nothing failed: the panel closes by itself.
// A failure stays open to read, and so do old fetches opened on purpose (Steps).
function autoCloseActivity() {
  clearTimeout(activity.closeTimer);
  activity.closeTimer = setTimeout(() => {
    const jobs = activity.order.map((id) => activity.jobs.get(id)).filter(Boolean);
    if (jobs.every((job) => !job.in_progress && job.status === 'succeeded')) closeActivity();
    activity.watched = false;
  }, 3500);
}

// Changes when reels are saved, transcribed or processed by the AI.
function progressCount(job) {
  return job.steps.map((s) => `${s.state}:${s.done_count || 0}`).join('|');
}

function stepDuration(step) {
  if (!step.started_at) return '';
  const end = step.finished_at ? new Date(step.finished_at) : new Date();
  return formatDuration((end - new Date(step.started_at)) / 1000);
}

function renderActivity() {
  const job = activity.jobs.get(activity.current);
  if (!job) return;
  const steps = job.steps;
  const failed = job.status === 'failed';
  const activeIndex = steps.findIndex((s) => s.state === 'active');
  const doneSteps = steps.filter((s) => ['done', 'skipped', 'failed'].includes(s.state)).length;
  const active = steps[activeIndex];
  const partial = active && active.total_count ? (active.done_count || 0) / active.total_count : 0;
  const progress = failed ? 1 : (doneSteps + partial) / steps.length;

  const panel = $('#activity');
  panel.classList.toggle('is-running', job.in_progress);
  panel.classList.toggle('is-failed', failed);
  panel.classList.toggle('is-done', !job.in_progress && !failed);
  $('#activity-title').textContent = job.in_progress
    ? `Fetching @${job.username}`
    : failed ? `@${job.username}: stopped` : `@${job.username}: done`;
  $('#activity-bar').style.width = `${Math.round(progress * 100)}%`;
  renderActivityJobs();
  renderSteps(steps);
  renderLog(job);
  renderActivityTimes();

  const pillText = job.in_progress
    ? `@${job.username} · step ${Math.max(activeIndex, 0) + 1} of ${steps.length}`
    : failed ? `@${job.username} · stopped` : `@${job.username} · done`;
  $('#activity-pill-text').textContent = pillText;
  $('#activity-pill').classList.toggle('is-running', job.in_progress);
  $('#activity-pill').classList.toggle('is-failed', failed);

  clearInterval(activity.ticker);
  if (job.in_progress) activity.ticker = setInterval(renderActivityTimes, 1000);
}

function renderActivityTimes() {
  const job = activity.jobs.get(activity.current);
  if (!job) return;
  const started = new Date(job.created_at);
  const ends = job.steps.map((s) => s.finished_at).filter(Boolean).map((t) => new Date(t));
  const end = job.in_progress ? new Date() : new Date(Math.max(started, ...ends));
  const total = formatDuration((end - started) / 1000);
  const activeIndex = job.steps.findIndex((s) => s.state === 'active');
  $('#activity-sub').textContent = job.in_progress
    ? `Step ${Math.max(activeIndex, 0) + 1} of ${job.steps.length} · ${total} so far`
    : job.status === 'failed' ? `Stopped after ${total}: ${job.message}` : `Finished in ${total}`;
  document.querySelectorAll('#activity-steps .step').forEach((li, i) => {
    const step = job.steps[i];
    if (step) li.querySelector('.step-time').textContent = stepDuration(step);
  });
}

function renderActivityJobs() {
  const box = $('#activity-jobs');
  box.hidden = activity.order.length < 2;
  setHtml(box, activity.order.map((id) => {
    const job = activity.jobs.get(id);
    if (!job) return '';
    const mark = job.in_progress ? 'is-running' : job.status === 'failed' ? 'is-failed' : 'is-done';
    return `<button class="job-chip ${mark}${id === activity.current ? ' is-current' : ''}" type="button" data-job="${id}">@${escapeHtml(job.username)}</button>`;
  }).join(''));
}

function renderSteps(steps) {
  const list = $('#activity-steps');
  steps.forEach((step, i) => {
    let li = list.children[i];
    if (!li || li.dataset.key !== step.key) {
      li = document.createElement('li');
      li.dataset.key = step.key;
      li.innerHTML = `
        <span class="step-icon" aria-hidden="true"></span>
        <div class="step-body">
          <div class="step-row"><span class="step-title"></span><span class="step-time"></span></div>
          <p class="step-detail"></p>
          <div class="step-progress" hidden><span><i></i></span><em></em></div>
        </div>`;
      list.insertBefore(li, list.children[i] || null);
    }
    li.className = `step is-${step.state}`;
    li.querySelector('.step-title').textContent = step.title;
    li.querySelector('.step-detail').textContent =
      step.detail || (step.state === 'pending' ? STEP_WAITING[step.key] || 'Waiting' : '');
    const bar = li.querySelector('.step-progress');
    bar.hidden = !step.total_count;
    if (step.total_count) {
      bar.querySelector('i').style.width = `${Math.round(100 * (step.done_count || 0) / step.total_count)}%`;
      bar.querySelector('em').textContent = `${step.done_count || 0} / ${step.total_count}`;
    }
  });
  while (list.children.length > steps.length) list.lastElementChild.remove();
}

function renderLog(job) {
  const log = $('#debug-log');
  if (activity.logJob !== job.id) {
    log.textContent = '';
    activity.logJob = job.id;
    activity.logCount = 0;
  }
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 12;
  job.events.slice(activity.logCount).forEach((event) => {
    const line = document.createElement('div');
    line.className = `log-line is-${event.kind}`;
    line.innerHTML = '<time></time><span class="log-step"></span><span class="log-msg"></span>';
    line.querySelector('time').textContent = formatClock(event.created_at);
    line.querySelector('.log-step').textContent = event.step;
    line.querySelector('.log-msg').textContent = event.message;
    log.appendChild(line);
  });
  activity.logCount = job.events.length;
  $('#debug-count').textContent = plural(job.events.length, 'line');
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function showActivity() {
  const panel = $('#activity');
  $('#activity-pill').hidden = true;
  panel.hidden = false;
  void panel.offsetWidth; // start from the closed state so it animates open
  panel.classList.add('is-open');
}

function showPill() {
  const panel = $('#activity');
  panel.classList.remove('is-open');
  panel.hidden = true;
  $('#activity-pill').hidden = false;
}

// Close really closes; the Steps button on the Competitors screen opens it again.
function closeActivity() {
  const panel = $('#activity');
  panel.classList.remove('is-open');
  $('#activity-pill').hidden = true;
  setTimeout(() => { if (!panel.classList.contains('is-open')) panel.hidden = true; }, 220);
}

function setDebugOpen(open) {
  $('#debug-log').hidden = !open;
  $('#debug-toggle').setAttribute('aria-expanded', String(open));
  storageSet('debugLog', open ? '1' : '0');
}

// Show fetches that are still running (or waiting for the AI) after a reload.
async function resumeActivity() {
  try {
    const recent = await api('/jobs?limit=3');
    const fresh = recent.filter((job) => isRunning(job) || (job.finished_at && Date.now() - new Date(job.finished_at) < 30 * 60e3));
    const details = await Promise.all(fresh.map((job) => api(`/jobs/${job.id}`)));
    const live = details.filter((job) => job.in_progress).map((job) => job.id).reverse();
    if (live.length) track(live, { reveal: false });
  } catch (e) { /* not important */ }
}

// ---------- Views (Main / Competitors) ----------
// Phones get the editor for a tapped reel, as the preview panel is hidden there.
const phone = window.matchMedia('(max-width: 760px)');

function setView(view) {
  state.view = view;
  document.documentElement.dataset.view = view; // phones show "Add competitor" only on Competitors
  window.scrollTo(0, 0);
  document.querySelectorAll('.nav-item').forEach((btn) => {
    const active = btn.dataset.view === view;
    btn.classList.toggle('is-active', active);
    if (active) btn.setAttribute('aria-current', 'page');
    else btn.removeAttribute('aria-current');
  });
  $('#view-home').hidden = view !== 'home';
  $('#view-main').hidden = view !== 'main';
  $('#view-plan').hidden = view !== 'plan';
  $('#view-competitors').hidden = view !== 'competitors';
  $('#view-settings').hidden = view !== 'settings';
  $('#view-plans').hidden = view !== 'plans';
  $('#view-admin').hidden = view !== 'admin';
  $('#settings-btn').classList.toggle('is-active', view === 'settings');
  if (view === 'competitors') loadCompetitors();
  if (view === 'settings' || view === 'plans') loadSettings();
  if (view === 'admin') loadAdmin();
  if (view === 'plan') loadPlan();
  if (view === 'home') loadIdeas();
}

// ---------- Settings: subscription, overview, admins, AI and account ----------
// Returns false when the server cannot answer yet (it may be waking up).
async function loadMe() {
  try {
    state.me = await api('/me');
  } catch (e) {
    return false;
  }
  renderRole();
  if (state.view === 'settings') renderSettings();
  return true;
}

const isStaff = () => Boolean(state.me && (state.me.role === 'owner' || state.me.role === 'admin'));
const hasActivePlan = (me) => me.subscription_status === 'active' || me.subscription_status === 'trialing';

function pounds(amount) {
  return new Intl.NumberFormat('en-GB', {
    style: 'currency', currency: 'GBP', minimumFractionDigits: amount % 1 ? 2 : 0, maximumFractionDigits: 2,
  }).format(amount);
}

function planName(key) {
  const plan = state.offer && state.offer.plans.find((p) => p.key === key);
  if (plan) return plan.name;
  return key ? key.charAt(0).toUpperCase() + key.slice(1) : 'Start';
}

// What each plan includes, in the order the cards list it.
function planFeatures(p) {
  return [
    `<b>${p.competitors}</b> competitors, each refreshed once a day`,
    `<b>${p.videos}</b> videos analysed a month`,
    `<b>${p.reels.toLocaleString('en-GB')}</b> reels fetched a month`,
    `<b>${p.remixes.toLocaleString('en-GB')}</b> AI remixes in your voice a month`,
    'Transcripts, hooks, scripts and CTAs by AI',
    'Any language, translated into English',
    'Content plan and daily ideas',
    p.exports && 'Export to CSV (Excel, Google Sheets)',
    p.notion && 'Export to Notion',
    p.priority_support && 'Priority support',
  ].filter(Boolean);
}

async function loadSettings() {
  renderSettings();
  const [me, offer] = await Promise.all([
    api('/me').catch(() => null),
    state.offer ? null : api('/billing/plans').catch(() => null),
  ]);
  if (me) state.me = me;
  if (offer) state.offer = offer;
  renderSettings();
}

function renderSettings() {
  const me = state.me;
  if (!me) return;
  const handle = me.telegram_username ? `@${me.telegram_username}` : '';
  const shown = me.name || me.email || handle || 'Your account';
  $('#settings-subtitle').textContent = me.email || handle || shown;
  $('#s-name').textContent = shown;
  $('#s-handle').textContent = me.name ? [handle, me.email].filter(Boolean).join(' · ') : '';
  const avatar = $('#s-avatar');
  avatar.style.backgroundImage = me.avatar_url ? `url("${me.avatar_url}")` : '';
  avatar.textContent = me.avatar_url ? '' : shown.replace(/^@/, '').charAt(0).toUpperCase();
  $('#s-email').textContent = me.email || 'None (you sign in with Telegram)';
  $('#s-telegram').textContent = me.telegram_connected ? (handle || 'Connected') : 'Not connected';
  $('#connect-telegram').hidden = me.telegram_connected;
  $('#s-since').textContent = formatDate(me.created_at);

  renderBilling();
  renderLanguage();
  renderPassword();
  renderVoice();
}

function renderVoice() {
  const box = $('#remix-profile');
  if (document.activeElement !== box) box.value = state.me.remix_profile || '';
  $('#voice-count').textContent = `${box.value.length} / 1000`;
}

async function saveVoice(e) {
  e.preventDefault();
  const button = $('#voice-save');
  button.disabled = true;
  try {
    state.me = await api('/me', { method: 'PATCH', body: { remix_profile: $('#remix-profile').value } });
    renderVoice();
    toast(state.me.remix_profile ? 'Saved. Remixes now sound like you.' : 'Cleared. Remixes will suit the original\'s niche.');
  } catch (err) {
    toast(err.message, { error: true });
  } finally {
    button.disabled = false;
  }
}

function renderPassword() {
  const me = state.me;
  $('#password-form').hidden = !me.email;
  $('#password-submit').textContent = me.has_password ? 'Change password' : 'Set password';
  $('#password-note').textContent = !me.email
    ? 'A password works with an e-mail address; this account signs in with Telegram.'
    : me.has_password ? `You can sign in with ${me.email} and your password.` : 'No password yet: you sign in with a code or Telegram.';
}

async function savePassword(e) {
  e.preventDefault();
  const input = $('#new-password');
  const button = $('#password-submit');
  button.disabled = true;
  try {
    state.me = await api('/me/password', { method: 'POST', body: { password: input.value } });
    input.value = '';
    renderPassword();
    toast('Password saved. Next time you can sign in with your e-mail and it.');
  } catch (err) {
    toast(err.message, { error: true });
  } finally {
    button.disabled = false;
  }
}

// Things only the owner and admins see, wherever they are.
function renderRole() {
  $('#nav-admin').hidden = !isStaff();
  $('#open-admin').hidden = !isStaff();
  document.documentElement.classList.toggle('is-staff', isStaff());
  $('#s-admins').hidden = !state.me || state.me.role !== 'owner';
  $('#s-ai').hidden = !state.me || state.me.role !== 'owner';
}

function renderLanguage() {
  const me = state.me;
  const select = $('#translate-to');
  const options = Object.entries(me.languages || { en: 'British English' })
    .map(([code, name]) => `<option value="${escapeHtml(code)}">${escapeHtml(name)}</option>`).join('');
  setHtml(select, options);
  select.value = me.translate_to || 'en';
}

async function changeLanguage() {
  const select = $('#translate-to');
  const name = select.options[select.selectedIndex].text;
  select.disabled = true;
  try {
    state.me = await api('/me', { method: 'PATCH', body: { translate_to: select.value } });
    toast(`The AI now writes in ${name}. Your reels' scripts are being rewritten; your own edits stay.`);
    setTimeout(() => { loadReels(); loadPlan(); }, 8000);
  } catch (err) {
    toast(err.message, { error: true });
    renderLanguage();
  } finally {
    select.disabled = false;
  }
}

function renderBilling() {
  const me = state.me;
  const active = hasActivePlan(me);
  const failed = me.subscription_status === 'past_due' || me.subscription_status === 'unpaid';
  let status;
  let note;
  const trial = me.allowance && me.allowance.trial;
  if (me.role === 'owner') {
    status = 'Owner: everything is free, with no limits';
    note = 'You run Remixfox, so every feature is on for you and nothing is charged.';
  } else if (me.role === 'admin') {
    status = 'Admin: free, with Studio limits';
    note = 'The owner made you an admin, so you use Remixfox free while you help customers.';
  } else if (active) {
    const period = me.plan_interval === 'year' ? 'yearly' : 'monthly';
    const renews = me.subscription_renews_at ? ` · renews on ${formatDate(me.subscription_renews_at)}` : '';
    status = `${planName(me.plan)} plan, ${period}${renews}`;
    note = 'Switch plans below at any time: Stripe works out the difference for the time left.';
  } else if (failed) {
    status = 'Payment failed';
    note = 'Update your card in “Manage billing and invoices” to keep fetching reels and transcripts.';
  } else if (trial && me.can_fetch) {
    const ends = me.trial_ends_at ? ` · until ${formatDate(me.trial_ends_at)}` : '';
    status = (me.subscription_status === 'canceled' ? 'Cancelled · free trial' : 'Free trial') + ends;
    note = '15 days free: two competitors, the 5 most viewed reels of each, with transcripts and scripts. '
      + (me.billing_ready ? 'Choose a plan whenever you want more.' : 'Plans open soon.');
  } else {
    const ended = trial && me.trial_ends_at && new Date(me.trial_ends_at) <= new Date();
    status = me.subscription_status === 'canceled' ? 'Cancelled' : ended ? 'Free trial ended' : 'Free trial used';
    note = me.billing_ready
      ? 'Choose a plan to track more competitors and get all their best reels, transcripts and scripts.'
      : 'Payments open soon. Until then you can look around.';
  }
  $('#billing-status').textContent = status;
  $('#billing-status').className = `s-status ${me.role !== 'user' || active || (trial && me.can_fetch) ? 'is-ok' : 'is-warn'}`;
  $('#billing-note').textContent = note;
  $('#settings-plan-status').textContent = status;
  $('#settings-plan-status').className = $('#billing-status').className;
  $('#manage-billing').hidden = me.role !== 'user' || !me.subscription_status || !me.billing_ready;
  renderUsage();
  renderPlans();
}

// Bars for what the plan allows and what this month has used.
function renderUsage() {
  const { allowance: allowed, usage, role } = state.me;
  const box = $('#billing-usage');
  box.hidden = !allowed;
  if (!allowed) return;
  const meter = (label, used, limit, hint) => {
    const full = limit != null && used >= limit;
    const share = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
    return `<div class="s-meter${full ? ' is-full' : ''}">
      <div class="s-meter-head"><span>${label}</span><b>${limit == null ? `${used} · no limit` : `${used} of ${limit}`}</b></div>
      ${limit == null ? '' : `<span class="s-meter-bar"><span style="width: ${share}%"></span></span>`}
      <small>${hint}</small>
    </div>`;
  };
  setHtml(box,
    meter('Competitors tracked', usage.competitors, allowed.competitors,
      role === 'owner' ? 'Refresh them as often as you like' : 'Each one can be refreshed once a day')
    + meter(allowed.trial ? 'Videos analysed in the trial' : 'Videos analysed this month', usage.videos, allowed.videos,
      allowed.trial ? 'The free trial includes 10; a plan gives you more every month'
        : `Counts each transcript; starts again on ${formatDate(usage.resets_at)}`)
    + (allowed.reels == null && role !== 'owner' ? '' : meter('Reels fetched this month', usage.reels, allowed.reels,
      allowed.reels == null ? 'Each fetch brings about 20' : `About 20 a fetch; starts again on ${formatDate(usage.resets_at)}`))
    + meter(allowed.trial ? 'AI remixes in the trial' : 'AI remixes this month', usage.remixes, allowed.remixes,
      allowed.trial ? 'The free trial includes 10' : 'Rewrites of a reel in your voice'));
}

function renderPlans() {
  const me = state.me;
  const offer = state.offer;
  const failed = me.subscription_status === 'past_due' || me.subscription_status === 'unpaid';
  const show = Boolean(offer && offer.plans.length > 0);
  $('#billing-plans').hidden = !show;
  if (!show) return;
  // Everyone sees the plans; only customers can pick one, once payments are on and paid up.
  const blocked = me.role !== 'user' ? 'Included free for you' : !me.billing_ready ? 'Opens soon'
    : failed ? 'Update your card first' : null;
  const active = hasActivePlan(me);
  $('#plan-consent').hidden = Boolean(blocked) || active; // only before a first subscription
  const interval = state.interval || (active && me.plan_interval === 'year' ? 'year' : 'month');
  document.querySelectorAll('#billing-interval button').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.interval === interval));
  });
  const current = active ? me.plan || 'start' : null;
  setHtml($('#plan-list'), offer.plans.map((p) => {
    const isCurrent = current === p.key && (me.plan_interval || 'month') === interval;
    const price = interval === 'year'
      ? `${pounds(p.yearly)}<small> a year</small>`
      : `${pounds(p.monthly)}<small> a month</small>`;
    const sub = interval === 'year'
      ? `${pounds(p.yearly_per_month)} a month, billed yearly`
      : `or ${pounds(p.yearly)} a year, saving 20%`;
    const label = blocked || (isCurrent ? 'Your plan' : active ? `Switch to ${p.name}` : `Choose ${p.name}`);
    return `<li class="s-plan${p.key === 'pro' ? ' is-popular' : ''}${isCurrent ? ' is-current' : ''}">
      ${p.key === 'pro' ? '<span class="s-plan-badge">Most popular</span>' : ''}
      <h3>${escapeHtml(p.name)}</h3>
      <p class="s-plan-tagline">${escapeHtml(p.tagline)}</p>
      <p class="s-plan-price">${price}</p>
      <p class="s-plan-sub">${sub}</p>
      <ul class="s-plan-features">${planFeatures(p).map((f) => `<li>${f}</li>`).join('')}</ul>
      <button class="btn ${isCurrent ? 'btn-chip' : p.key === 'pro' ? 'btn-primary' : 'btn-soft'}" type="button"
        data-plan="${escapeHtml(p.key)}"${isCurrent || blocked ? ' disabled' : ''}>${label}</button>
    </li>`;
  }).join(''));
}

// New subscribers go to Stripe's payment page; subscribers switch in place.
async function choosePlan(button) {
  const me = state.me;
  const plan = state.offer.plans.find((p) => p.key === button.dataset.plan);
  const interval = state.interval || (hasActivePlan(me) && me.plan_interval === 'year' ? 'year' : 'month');
  const price = interval === 'year' ? `${pounds(plan.yearly)} a year` : `${pounds(plan.monthly)} a month`;
  if (hasActivePlan(me)
      && !confirm(`Switch to ${plan.name} for ${price}? Stripe works out the difference for the time left on your current plan.`)) return;
  const consent = $('#plan-consent-box').checked;
  if (!hasActivePlan(me) && !consent) {
    const box = $('#plan-consent');
    box.classList.remove('is-asking');
    void box.offsetWidth; // restart the glow
    box.classList.add('is-asking');
    box.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    $('#plan-consent-box').focus({ preventScroll: true });
    toast('Tick the box above the plans first: it agrees to the Terms and starts your plan straight away.');
    return;
  }
  button.disabled = true;
  try {
    const result = await api('/billing/checkout', { method: 'POST', body: { plan: plan.key, interval, consent } });
    if (result.url) {
      location.href = result.url;
      return;
    }
    await loadMe();
    renderSettings();
    toast(`You are on ${plan.name} now. Stripe adjusts your next invoice.`);
  } catch (err) {
    toast(err.message, { error: true });
    button.disabled = false;
  }
}

const SUBSCRIPTION_LABELS = {
  active: 'Subscribed', trialing: 'Trial', past_due: 'Payment failed', unpaid: 'Payment failed', canceled: 'Cancelled',
};

function accountPlan(u) {
  if (u.owner) return 'Owner, everything free';
  if (u.admin) return 'Admin, free';
  if (u.plan) return `${planName(u.plan)}, ${u.plan_interval === 'year' ? 'yearly' : 'monthly'}`;
  return SUBSCRIPTION_LABELS[u.subscription_status] || 'No plan';
}

// Everyone's accounts and the latest fetches, for the owner and admins.
let overviewTimer;
async function loadOverview() {
  clearTimeout(overviewTimer);
  if (!isStaff()) return;
  let o;
  try {
    o = await api('/owner/overview');
  } catch (e) {
    return;
  }
  $('#s-overview').hidden = false;
  const t = o.totals;
  setHtml($('#overview-stats'), [
    [t.users, t.users === 1 ? 'account' : 'accounts'], [t.subscribed, 'subscribed'],
    [t.competitors, t.competitors === 1 ? 'competitor' : 'competitors'], [t.reels, t.reels === 1 ? 'reel' : 'reels'],
    [t.active_jobs, 'fetching now'],
  ].map(([n, label]) => `<div><b>${n}</b><span>${label}</span></div>`).join(''));

  setHtml($('#overview-jobs'), o.jobs.length ? o.jobs.map((j) => `
    <li><button class="s-row" type="button" data-owner-job="${j.id}">
      <span class="s-dot is-${escapeHtml(j.status)}" title="${escapeHtml(j.status)}"></span>
      <span class="s-row-main"><b>@${escapeHtml(j.username)}</b>
        <small>${escapeHtml(j.account || 'from before accounts')} · ${escapeHtml(j.message || j.status)}</small></span>
      <time datetime="${escapeHtml(j.created_at)}">${timeAgo(j.created_at)}</time>
    </button></li>`).join('') : '<li class="s-note">No fetches yet.</li>');

  const tag = (on, text) => (on ? ` <span class="s-tag">${text}</span>` : '');
  setHtml($('#overview-users'), o.users.map((u) => `
    <li class="s-row">
      <span class="s-row-main"><b>${escapeHtml(u.label)}${tag(u.owner, 'Owner')}${tag(u.admin, 'Admin')}${tag(u.telegram, 'Telegram')}</b>
        <small>${escapeHtml(accountPlan(u))} · ${plural(u.competitors, 'competitor')} · ${plural(u.reels, 'reel')}</small></span>
      <time datetime="${escapeHtml(u.created_at)}">joined ${formatDate(u.created_at)}</time>
    </li>`).join(''));

  if (state.view === 'admin') overviewTimer = setTimeout(loadOverview, t.active_jobs ? 5000 : 30000);
}

// ---------- Admins (owner only): up to 10 e-mails ----------
async function loadAdmins() {
  if (!state.me || state.me.role !== 'owner') return;
  let admins;
  try {
    admins = await api('/admins');
  } catch (e) {
    return;
  }
  $('#admin-form').hidden = admins.length >= 10;
  setHtml($('#admin-list'), admins.length ? admins.map((a) => `
    <li class="s-row">
      <span class="s-row-main"><b>${escapeHtml(a.email)}</b>
        <small>${a.has_password ? 'Has a password' : 'No password'} · ${a.signed_up ? 'account ready' : 'not signed in yet'} · added ${formatDate(a.added_at)}</small></span>
      <button class="btn btn-chip is-quiet" type="button" data-admin-password="${escapeHtml(a.email)}">New password</button>
      <button class="btn btn-chip is-quiet" type="button" data-remove-admin="${escapeHtml(a.email)}">Remove</button>
    </li>`).join('') : '<li class="s-note">No admins yet.</li>');
}

async function addAdmin(e) {
  e.preventDefault();
  const input = $('#admin-email');
  const email = input.value.trim();
  const password = $('#admin-password').value;
  if (!email) return;
  const button = $('#admin-form button[type="submit"]');
  button.disabled = true;
  try {
    const added = await api('/admins', { method: 'POST', body: { email, password: password || null } });
    input.value = '';
    $('#admin-password').value = '';
    toast(password
      ? `${added.email} is an admin. Give them the password; they sign in on ${location.host} with that e-mail and it.`
      : `${added.email} is an admin. They sign in on ${location.host} with a code sent to that e-mail.`);
    loadAdmins();
    loadOverview();
  } catch (err) {
    toast(err.message, { error: true });
  } finally {
    button.disabled = false;
  }
}

// A password for an admin: letters and digits that cannot be mixed up (no 0/O, 1/l/I).
function generatePassword() {
  const letters = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const bytes = crypto.getRandomValues(new Uint8Array(14));
  return Array.from(bytes, (b) => letters[b % letters.length]).join('');
}

async function setAdminPassword(email) {
  const password = prompt(`A new password for ${email} (at least 8 characters). Copy it before you press OK:`, generatePassword());
  if (!password) return;
  try {
    await api(`/admins/${encodeURIComponent(email)}/password`, { method: 'PUT', body: { password } });
    toast(`New password saved for ${email}. Give it to them; they can change it in Settings.`);
    loadAdmins();
  } catch (err) {
    toast(err.message, { error: true });
  }
}

async function removeAdmin(email) {
  if (!confirm(`Remove ${email} as an admin? They keep their account, but lose free access and the Overview.`)) return;
  try {
    await api(`/admins/${encodeURIComponent(email)}`, { method: 'DELETE' });
    toast(`${email} is no longer an admin`);
    loadAdmins();
    loadOverview();
  } catch (err) {
    toast(err.message, { error: true });
  }
}

// ---------- The AI (owner only): OpenAI API status ----------
let aiTimer;
async function loadAI() {
  clearTimeout(aiTimer);
  if (!state.me || state.me.role !== 'owner') return;
  let s;
  try {
    s = await api('/ai');
  } catch (e) {
    return;
  }
  $('#ai-status').textContent = s.configured
    ? 'Connected to the OpenAI API'
    : 'No API key yet: add OPENAI_API_KEY in Render (Environment), then deploy';
  $('#ai-status').className = `s-status ${s.configured ? 'is-ok' : 'is-warn'}`;
  $('#ai-model').textContent = `${s.model} · ${s.effort} reasoning`;
  $('#ai-waiting').textContent = s.waiting ? plural(s.waiting, 'transcript') : 'Nothing, all done';
  $('#ai-last').textContent = s.last_run_at ? `${timeAgo(s.last_run_at)}: ${s.last_result}` : 'Not yet since the last restart';
  if (state.view === 'admin' && s.waiting) aiTimer = setTimeout(loadAI, 15000);
}

// ---------- Exports: CSV (Pro and Studio) and Notion (Studio) ----------
async function download(path, button, done) {
  button.disabled = true;
  try {
    const res = await fetch(`/api${path}`);
    if (res.status === 401) {
      location.replace('/welcome.html');
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      toast((data && data.detail) || `Something went wrong (${res.status})`, { error: true });
      if (res.status === 402) showSubscription();
      return;
    }
    const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '');
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = name ? name[1] : 'remixfox-export';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast(done);
  } catch (err) {
    toast('Could not download the file. Try again.', { error: true });
  } finally {
    button.disabled = false;
  }
}

// A paid action was refused (402): show what to do about it.
function showSubscription() {
  if (state.view !== 'plans') setView('plans');
}

async function goToStripe(path, button) {
  const btn = $(button);
  btn.disabled = true;
  try {
    const { url } = await api(path, { method: 'POST' });
    location.href = url;
  } catch (err) {
    toast(err.message, { error: true });
    btn.disabled = false;
  }
}

// Connect Telegram: open the bot, press Start there, and this account gets Telegram sign-in too.
let connectTimer;
async function connectTelegram() {
  const tab = window.open('', '_blank'); // opened during the click, so pop-up blockers allow it
  let start;
  try {
    start = await api('/auth/telegram/start?connect=true', { method: 'POST' });
  } catch (err) {
    if (tab) tab.close();
    toast(err.message, { error: true });
    return;
  }
  if (tab) tab.location.href = start.url;
  $('#tg-connect-link').href = start.url;
  $('#tg-connect-wait').hidden = false;
  $('#connect-telegram').hidden = true;
  const until = new Date(start.expires_at).getTime();
  const done = (message, error) => {
    clearTimeout(connectTimer);
    $('#tg-connect-wait').hidden = true;
    renderSettings();
    if (message) toast(message, { error });
  };
  const poll = async () => {
    if (Date.now() > until) return done('Connecting Telegram timed out. Try again.', true);
    const res = await fetch('/api/auth/telegram/finish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: start.token }),
    }).catch(() => null);
    if (res && res.status === 200) {
      state.me = (await res.json()).me;
      return done('Telegram connected');
    }
    if (res && res.status !== 202) {
      const data = await res.json().catch(() => null);
      return done((data && data.detail) || 'Could not connect Telegram', true);
    }
    connectTimer = setTimeout(poll, 2000);
  };
  poll();
}

async function signOut() {
  try { await api('/auth/logout', { method: 'POST' }); } catch (e) { /* signed out either way */ }
  location.replace('/welcome.html');
}

// After Stripe's pages: ?billing=success|cancelled, or ?view=account from the billing portal.
function handleReturn() {
  const params = new URLSearchParams(location.search);
  const billing = params.get('billing');
  if (!billing && !params.get('view')) return;
  history.replaceState(null, '', '/');
  setView('plans');
  if (billing === 'success') {
    toast('Thank you! Activating your plan…');
    waitForSubscription();
  } else if (billing === 'cancelled') {
    toast('Payment cancelled. You have not been charged.');
  }
}

// A plan picked on the landing page: open it in Settings once, after signing in.
function openChosenPlan() {
  let chosen = null;
  try {
    chosen = JSON.parse(storageGet('chosenPlan') || 'null');
    localStorage.removeItem('chosenPlan');
  } catch (e) { /* nothing chosen */ }
  const me = state.me;
  if (!chosen || !me || me.role !== 'user' || hasActivePlan(me) || !me.billing_ready) return;
  state.interval = chosen.interval === 'year' ? 'year' : 'month';
  setView('plans');
  const focus = async () => {
    for (let i = 0; i < 20 && !document.querySelector(`#plan-list [data-plan="${CSS.escape(chosen.plan)}"]`); i++) await sleep(150);
    const button = document.querySelector(`#plan-list [data-plan="${CSS.escape(chosen.plan)}"]`);
    if (!button) return;
    button.closest('.s-plan').scrollIntoView({ behavior: 'smooth', block: 'center' });
    button.focus({ preventScroll: true });
  };
  focus();
}

// Stripe tells the server a moment later (webhook), so check a few times.
async function waitForSubscription() {
  for (let i = 0; i < 15; i++) {
    await sleep(2000);
    await loadMe();
    if (state.me && hasActivePlan(state.me)) {
      renderSettings();
      toast(`Your ${planName(state.me.plan)} plan is active. Happy remixing!`);
      return;
    }
  }
}

// ---------- Admin panel (owner and admins): money, people, reels and costs ----------
let adminTimer;
async function loadAdmin() {
  clearTimeout(adminTimer);
  if (!isStaff()) {
    setView('home');
    return;
  }
  loadOverview();
  loadAdmins();
  loadAI();
  if (!state.offer) state.offer = await api('/billing/plans').catch(() => null);
  try {
    state.stats = await api('/admin/stats');
  } catch (e) {
    return;
  }
  renderAdmin();
  if (state.view === 'admin') adminTimer = setTimeout(loadAdmin, 60000);
}

function tilesHtml(list) {
  return list.map(([value, label, hint]) => `<div${hint ? ` title="${escapeHtml(hint)}"` : ''}>
    <b>${escapeHtml(String(value))}</b><span>${escapeHtml(label)}</span></div>`).join('');
}

function renderAdmin() {
  const s = state.stats;
  if (!s) return;
  const m = s.money;
  const counted = m.configured && !m.error;
  setHtml($('#admin-money'), tilesHtml([
    [counted ? pounds(m.revenue_this_month) : '–', 'revenue this month'],
    [counted ? pounds(m.revenue) : '–', 'revenue, all time'],
    [counted ? m.payments : '–', m.payments === 1 ? 'payment' : 'payments', 'Paid invoices in Stripe'],
    [pounds(m.mrr), 'a month from active plans', 'MRR: monthly plans, plus yearly plans divided by 12'],
  ]));
  $('#admin-money-note').textContent = !m.configured
    ? 'Stripe is not connected yet (STRIPE_SECRET_KEY), so there are no payments to show.'
    : m.error ? `Could not read the payments from Stripe: ${m.error}`
      : `${plural(m.payments_this_month, 'payment')} this month. From Stripe's paid invoices (test payments while Stripe uses test keys); refreshed every 5 minutes.`;

  const u = s.users;
  setHtml($('#admin-people'), tilesHtml([
    [u.total, u.total === 1 ? 'account' : 'accounts'],
    [u.subscribed, 'on a paid plan'],
    [u.free, 'not paying', 'On the free trial, or cancelled'],
    [u.staff, 'you and admins'],
    [u.new_this_month, 'joined this month'],
  ]));
  const most = Math.max(1, ...s.plans.map((p) => p.month + p.year));
  setHtml($('#admin-plans'), s.plans.map((p) => `
    <li class="a-plan">
      <span class="a-plan-name">${escapeHtml(p.name)}</span>
      <span class="a-plan-bar"><span style="width: ${Math.round(((p.month + p.year) / most) * 100)}%"></span></span>
      <span class="a-plan-count"><b>${p.month + p.year}</b> · ${p.month} monthly, ${p.year} yearly</span>
    </li>`).join(''));

  const c = s.content;
  setHtml($('#admin-reels'), tilesHtml([
    [c.competitors, c.competitors === 1 ? 'competitor' : 'competitors'],
    [c.reels, 'reels saved', `${c.reels_this_month} this month`],
    [c.transcribed, 'transcribed'],
    [c.scripted, 'scripts by the AI'],
    [c.translated, 'translated'],
    [c.planned, 'in content plans'],
    [c.waiting, 'waiting for the AI'],
    [c.failed, 'the AI could not do'],
  ]));

  const month = s.usage.this_month;
  const all = s.usage.all_time;
  const row = (label, a, b, hint) => `<tr${hint ? ` title="${escapeHtml(hint)}"` : ''}><th scope="row">${label}</th><td>${a}</td><td>${b}</td></tr>`;
  setHtml($('#admin-costs'), `<thead><tr><th></th><th scope="col">This month</th><th scope="col">All time</th></tr></thead><tbody>
    ${row('Apify (reels and transcripts)', formatUsd(month.apify_usd), formatUsd(all.apify_usd))}
    ${row('OpenAI (the AI)', formatUsd(month.openai_usd), formatUsd(all.openai_usd))}
    ${row('<b>Total</b>', `<b>${formatUsd(month.total_usd)}</b>`, `<b>${formatUsd(all.total_usd)}</b>`)}
    ${row('Fetches', month.fetches, all.fetches, 'A competitor’s latest reels, about $0.05 each')}
    ${row('Videos transcribed', month.videos, all.videos, 'About $0.05 a minute of speech')}
    ${row('AI remixes', month.remixes, all.remixes, 'Reels rewritten in a user’s voice, about $0.003 each')}
    ${row('OpenAI tokens', month.openai_tokens.toLocaleString('en-GB'), all.openai_tokens.toLocaleString('en-GB'))}
  </tbody>`);
  $('#admin-subtitle').textContent = `Everything happening in Remixfox · updated ${new Date(s.generated_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`;
}

// ---------- The guide: steps that light up what to press next ----------
const TOUR = [
  {
    title: 'Welcome to Remixfox 👋',
    text: () => 'In about a minute you will have your first ideas: add a competitor, get their best reels, and turn them into scripts of your own.',
  },
  {
    title: 'Start with your competitors',
    text: () => 'Open Competitors: this is where the Instagram accounts you want to learn from live.',
    target: '.nav-item[data-view="competitors"]',
    advance: 'click',
  },
  {
    title: 'Paste an Instagram account',
    text: () => 'Paste a link such as instagram.com/name, or just @name, into this box.',
    target: '#competitor-input',
    view: 'competitors',
  },
  {
    title: 'Press Fetch Reels',
    text: () => (state.me && state.me.allowance && state.me.allowance.trial
      ? 'You get their 5 most viewed reels free (for two competitors in your 15-day trial), with transcripts and ready-made scripts. It takes about a minute, and you can watch every step.'
      : 'Remixfox fetches their latest reels, transcribes them and writes the scripts. It takes about a minute, and you can watch every step.'),
    target: '#competitor-form .btn-primary',
    view: 'competitors',
    advance: 'submit',
  },
  {
    title: 'Pick ideas on Home',
    text: () => 'The best reels come first, one at a time. Press ✓ or swipe right to take an idea into your content plan, ✕ or swipe left to skip it.',
    target: '.nav-item[data-view="home"]',
    advance: 'click',
  },
  {
    title: 'Make the script yours',
    text: () => 'On Reels, open any reel and press the pencil: edit the hook, main script and call to action, or press “Remix in my voice” for a version written for your niche. Reels in other languages arrive translated.',
    target: '.nav-item[data-view="main"]',
    advance: 'click',
  },
  {
    title: 'Film from your content plan',
    text: () => 'Everything you take lands here: ❌ not started, ⏳ ready to film, ✅ posted. Move the cards as you film and post.',
    target: '.nav-item[data-view="plan"]',
    advance: 'click',
  },
  {
    title: 'You are all set',
    text: () => 'Want more competitors and reels? Press Plans (by the logo) to choose a plan. Settings can show this guide again.',
  },
];

const tour = { step: -1, frame: 0 };

function tourKey() {
  return `tourDone:${state.me ? state.me.created_at : ''}`;
}

// New accounts (nothing added yet) see the guide once; Settings can show it again.
async function startTour({ force = false } = {}) {
  if (!state.me) return;
  if (!force) {
    if (storageGet(tourKey())) return;
    const competitors = state.competitors.length ? state.competitors : await api('/competitors').catch(() => null);
    if (!competitors || competitors.length) {
      if (competitors) storageSet(tourKey(), '1'); // not new: nothing to guide
      return;
    }
  }
  $('#tour').hidden = false;
  document.documentElement.classList.add('touring');
  showTourStep(0);
}

function endTour() {
  storageSet(tourKey(), '1');
  tour.step = -1;
  document.documentElement.classList.remove('touring');
  $('#tour').classList.add('is-leaving');
  setTimeout(() => {
    $('#tour').hidden = true;
    $('#tour').classList.remove('is-leaving');
  }, 260);
}

function showTourStep(i) {
  if (i < 0) return;
  if (i >= TOUR.length) {
    endTour();
    return;
  }
  tour.step = i;
  const step = TOUR[i];
  if (step.view && state.view !== step.view) setView(step.view);
  if (!step.view && step.target && $('#editor').open) $('#editor').close();
  $('#tour-step').textContent = `Step ${i + 1} of ${TOUR.length}`;
  $('#tour-title').textContent = step.title;
  $('#tour-text').textContent = step.text();
  setHtml($('#tour-dots'), TOUR.map((_, n) => `<i class="${n === i ? 'is-on' : n < i ? 'is-done' : ''}"></i>`).join(''));
  $('#tour-back').hidden = i === 0;
  $('#tour-skip').hidden = i === TOUR.length - 1;
  $('#tour-next').textContent = i === 0 ? 'Show me' : i === TOUR.length - 1 ? 'Done' : 'Next';
  const card = $('#tour-card');
  card.classList.remove('is-in');
  void card.offsetWidth; // replay the entrance
  card.classList.add('is-in');
  positionTour();
  setTimeout(positionTour, 120); // after a view change has laid out
  $('#tour-next').focus({ preventScroll: true });
}

// The first matching element that is on screen (phones and desktops show different ones).
function tourTarget(selector) {
  return [...document.querySelectorAll(selector)].find((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }) || null;
}

function positionTour() {
  if (tour.step < 0) return;
  const step = TOUR[tour.step];
  const spot = $('#tour-spot');
  const card = $('#tour-card');
  const arrow = $('#tour-arrow');
  const el = step.target && tourTarget(step.target);
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const cw = card.offsetWidth;
  const ch = card.offsetHeight;
  const margin = 12;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  if (!el) {
    Object.assign(spot.style, { left: `${vw / 2}px`, top: `${vh / 2}px`, width: '0px', height: '0px' });
    spot.classList.add('is-empty');
    Object.assign(card.style, { left: `${(vw - cw) / 2}px`, top: `${Math.max(margin, (vh - ch) / 2)}px` });
    arrow.dataset.side = 'none';
    return;
  }
  spot.classList.remove('is-empty');
  const box = el.getBoundingClientRect();
  if (box.top < 0 || box.bottom > vh) {
    el.scrollIntoView({ block: 'center' });
    requestAnimationFrame(positionTour);
    return;
  }
  const pad = 8;
  const r = { left: box.left - pad, top: box.top - pad, width: box.width + pad * 2, height: box.height + pad * 2 };
  r.right = r.left + r.width;
  r.bottom = r.top + r.height;
  Object.assign(spot.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });

  const gap = 18;
  let side;
  let x;
  let y;
  if (vw >= 700 && r.right + gap + cw + margin <= vw) {
    side = 'left'; // the arrow is on the card's left, pointing at the target
    x = r.right + gap;
    y = clamp(r.top + r.height / 2 - ch / 2, margin, vh - ch - margin);
  } else if (r.bottom + gap + ch + margin <= vh) {
    side = 'top';
    y = r.bottom + gap;
    x = clamp(r.left + r.width / 2 - cw / 2, margin, vw - cw - margin);
  } else {
    side = 'bottom';
    y = Math.max(margin, r.top - gap - ch);
    x = clamp(r.left + r.width / 2 - cw / 2, margin, vw - cw - margin);
  }
  Object.assign(card.style, { left: `${x}px`, top: `${y}px` });
  arrow.dataset.side = side;
  if (side === 'left') arrow.style.cssText = `top: ${clamp(r.top + r.height / 2 - y, 22, ch - 22)}px`;
  else arrow.style.cssText = `left: ${clamp(r.left + r.width / 2 - x, 22, cw - 22)}px`;
}

function onTourResize() {
  cancelAnimationFrame(tour.frame);
  tour.frame = requestAnimationFrame(positionTour);
}

// Doing what a step asks (pressing the lit tab, fetching) moves the guide on.
function onTourAction(e) {
  if (tour.step < 0) return;
  const step = TOUR[tour.step];
  if (!step.target || e.target.closest('#tour-card')) return;
  if (step.advance === e.type && e.target.closest(step.target)) setTimeout(() => showTourStep(tour.step + 1), 260);
  else if (step.advance === 'submit' && e.type === 'submit' && e.target.id === 'competitor-form') {
    setTimeout(() => showTourStep(tour.step + 1), 900);
  }
}

function bindTour() {
  $('#tour-next').addEventListener('click', () => showTourStep(tour.step + 1));
  $('#tour-back').addEventListener('click', () => showTourStep(tour.step - 1));
  $('#tour-skip').addEventListener('click', endTour);
  document.addEventListener('click', onTourAction, true);
  document.addEventListener('submit', onTourAction, true);
  window.addEventListener('resize', onTourResize);
  window.addEventListener('scroll', onTourResize, true);
  document.addEventListener('keydown', (e) => {
    if (tour.step >= 0 && e.key === 'Escape') endTour();
  });
}

// ---------- Theme ----------
// Light by default, as in the design reference; dark only when chosen.
function effectiveTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

function syncThemeButton() {
  const dark = effectiveTheme() === 'dark';
  const btn = $('#theme-toggle');
  btn.querySelector('use').setAttribute('href', dark ? '#i-moon' : '#i-sun');
  btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
}

// ---------- Preview actions ----------
function selectedReelLink() {
  const reel = findReel(state.selectedId);
  if (reel && reel.url) return reel.url;
  toast('Choose a reel first');
  return null;
}

async function copyLink() {
  const url = selectedReelLink();
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    toast('Link copied');
  } catch (e) {
    toast(url);
  }
}

function download() {
  const reel = findReel(state.selectedId);
  if (reel && reel.video_url) window.open(reel.video_url, '_blank', 'noopener');
  else toast('This reel has no video link yet');
}

// ---------- Events ----------
function bindEvents() {
  $('#reel-grid').addEventListener('click', (e) => {
    const plan = e.target.closest('[data-plan]');
    if (plan) {
      const id = Number(plan.dataset.plan);
      const reel = findReel(id);
      if (reel && reel.plan_status) showInPlan(id);
      else addToPlan(id);
      return;
    }
    const remix = e.target.closest('[data-remix]');
    if (remix) {
      selectReel(Number(remix.dataset.remix));
      openEditor(Number(remix.dataset.remix));
      return;
    }
    const card = e.target.closest('.reel[data-id]');
    if (card) {
      selectReel(Number(card.dataset.id));
      if (phone.matches) openEditor(Number(card.dataset.id));
    }
  });

  $('#competitor-list').addEventListener('click', (e) => {
    const fetchBtn = e.target.closest('[data-fetch]');
    if (fetchBtn) fetchCompetitor(Number(fetchBtn.dataset.fetch));
    const deleteBtn = e.target.closest('[data-delete]');
    if (deleteBtn) deleteCompetitor(Number(deleteBtn.dataset.delete));
    const progressBtn = e.target.closest('[data-progress]');
    if (progressBtn) track([Number(progressBtn.dataset.progress)]);
  });

  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  });

  $('#refresh').addEventListener('click', refreshAll);
  $('#sort-toggle').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-sort]');
    if (btn && btn.dataset.sort !== state.sort) setSort(btn.dataset.sort);
  });
  $('#competitor-form').addEventListener('submit', addCompetitor);
  $('#competitor-input').addEventListener('input', (e) => e.target.removeAttribute('aria-invalid'));

  $('#theme-toggle').addEventListener('click', () => {
    const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    storageSet('theme', next);
    syncThemeButton();
  });

  $('#preview-expand').addEventListener('click', () => {
    const cover = $('#preview-cover');
    if (document.fullscreenElement) document.exitFullscreen();
    else if (cover.requestFullscreen) cover.requestFullscreen().catch(() => {});
  });

  $('#open-instagram').addEventListener('click', () => {
    const url = selectedReelLink();
    if (url) window.open(url, '_blank', 'noopener');
  });
  $('#edit-reel').addEventListener('click', () => openEditor(state.selectedId));
  $('#plan-toggle').addEventListener('click', () => {
    const reel = findReel(state.selectedId);
    if (!reel) return;
    if (reel.plan_status) showInPlan(reel.id);
    else addToPlan(reel.id);
  });

  // Emoji statuses and "Add to content plan" work the same everywhere.
  document.addEventListener('click', (e) => {
    const status = e.target.closest('[data-set-status]');
    if (status) setPlanStatus(Number(status.dataset.reel), status.dataset.setStatus);
    const add = e.target.closest('[data-add-plan]');
    if (add) addToPlan(Number(add.dataset.addPlan));
    const remove = e.target.closest('[data-remove-plan]');
    if (remove && !remove.closest('#kanban')) removeFromPlan(Number(remove.dataset.removePlan));
  });
  bindKanban();
  $('#swipe-take').addEventListener('click', () => decide('take'));
  $('#swipe-skip').addEventListener('click', () => decide('skip'));
  $('#swipe-edit').addEventListener('click', () => decide('take', { edit: true }));
  $('#swipe-undo').addEventListener('click', undoSwipe);
  document.addEventListener('keydown', (e) => {
    if (state.view !== 'home' || $('#editor').open || e.target.closest('input, textarea')) return;
    const keys = { ArrowRight: () => decide('take'), ArrowLeft: () => decide('skip'),
      ArrowUp: () => decide('take', { edit: true }), Backspace: undoSwipe };
    if (keys[e.key]) {
      e.preventDefault();
      keys[e.key]();
    }
  });
  $('#copy-link').addEventListener('click', copyLink);
  $('#download').addEventListener('click', download);

  const editor = $('#editor');
  $('#editor-form').addEventListener('submit', saveEditor);
  $('#get-transcript').addEventListener('click', getTranscript);
  $('#copy-transcript').addEventListener('click', copyTranscript);
  $('#reset-version').addEventListener('click', resetVersion);
  $('#remix-ai').addEventListener('click', remixReel);
  $('#voice-form').addEventListener('submit', saveVoice);
  $('#remix-profile').addEventListener('input', () => {
    $('#voice-count').textContent = `${$('#remix-profile').value.length} / 1000`;
  });
  $('#editor-form').addEventListener('input', () => { $('#editor-form').dataset.touched = '1'; });
  $('#preview-open').addEventListener('click', (e) => {
    if (!e.currentTarget.hasAttribute('href')) selectedReelLink();
  });
  editor.querySelectorAll('[data-close]').forEach((btn) => btn.addEventListener('click', () => editor.close()));
  editor.addEventListener('click', (e) => { if (e.target === editor) editor.close(); }); // backdrop

  $('#toast').addEventListener('click', hideToast);
  $('#activity-close').addEventListener('click', closeActivity);
  $('#activity-minimise').addEventListener('click', showPill);
  $('#activity-pill').addEventListener('click', showActivity);
  $('#debug-toggle').addEventListener('click', () => setDebugOpen($('#debug-log').hidden));
  $('#activity-jobs').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-job]');
    if (!chip) return;
    activity.current = Number(chip.dataset.job);
    renderActivity();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('#activity').hidden && !$('#editor').open) closeActivity();
  });

  $('#settings-btn').addEventListener('click', () => setView('settings'));
  $('#connect-telegram').addEventListener('click', connectTelegram);
  $('#sign-out').addEventListener('click', signOut);
  $('#manage-billing').addEventListener('click', () => goToStripe('/billing/portal', '#manage-billing'));
  $('#billing-interval').addEventListener('click', (e) => {
    const button = e.target.closest('[data-interval]');
    if (!button) return;
    state.interval = button.dataset.interval;
    renderPlans();
  });
  $('#plan-list').addEventListener('click', (e) => {
    const button = e.target.closest('[data-plan]');
    if (button && !button.disabled) choosePlan(button);
  });
  $('#admin-form').addEventListener('submit', addAdmin);
  $('#admin-refresh').addEventListener('click', loadAdmin);
  $('#open-admin').addEventListener('click', () => setView('admin'));
  $('#translate-to').addEventListener('change', changeLanguage);
  $('#replay-guide').addEventListener('click', () => startTour({ force: true }));
  $('#account-filter').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-account]');
    if (chip) toggleAccount(chip.dataset.account);
  });
  $('#admin-list').addEventListener('click', (e) => {
    const remove = e.target.closest('[data-remove-admin]');
    if (remove) removeAdmin(remove.dataset.removeAdmin);
    const password = e.target.closest('[data-admin-password]');
    if (password) setAdminPassword(password.dataset.adminPassword);
  });
  $('#admin-generate').addEventListener('click', () => { $('#admin-password').value = generatePassword(); });
  $('#password-form').addEventListener('submit', savePassword);
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-open-plans]')) setView('plans');
  });
  $('#caption-toggle').addEventListener('click', () => {
    const reel = findReel(state.editingId);
    if (reel) renderCaption(reel, !$('#caption-toggle').dataset.original);
  });
  $('#export-csv').addEventListener('click', (e) => download('/export/reels.csv', e.currentTarget,
    'Downloaded. Open it in Excel or Google Sheets.'));
  $('#export-notion').addEventListener('click', (e) => download('/export/plan.md', e.currentTarget,
    'Downloaded. In Notion choose Import, then Text & Markdown.'));
  // The logo and the name take you Home from any screen.
  $('#brand').addEventListener('click', (e) => {
    e.preventDefault();
    setView('home');
  });
  $('#overview-jobs').addEventListener('click', (e) => {
    const row = e.target.closest('[data-owner-job]');
    if (row) track([Number(row.dataset.ownerJob)]);
  });
}

// ---------- Init ----------
async function init() {
  // Browsers can restore a typed value after a reload; the field starts empty.
  $('#competitor-input').value = '';
  syncThemeButton();
  setDebugOpen(storageGet('debugLog') !== '0'); // debug log open unless closed before
  state.sort = storageGet('sort') === 'latest' ? 'latest' : 'views';
  try { state.accounts = (JSON.parse(storageGet('accounts') || '[]') || []).filter(Number.isInteger); } catch (e) { state.accounts = []; }
  syncSortButtons();
  bindEvents();
  bindTour();
  state.view = 'home';
  renderPreview();
  await firstLoad();
  document.documentElement.classList.add('is-ready');
  loadIdeas();
  loadPlan();
  loadCompetitors();
  resumeActivity();
  handleReturn();
  openChosenPlan();
  startTour();
}

init();
