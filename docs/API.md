# API

Everything is under `/api`, JSON in and out. Interactive docs: https://remixfox.co.uk/api/docs. Unless marked **public**, a request needs the session cookie (401 otherwise). Another user's reel, competitor or job is a 404.

## Status codes the app relies on

| Code | Meaning |
|---|---|
| 401 | Not signed in (the app goes to the landing page) |
| 402 | A plan is needed: the trial is over, a plan limit is reached, or the feature is on a bigger plan (the app opens the Plans page) |
| 403 | Owner or admin only |
| 409 | Already exists / not allowed in this state (for example, the same plan) |
| 422 | Invalid input |
| 429 | Too soon: a competitor fetched in the last 24 hours, the day's fetches used, too many codes or wrong passwords |

## Signing in

| Method and path | What it does |
|---|---|
| `POST /auth/email/start {email}` **public** | E-mails a 6-digit code (204). |
| `POST /auth/email/verify {email, code}` **public** | Signs in (creates the account the first time); returns `Me`. |
| `POST /auth/password/login {email, password}` **public** | Signs in with a password; 401 "Wrong e-mail or password". |
| `POST /auth/telegram/start` **public** | Starts a Telegram sign-in (`?connect=true` connects Telegram to the signed-in account). |
| `POST /auth/telegram/finish {token}` **public** | 202 while waiting, 200 with `Me` once Start was pressed. |
| `GET /auth/telegram/link?token=` **public** | The one-time button sent by the bot. |
| `POST /telegram/webhook` **public** | Telegram's updates (secret header required). |
| `POST /auth/logout` | Ends the session. |

## You

| Method and path | What it does |
|---|---|
| `GET /me` | The account: e-mail, Telegram, `role`, `plan`, `allowance`, `usage`, `trial_ends_at`, `has_password`, `translate_to`, `languages`. |
| `PATCH /me {translate_to?, remix_profile?}` | The AI's language (sends your transcripts back to the AI); "Your voice" for remixes (up to 1,000 characters). |
| `POST /me/password {password}` | Sets or changes your password (needs an e-mail on the account). |
| `GET /me/avatar` | The Telegram profile photo. |

## Plans and payments

| Method and path | What it does |
|---|---|
| `GET /billing/plans` **public** | The plans in pounds, with limits. |
| `POST /billing/checkout {plan, interval, consent}` | Stripe Checkout `{url}` (a first subscription needs `consent: true`, the Plans page's box; 422 without it), or `{changed: true}` when switching an active plan. |
| `POST /billing/portal` | Stripe's billing portal `{url}`. |
| `POST /stripe/webhook` **public** | Stripe's events (signature checked). |

## Competitors, reels, plan, jobs

| Method and path | What it does |
|---|---|
| `GET /competitors` · `POST /competitors {handle}` | List; add and fetch (402 past the plan or trial, 429 past the day's fetches). |
| `DELETE /competitors/{id}` | Removes it with its reels. |
| `POST /competitors/{id}/fetch` · `POST /refresh` | Fetch one, or every competitor that is due. |
| `GET /reels?sort=latest\|views&competitor_id=…&limit=` | Your reels; `competitor_id` repeatable (up to 500). |
| `GET /reels/{id}` · `PATCH /reels/{id}` · `DELETE /reels/{id}` | One reel; save your hook/script/CTA and/or `plan_status`; delete. |
| `POST /reels/{id}/transcript` | Fetch its transcript (402 when the videos are used up). |
| `POST /reels/{id}/remix` | An AI remix in your voice: `{hook, script, cta, remixes_left}`; nothing is saved (409 nothing to remix yet, 402 remixes used up, 503 no AI key, 502 OpenAI failed). |
| `GET /reels/{id}/cover` | The stored cover image. |
| `POST\|DELETE /reels/{id}/plan` · `POST\|DELETE /reels/{id}/skip` | Content plan and skipped ideas. |
| `GET /plan` | The content plan. |
| `GET /jobs` · `GET /jobs/{id}` | Fetch jobs, and one with its steps and log. |
| `GET /export/reels.csv` · `GET /export/plan.md` | CSV (Pro, Studio) and Notion Markdown (Studio). |

## Owner and admins

| Method and path | Who | What it does |
|---|---|---|
| `GET /admin/stats` | owner, admins | Payments, revenue, MRR, users by plan, reels, Apify and OpenAI costs. |
| `GET /owner/overview` | owner, admins | All accounts and the latest 30 fetches. |
| `GET /ai` | owner | The AI's status. |
| `GET /admins` · `POST /admins {email, password?}` · `DELETE /admins/{email}` | owner | Up to 10 admins. |
| `PUT /admins/{email}/password {password}` | owner | A new password for an admin. |

Unknown `/api/...` addresses answer `404 {"detail": "Not Found"}`; unknown pages get the site's 404 page.

## Health

`GET /health` **public**: database and Apify status (Render's health check).
