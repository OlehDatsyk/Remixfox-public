# Architecture

Remixfox is one Docker container on Render: a FastAPI app that serves the API, the static front end and two kinds of background work (fetch jobs and the AI worker), with PostgreSQL for data and file storage.

```
Browser (public/: plain HTML, CSS, JS)
   │  HTTP/JSON (/api), session cookie
   ▼
FastAPI (app/main.py, one uvicorn process)
   ├── auth, email_login, passwords, telegram   sign-in, sessions, roles
   ├── billing                                  Stripe Checkout, portal, webhook, payments
   ├── api                                      competitors, reels, plan, jobs, exports, admin
   ├── jobs  (thread pool, 3 at once)           Apify fetch → save reels and covers → transcripts
   └── ai    (3 threads)                        OpenAI: language, hook/script/CTA, caption
   ▼
PostgreSQL (users, sessions, competitors, reels, fetch_jobs, job_events,
            stored_files, usage_events, admins, login_codes, telegram_logins, avatars)

Outside: Apify (Instagram reels, transcripts) · OpenAI (GPT-5.6 Luna) · Stripe ·
         Resend (sign-in e-mails) · Telegram Bot API
```

## Modules (`app/`)

| Module | What it does |
|---|---|
| `main.py` | The FastAPI app: routers, the `/` page choice (app or landing by cookie), no-cache headers, HSTS, the redirect from onrender.com to `PUBLIC_URL`, start-up (migrations run in `start.sh`, then the AI worker and the Telegram webhook). |
| `config.py` | Settings from environment variables (all trimmed). |
| `db.py` | The SQLAlchemy engine (pool 10 + 10) and sessions. |
| `models.py` | Tables (SQLModel) and the API's JSON shapes. |
| `auth.py` | Sessions, `CurrentUser`/`OwnerUser`/`StaffUser`, roles, `allowance()` (owner, admin, plan or trial) and `/api/me`. |
| `email_login.py` | 6-digit codes by e-mail (Resend), with rate limits. |
| `passwords.py` | Optional passwords (scrypt), sign-in with a password, rate limits. |
| `telegram.py` | Sign-in and connecting through @remixfoxbot. |
| `plans.py` | Plans, prices and limits; the free trial; usage counting from `usage_events`. |
| `billing.py` | Stripe over httpx: plans, Checkout, switching, the portal, the webhook, payment figures. |
| `api.py` | Everything else under `/api` (see `docs/API.md`). |
| `jobs.py` | The fetch pipeline and its step log (`job_events`). |
| `apify.py` | The Apify client and its price list. |
| `storage.py` | Covers from Instagram's CDN, kept in Postgres. |
| `transcripts.py` | One transcript on request. |
| `translations.py` | The AI queue, languages, storing results. |
| `ai.py` | The OpenAI worker (prompt, JSON schema, checks, tokens and cost), and the remix in the user's voice. |
| `handles.py` | Parsing Instagram usernames and links. |

## The fetch pipeline

`POST /api/competitors` (or fetch, or refresh) → `jobs.queue_fetch` (a `fetch` usage row) → background `run_fetch_job`:

1. **database** and **queue** steps (up to three jobs at once);
2. **apify**: the latest 20 reels (pinned ones skipped); a `reels` usage row with their count and cost; on the free trial only the 5 most viewed are kept;
3. **save**: new reels inserted, known ones updated, covers downloaded once;
4. **transcripts**: for reels without one, most viewed first, as many as the month's (or trial's) videos allow; a `video` usage row each;
5. **ai**: the AI worker is woken.

Every step writes `job_events`; the activity panel polls `GET /api/jobs/{id}`.

## The AI

Three worker threads claim batches of 10 (`FOR UPDATE SKIP LOCKED`), send one Responses API request per batch with a strict JSON schema, check each result, and store hook, main script, CTA, the translated caption, the detected language and `translated_into`. Tokens and cost are split over the reels' owners as `ai` usage rows. A failure (bad key, quota) hands the batch back and waits 5 minutes. Details in `app/ai.py`.

## Front end (`public/`)

- `index.html` + `app.js` + `styles.css`: the app (Home swipe deck, Reels, Content plan, Competitors, Plans, Admin panel, Settings), the editor, the activity panel and the guide. No framework or build step; the desktop layout is the 1549 × 1015 design scaled to the window; tablets and phones have their own layouts.
- `welcome.html` + `welcome.js` + `welcome.css`: the landing page and sign-in.
- `legal.html`, `privacy.html`, `terms.html`: the legal pages.

## Deploys

Local → GitHub (`main`) → GitHub Actions runs the tests against PostgreSQL → Render deploy hook → `start.sh` runs `alembic upgrade head` and starts uvicorn.
