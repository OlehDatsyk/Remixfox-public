// The welcome (landing) page: sign-in by e-mail code, password or Telegram, the
// live plans, and the page's animations. No framework, no build step.
'use strict';
(function () {
  const $ = (sel) => document.querySelector(sel);
  let email = '';
  let step = 'email';

  function showError(message) {
    $('#auth-error').textContent = message;
    $('#auth-error').hidden = !message;
  }

  function problem(err) {
    return err.message === 'Failed to fetch' ? 'The server is waking up. Try again in a few seconds.' : err.message;
  }

  async function post(path, body) {
    const res = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const detail = data && data.detail;
      const error = new Error(typeof detail === 'string' ? detail
        : res.status === 422 ? 'Enter a valid e-mail address.' : `Something went wrong (${res.status}). Try again.`);
      error.status = res.status;
      throw error;
    }
    return { status: res.status, data };
  }

  // ---------- Signing in: e-mail and a code, or e-mail and a password ----------
  // Steps: 'email' (send a code), 'code' (type it), 'password' (sign in with one),
  // 'newpass' (a new account may add a password before going in).
  const STEPS = {
    email: { fields: ['#email-field'], button: 'E-mail me a code', note: () => 'No password needed: we e-mail you a 6-digit code.' },
    code: { fields: ['#code-field', '#code-links'], button: 'Sign in',
      note: () => `We sent a code to ${email}. It works for 10 minutes; check spam if it is not there.` },
    password: { fields: ['#email-field', '#password-field', '#password-links'], button: 'Sign in',
      note: () => 'Sign in with your e-mail and the password you set (admins: the one the owner gave you).' },
    newpass: { fields: ['#newpass-field', '#newpass-links'], button: 'Save password and continue',
      note: () => 'You are in! Add a password to sign in faster next time, or skip it.' },
  };
  const FIELDS = ['#email-field', '#code-field', '#code-links', '#password-field', '#password-links', '#newpass-field', '#newpass-links'];

  function setStep(next) {
    step = next;
    FIELDS.forEach((sel) => { $(sel).hidden = !STEPS[next].fields.includes(sel); });
    $('#auth-mode').hidden = next === 'code' || next === 'newpass';
    $('#telegram-btn').hidden = next === 'newpass';
    $('.w-or').hidden = next === 'newpass';
    document.querySelectorAll('#auth-mode [data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === next)));
    $('#auth-title').textContent = next === 'newpass' ? 'Welcome to Remixfox' : 'Sign in or create an account';
    $('#auth-submit').textContent = STEPS[next].button;
    $('#auth-note').textContent = STEPS[next].note();
    showError('');
    ({ email: $('#email'), code: $('#code'), password: $('#email').value ? $('#password') : $('#email'), newpass: $('#new-password') })[next].focus();
  }

  async function sendCode() {
    email = $('#email').value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Error('Enter a valid e-mail address.');
    try {
      await post('/api/auth/email/start', { email });
    } catch (err) {
      if (err.status !== 502) throw err;
      // The code exists but the e-mail failed: offer "Send a new code" from the code step.
      $('#code').value = '';
      setStep('code');
      $('#auth-note').textContent = `We could not e-mail a code to ${email} just now.`;
      throw err;
    }
    $('#code').value = '';
    setStep('code');
  }

  async function verify() {
    const code = $('#code').value.replace(/\D/g, '');
    if (code.length !== 6) throw new Error('Enter the 6-digit code from the e-mail.');
    const { data: me } = await post('/api/auth/email/verify', { email, code });
    // A brand-new account may add a password now; everyone else goes straight in.
    const isNew = me && Date.now() - new Date(me.created_at).getTime() < 10 * 60 * 1000;
    if (isNew && !me.has_password && me.email) setStep('newpass');
    else location.replace('/');
  }

  async function passwordSignIn() {
    email = $('#email').value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Error('Enter a valid e-mail address.');
    if (!$('#password').value) throw new Error('Enter your password.');
    await post('/api/auth/password/login', { email, password: $('#password').value });
    location.replace('/');
  }

  async function savePassword() {
    const password = $('#new-password').value;
    if (password.length < 8) throw new Error('Use at least 8 characters, or skip it.');
    await post('/api/me/password', { password });
    location.replace('/');
  }

  const ACTIONS = { email: sendCode, code: verify, password: passwordSignIn, newpass: savePassword };
  const BUSY = { email: 'Sending the code…', code: 'Signing in…', password: 'Signing in…', newpass: 'Saving…' };

  $('#email-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const submit = $('#auth-submit');
    const label = submit.textContent;
    submit.disabled = true;
    submit.textContent = BUSY[step];
    try {
      await ACTIONS[step]();
    } catch (err) {
      showError(problem(err));
    } finally {
      submit.disabled = false;
      if (submit.textContent.endsWith('…')) submit.textContent = label;
    }
  });
  // Six digits typed (or pasted from the e-mail): sign in straight away.
  $('#code').addEventListener('input', () => {
    if ($('#code').value.replace(/\D/g, '').length === 6) $('#email-form').requestSubmit();
  });
  $('#auth-mode').addEventListener('click', (e) => {
    const button = e.target.closest('[data-mode]');
    if (button) setStep(button.dataset.mode);
  });
  $('#change-email').addEventListener('click', () => setStep('email'));
  $('#resend-code').addEventListener('click', () => sendCode().catch((err) => showError(problem(err))));
  $('#use-code').addEventListener('click', () => setStep('email'));
  $('#skip-password').addEventListener('click', () => location.replace('/'));

  // ---------- Telegram: open the bot, press Start, this page signs in ----------
  let tgToken = null;
  let tgTimer = null;

  function stopTelegram() {
    clearTimeout(tgTimer);
    tgToken = null;
    $('#tg-wait').hidden = true;
    $('#telegram-btn').hidden = false;
  }

  async function poll(until) {
    if (!tgToken) return;
    if (Date.now() > until) {
      stopTelegram();
      showError('The Telegram sign-in expired. Press “Continue with Telegram” again.');
      return;
    }
    try {
      const { status } = await post('/api/auth/telegram/finish', { token: tgToken });
      if (status === 200) {
        location.replace('/');
        return;
      }
    } catch (err) {
      if (err.status) { // a real answer (expired, not found...); network blips just retry
        stopTelegram();
        showError(problem(err));
        return;
      }
    }
    tgTimer = setTimeout(() => poll(until), 2000);
  }

  $('#telegram-btn').addEventListener('click', async () => {
    const tab = window.open('', '_blank'); // opened during the click, so pop-up blockers allow it
    showError('');
    try {
      const { data } = await post('/api/auth/telegram/start', {});
      tgToken = data.token;
      $('#tg-link').href = data.url;
      if (tab) tab.location.href = data.url;
      $('#telegram-btn').hidden = true;
      $('#tg-wait').hidden = false;
      poll(new Date(data.expires_at).getTime());
    } catch (err) {
      if (tab) tab.close();
      showError(problem(err));
    }
  });
  $('#tg-cancel').addEventListener('click', stopTelegram);

  // ---------- Smooth scrolling: "How it works", "Pricing", "Sign in" and the buttons glide there ----------
  let glideFrame = 0;
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

  // Scroll to a part of the page with an eased glide (a jump when the system asks for less motion).
  function glideTo(target, { centre = false } = {}) {
    cancelAnimationFrame(glideFrame);
    const header = $('.w-top').getBoundingClientRect().height;
    const box = target.getBoundingClientRect();
    const from = window.scrollY;
    const fits = box.height < window.innerHeight - header - 32;
    const offset = target.id === 'top' ? -from
      : centre && fits ? box.top - header - (window.innerHeight - header - box.height) / 2
        : box.top - header - 12;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    const to = Math.max(0, Math.min(max, from + offset));
    const distance = to - from;
    if (still || Math.abs(distance) < 2) {
      window.scrollTo(0, to);
      return Promise.resolve();
    }
    const duration = Math.max(700, Math.min(1200, Math.abs(distance) * 0.5)); // long enough to see, never slow
    const started = performance.now();
    return new Promise((resolve) => {
      const step = (now) => {
        const t = Math.min(1, (now - started) / duration);
        window.scrollTo(0, from + distance * easeInOut(t));
        if (t < 1) glideFrame = requestAnimationFrame(step);
        else resolve();
      };
      glideFrame = requestAnimationFrame(step);
    });
  }
  // Scrolling yourself (wheel, touch, a click elsewhere) takes over from a glide.
  ['wheel', 'touchstart', 'mousedown'].forEach((type) => {
    window.addEventListener(type, () => cancelAnimationFrame(glideFrame), { passive: true });
  });

  // The sign-in card: glide to it, then a soft glow says "here", and the cursor waits in the box.
  async function goToAuth() {
    await glideTo($('#auth'), { centre: true });
    const card = $('#auth');
    card.classList.remove('is-called');
    void card.offsetWidth;
    card.classList.add('is-called');
    $(step === 'code' ? '#code' : step === 'newpass' ? '#new-password' : '#email').focus({ preventScroll: true });
  }

  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href^="#"]');
    if (!link || link.closest('#plans') || e.defaultPrevented) return;
    const hash = link.getAttribute('href');
    const target = hash.length > 1 && document.getElementById(hash.slice(1));
    if (!target) return;
    e.preventDefault();
    history.replaceState(null, '', hash === '#top' ? location.pathname + location.search : hash);
    if (link.hasAttribute('data-to-auth')) goToAuth();
    else glideTo(target);
  });

  if (new URLSearchParams(location.search).get('telegram') === 'expired') {
    showError('That sign-in button from the bot has expired or was used. Send /login to the bot for a new one.');
  }
  fetch('/api/me').then((res) => { if (res.ok) location.replace('/'); }).catch(() => {});

  // ---------- Pricing: the three plans, live from /api/billing/plans ----------
  const pounds = (n) => new Intl.NumberFormat('en-GB', {
    style: 'currency', currency: 'GBP', minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2,
  }).format(n);
  const escapeHtml = (v) => String(v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const check = '<svg class="icon" aria-hidden="true"><use href="#w-check"/></svg>';
  let offer = null;
  let interval = 'month';

  function features(p) {
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

  function renderPlans() {
    if (!offer) return;
    document.querySelectorAll('#interval button').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.interval === interval));
    });
    $('#plans').innerHTML = offer.plans.map((p) => {
      const yearly = interval === 'year';
      return `<li class="w-plan${p.key === 'pro' ? ' is-popular' : ''}">
        ${p.key === 'pro' ? '<span class="w-plan-badge">Most popular</span>' : ''}
        <h3>${escapeHtml(p.name)}</h3>
        <p class="w-plan-tag">${escapeHtml(p.tagline)}</p>
        <p class="w-amount">${pounds(yearly ? p.yearly : p.monthly)}<small> / ${yearly ? 'year' : 'month'}</small></p>
        <p class="w-small">${yearly ? `${pounds(p.yearly_per_month)} a month, billed yearly` : `or ${pounds(p.yearly)} a year, saving 20%`}</p>
        <ul class="w-points">${features(p).map((f) => `<li>${check}<span>${f}</span></li>`).join('')}</ul>
        <a class="btn ${p.key === 'pro' ? 'btn-primary' : 'btn-soft'} w-cta" href="#connect" data-plan="${escapeHtml(p.key)}">Choose ${escapeHtml(p.name)}</a>
      </li>`;
    }).join('');
  }

  $('#interval').addEventListener('click', (e) => {
    const button = e.target.closest('[data-interval]');
    if (!button) return;
    interval = button.dataset.interval;
    renderPlans();
  });
  // A plan picked here opens in Settings straight after signing in.
  $('#plans').addEventListener('click', (e) => {
    const link = e.target.closest('[data-plan]');
    if (!link) return;
    e.preventDefault();
    try { localStorage.setItem('chosenPlan', JSON.stringify({ plan: link.dataset.plan, interval })); } catch (err) { /* optional */ }
    goToAuth();
  });
  fetch('/api/billing/plans').then((res) => (res.ok ? res.json() : null)).then((data) => {
    if (!data) return;
    offer = data;
    renderPlans();
  }).catch(() => {});

  // ---------- Animations (all skipped when the system asks for less motion) ----------
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Hero: "Get ___ from reels", the word typed and erased in turn.
  const typed = $('#typed');
  const words = ['views', 'leads', 'clients'];
  if (typed && !still) {
    let w = 0;
    let n = words[0].length;
    let erasing = false;
    const tick = () => {
      if (!erasing && n === words[w].length) {
        erasing = true;
        setTimeout(tick, 1800);
        return;
      }
      if (erasing && n === 0) {
        erasing = false;
        w = (w + 1) % words.length;
        setTimeout(tick, 260);
        return;
      }
      n += erasing ? -1 : 1;
      typed.textContent = words[w].slice(0, n);
      setTimeout(tick, erasing ? 55 : 110);
    };
    setTimeout(tick, 1800);
  }

  // The demo card: views count up, then the remix writes itself, and again.
  function compact(v) {
    if (v >= 1e6) return `${(v / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
    if (v >= 1e3) return `${Math.round(v / 1e3)}K`;
    return String(Math.round(v));
  }
  function countUp(el, to, ms) {
    const start = performance.now();
    const frame = (now) => {
      const t = Math.min(1, (now - start) / ms);
      el.textContent = compact(to * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  async function typeInto(el, text) {
    for (let i = 1; i <= text.length; i++) {
      el.textContent = text.slice(0, i);
      await wait(24);
    }
  }
  async function playDemo() {
    const rows = [...document.querySelectorAll('.d-row em')];
    for (;;) {
      document.querySelectorAll('.d-views').forEach((el) => countUp(el, Number(el.dataset.to), 1400));
      rows.forEach((el) => { el.textContent = ''; });
      await wait(900);
      for (const el of rows) await typeInto(el, el.dataset.text);
      await wait(4200);
    }
  }
  if (!still && document.querySelector('.d-card')) playDemo();

  // Sections rise into view as they are scrolled to.
  const reveals = document.querySelectorAll('.reveal');
  if (!('IntersectionObserver' in window) || still) {
    reveals.forEach((el) => el.classList.add('is-in'));
  } else {
    const seen = new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-in');
        seen.unobserve(entry.target);
      }
    }), { rootMargin: '0px 0px -8% 0px' });
    reveals.forEach((el) => seen.observe(el));
  }
})();
