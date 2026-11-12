# Changelog

What changed in Remixfox, newest first. Every change goes local → GitHub → Render.

## 11 November 2026 (final audit)

- **Payments ready for real customers**: before a first subscription the Plans page asks the customer to agree to the Terms and to start the plan straight away (UK consumer law's 14-day rule); the server refuses Checkout without it, and Stripe keeps the time and the Terms' version. Stripe now asks for a billing address (VAT records), and its page repeats the agreement above the pay button. The Terms say so (updated 11 November 2026).
- **Security**: browser security headers and a Content Security Policy; sign-ins started through Telegram are limited per network; expired codes, Telegram sign-ins and sessions are cleaned up; the git history and dependencies were checked (nothing found).
- **Search engines**: a clearer title, structured data (with the real price range), canonical links on every public page, a home-screen icon, the app marked `noindex`, and a proper 404 page (API addresses still answer in JSON).
- The landing page's demo no longer shows an invented "+12 leads this week"; it says "Script ready to film".
- **GitHub**: a new README with real screenshots, `BACKUP.md`, `LICENSE`, `.env.example` and a social preview image.

## 10 November 2026

- **Content plan drag and drop fixed**: a dragged card no longer slides under the next column's cards, and it can no longer leave the board or be cut off at the panel's edge; with the pointer outside the board it drops into the nearest column. A dropped card stays on top while it glides into place.

## 9 November 2026 (late night)

- **"Good to know" on the landing page**: eight honest answers (where the numbers come from, how often data is updated, which accounts, what happens when Instagram data cannot be fetched, which AI, copying, the trial's end, cancelling), linked as "FAQ" in the header.
- **Link previews and search engines**: Open Graph tags and a preview image, so a shared link shows a card; `robots.txt` and `sitemap.xml`.
- **The contact e-mail is now oleh@remixfox.co.uk** on the Legal notice, Privacy policy (with Cookies), Terms of use and the landing page.
- **Ukrainian comes right after British English** in the language list.
- **A full layout audit** at nine screen sizes in both themes (see `docs/TESTING.md`): the swipe ✓ / ✕ buttons now stay above the tab bar on short phones and short tablet windows, phone tap targets are bigger, and the "−20%" badge and the "Continue with Telegram" button are easier to read.

## 9 November 2026 (night)

- **AI remix in your voice**: describe yourself once in Settings → Your voice (niche, audience, tone, offer, call to action), then press **Remix in my voice** in the reel editor. The AI keeps what made the reel work and rewrites it for you, in your language, without copying the original's sentences or inventing facts about you. Nothing is saved until you press Save. Remixes per month: Start 100, Pro 400, Studio 1,500; 10 in the free trial.

## 9 November 2026 (late evening)

- **Smooth scrolling on the landing page**: "How it works", "Pricing", "Sign in" and the hero buttons glide to their section (an eased 0.7–1.2 s scroll that lands under the sticky header); the sign-in card is centred and glows once.

## 9 November 2026 (evening)

- **Sign in with a password**, next to the e-mail code and Telegram. A new account can add a password straight after its first code; anyone can set or change one in Settings. The owner gives each admin a password when adding them (with a Generate button), and can set a new one at any time. Only scrypt hashes are stored; wrong passwords are limited (10 per e-mail, 30 per IP, per 15 minutes).
- **15-day free trial** from the first sign-in: two competitors, the 5 most viewed reels of each, with transcripts and scripts (10 videos, 4 fetches at most). After 15 days fetching needs a plan; everything already fetched stays.
- **Monthly reel limits** on paid plans: Start 900, Pro 2,400, Studio 6,000 reels fetched a month (about 20 a fetch), shown in the plan cards and the usage bars.
- **Plans page in the app** ("Plans" pill beside the logo, or in the top bar on phones): the three plans, Monthly / Yearly, your usage and Manage billing. Settings keeps a short summary.
- **Legal**: the Legal notice page (`legal.html`) is new; the Privacy policy and Terms of use are filled in with the owner's details, data retention, cancelling and refunds, liability and governing law; every page of the app has a footer with Legal notice, Privacy policy, Terms of use and Cookies.
- **Captions are translated** into the user's language too (also for reels without speech); the editor shows the translation with the original a click away, and titles use it.
- **Polish removed** from the AI languages (with Russian and Belarusian, never offered).
- **Content plan drag and drop**: the card lifts, tilts and follows the pointer; a slot opens at the top of the column it will land in; the page scrolls near the edges; the card glides into place and settles. On touch screens, press and hold to drag; on a keyboard, Shift + ← / →. Fixed: the card used to stay frozen while dragged.
- Swiping right says "Added to your content plan, under Not started".
- `BACKLOG.md`, `CHANGELOG.md`, `SECURITY.md` and `docs/` added.

## 9 November 2026 (afternoon)

- Free trial (first version), the AI's language per account, costs of every fetch, transcript and AI batch in `usage_events`, and the **Admin panel** (payments, revenue, MRR, users by plan, reels, costs).
- Onboarding guide for new accounts (a dimmed page with the next step lit).
- Reels filter by account; the Scripts language setting; the activity panel closes itself after a successful fetch.
- Fixes: phone title blocks were 220px tall and hid the swipe buttons; "Taken ❌" toast; the AI rejected honest Ukrainian translations; deleting a competitor left its reel in the preview.
- The AI logs each batch it finishes, and says why OpenAI refused a key.

## 9 November 2026 (midday)

- **Start, Pro and Studio plans**, monthly or yearly at 20% off, in pounds, with Stripe Checkout, switching and the billing portal; limits on competitors, videos a month and daily fetches; CSV and Notion exports.
- **Admins**: up to 10 e-mails the owner adds; free use with Studio limits.
- **The AI moved from Codex CLI to the OpenAI API** (GPT-5.6 Luna, high reasoning).
- Landing page: three plans, a Legal block, "Made with ♥ by Oleh Datsyk". The logo opens Home.

## 9 November 2026 (morning)

- Accounts and Stripe subscriptions; the owner from `OWNER_EMAIL` with an overview.
- remixfox.co.uk as the custom domain, HTTPS only (HSTS), pages on onrender.com redirect there.
- Passwordless sign-in: e-mail codes (Resend) and the Telegram bot (@remixfoxbot).
- Phone and tablet layouts; the landing page (typed hero, live demo, how it works, pricing); Privacy policy and Terms of use.

## 8 November 2026

- The FastAPI back end with SQLModel, Alembic and PostgreSQL; tests before every deploy (GitHub Actions → Render deploy hook).
- Apify: the latest 20 reels of a competitor, covers stored in Postgres, transcripts on request.
- Codex CLI split transcripts into hook, script and call to action (British English); a live step tracker for fetches.
- The content plan (Kanban), the swipe Home page, ranking by views.

## 7 November 2026

- The design reference, the first UI, the name Remixfox and its logo, British English throughout.
