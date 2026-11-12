# Testing

## Automated tests

```bash
docker compose up -d db
pip install -r requirements-dev.txt
pytest
```

They run against a real PostgreSQL database (`remixfox_test`, created if needed, migrated from scratch), sign in as `owner@example.com`, and fake Apify, Stripe, OpenAI, Resend and Telegram (`fake_apify`, `stripe_ready`, `fake_ai`, `SENT_CODES`, `bot`). GitHub Actions runs them before every deploy.

| File | Covers |
|---|---|
| `test_accounts.py` | E-mail codes, each user's own data, the 15-day trial (two competitors, 5 best reels each, its end), security headers, what search engines see (structured data prices, `noindex`, the sitemap), the 404 page |
| `test_passwords.py` | Setting a password, signing in with it, hashing, wrong-password limits, admin passwords |
| `test_telegram.py` | Telegram sign-in and connecting; the bot token kept out of the log; the per-network limit and tidying old sign-ins |
| `test_billing.py` | Plans in pounds, Stripe prices, Checkout (with the Terms box, its record in Stripe and the billing address), switching, the webhook, failed payments |
| `test_limits.py` | Competitors, videos and reels per month, daily fetches, deleting does not give usage back, admins, exports, the reel filter, the admin statistics, the profit check |
| `test_pipeline.py` | The fetch pipeline step by step |
| `test_translations.py` | The AI queue, languages (no Polish, Russian or Belarusian), captions, checks, OpenAI requests and errors, tokens and cost |
| `test_plan.py` | The content plan |
| `test_remix.py` | The AI remix: your voice in the prompt, your language, nothing saved, needs a transcript (or a caption without speech), remixes per plan and trial, errors, other users' reels |
| `test_api.py`, `test_apify.py` | The rest of the API (competitors, reels, covers, jobs), the Apify client |

## End-to-end checklist

Run it locally in Docker (sign-in codes appear in `docker compose logs web` when Resend is not set) at three sizes: desktop 1549 × 1015, tablet 768 × 1024, phone 375 × 812. Last full run: 9 November 2026, all passed.

1. **Register with a code**: the landing page → e-mail → code from the log → "Create a password (optional)" → Save → the app opens on the guide; Settings shows "Free trial · until <15 days later>", 0 of 2 competitors, 0 of 10 videos.
2. **Password sign-in**: sign out → Password tab → a wrong password shows "Wrong e-mail or password" → the right one opens the app.
3. **Guide**: steps 1 to 8 light the Competitors tab, the link box, Fetch Reels, Home, Reels and Content plan; clicking a lit tab moves on; on a phone the card sits above or below the target.
4. **Plans page**: the Plans pill (by the logo; in the top bar on phones) opens the three plans with Monthly / Yearly and the usage bars; Settings → Subscription → Plans and prices opens it too.
5. **Admin with a password**: as the owner, Admin panel → Admins → e-mail + Generate (or type) → Add admin → "Has a password"; sign out; sign in with that e-mail and password → role admin, the Admin panel shows money, people, reels, costs and accounts, but not the Admins or AI cards.
6. **Content plan**: drag a card from Not started to Ready to film → it lifts and follows the pointer, above the other columns' cards; drag it past the board's edges → it stays inside the board, uncut, and lands in the nearest column; a slot opens at the top of the target column, it lands there and "⏳ Ready to film" shows; Esc cancels; Shift + → moves a focused card; on a phone, press and hold to drag.
7. **Home**: ✓ and swipe right add the idea to the content plan ("Added to your content plan, under Not started"); ✕ and swipe left skip; the buttons are on screen at all three sizes.
8. **Reels filter**: pick one account, then two, then All accounts; the subtitle follows.
9. **AI remix**: Settings → Your voice → write and Save ("x / 1000"); open a reel without a transcript → "Remix in my voice" is disabled with a note; open one with a transcript → press it → "Remixing… about 20 seconds" → the hook, script and call to action fill in your voice; nothing is saved until Save; the Plans page shows the remixes used.
10. **Deleting a competitor** removes its reels from the feed, Home, the content plan and the preview.
11. **FAQ**: the header's FAQ link glides to "Good to know"; each question opens and closes (also with Enter / Space), the + turns into ×.
12. **Legal**: the app footer, the landing footer and the legal pages link Legal notice, Privacy policy, Terms of use and Cookies; every page opens (200) and has no placeholders left.
13. **No sideways scrolling** on any screen at tablet and phone sizes.

Fetching real reels costs Apify credit, so locally use the test data (or a competitor inserted into the local database) unless a real fetch is the point of the test.

## Layout audit (every screen, every size)

Last run 9 November 2026 on the local copy: the app's seven screens plus the editor and the activity panel, the landing page and the three legal pages, at 1549 × 1015, 1280 × 720, 1024 × 768, 768 × 1024, 768 × 600, 414 × 896, 375 × 812, 360 × 640 and 320 × 568, in light and dark. A script in the page checked for sideways scrolling, anything off screen, clipped text, buttons covered by something else, tap targets under 30px on phones, and text with less than 3 : 1 contrast. It found and fixed: the swipe buttons under the tab bar on short phones and a short tablet window, the stacked cards covering the buttons' edge, small footer links and text buttons on phones, the faint "−20%" badge, and the light Telegram button (white text 2.6 : 1, now 4.9 : 1). Links inside sentences (e-mail addresses, "Terms of use" in a line of text) are exempt, as the accessibility rules allow.

## Final audit (10-11 November 2026)

What was run, on the local copy unless it says live:

- **A new customer's journey**: register with a code, add a password, the guide, sign out, a wrong password and an unknown e-mail (the same answer), sign in with the password; add a competitor with a real Apify fetch (39 s, about $0.05, 20 returned, the 5 most viewed kept on the trial, covers saved); a second fetch the same day (429); one real transcript (1 of 10 videos); the AI remix without a key (503, a clear message); the content plan, skip, exports on the trial (402), the Admin panel as a customer (403), another user's reel (404); delete the competitor (reels and plan gone, the used video still counted).
- **The owner**: Admin statistics, CSV and Notion exports, accounts overview, admins, the AI status.
- **The Terms box**: shown before a first subscription; "Choose" without it glows, focuses it and explains; with it, the request carries `consent`. A real Stripe payment was not made (no Stripe keys locally; live payments are the owner's to test).
- **Layout**: every screen, the editor and the activity panel at 1549 x 1015, 768 x 1024, 375 x 812 and 320 x 568, and the landing page in both themes: no problems. (In a hidden browser pane colour transitions freeze halfway, which can make the contrast check flag buttons; with transitions off they pass.)
- **Performance (live, headless Edge, no throttling)**: the landing page's largest paint in about 0.2 s on desktop and phone, no layout shift, about 61 KB in 8 requests; the privacy page about 50 KB. The free Render plan's 50-second wake-up after 15 idle minutes is the real delay.
- **Animation smoothness (real mouse input, headless Edge)**: the app's own code stays under 50 ms in every interaction (the editor opening is the largest, 44 ms); the browser's largest layout is about 78 ms the first time a big screen appears; re-sorting, the editor, dragging and swiping run without long tasks once warm. Headless Edge draws without a graphics card, so its frame times are pessimistic.
- **Security**: the git history and `pip-audit` (nothing found), headers and the Content Security Policy (no violations on any screen), `ruff` with security rules (nothing in the app).

## On the live site after a deploy

- `curl https://remixfox.co.uk/api/health` → `{"status":"ok","database":"ok",...}`.
- The landing page, `/legal.html`, `/privacy.html`, `/terms.html`, `/robots.txt`, `/sitemap.xml`, `/og-image.png` and `/apple-touch-icon.png` return 200; `/no-such-page` returns the 404 page.
- `curl -I https://remixfox.co.uk/` shows `content-security-policy`, `x-frame-options` and `strict-transport-security`.
- Render → Logs: migrations ran, `Telegram webhook set`, and no `AI: OpenAI refused` lines.
