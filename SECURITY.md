# Security

How Remixfox keeps accounts, secrets and payments safe, and what to do if something goes wrong.

## Reporting a problem

Write to oleh@remixfox.co.uk. Please do not test against other people's accounts or try to overload the service.

## Secrets

- Secrets live only in Render → reels-remix → Environment (`SECRET_KEY`, `APIFY_TOKEN`, `OPENAI_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `RESEND_API_KEY`, `TELEGRAM_BOT_TOKEN`) and, for local runs, in `.env` (git-ignored). Never in the repository, commit messages or chat.
- A saved variable reaches the app only after a deploy ("Save, rebuild, and deploy").
- The HTTP library's request log is switched off (`httpx` at WARNING) because the Telegram Bot API puts the bot token in the URL. Error messages name only the kind of failure, and OpenAI's messages have any key fragment masked.
- `.env.example` holds placeholders only; `.env` and `.env.*` are git-ignored (except the example). On 10 November 2026 the full history of the private repository was scanned for keys, tokens and private keys: none, only the local development database address. `pip-audit` found no known vulnerabilities in the pinned packages.
- If a secret leaks: create a new one at the provider, put it in the host's environment, redeploy, and revoke the old one.

## Signing in

- **E-mail codes** (`app/email_login.py`): 6 digits, valid 10 minutes, only the newest code counts and only once, 5 wrong tries lock it; at most one new code a minute, 5 an hour per address, 20 an hour per IP. Stored as HMAC-SHA256 keyed with `SECRET_KEY`.
- **Telegram** (`app/telegram.py`): the webhook needs a secret header derived from `SECRET_KEY`; a website sign-in needs both the one-time token and a 10-minute browser cookie, so a forwarded bot link signs nobody else in. At most 20 sign-ins can be started from one IP an hour (each stores a row); expired ones are deleted after a day.
- **Passwords** (`app/passwords.py`): optional, at least 8 characters, stored as scrypt hashes (n 16384, r 8, p 1, a random 16-byte salt). A password can be set only by someone already signed in (so the e-mail is proven first) or by the owner for an admin. Wrong passwords are limited to 10 per e-mail and 30 per IP in 15 minutes (counted in memory). A wrong e-mail and a wrong password get the same answer, and take the same time.
- **Sessions**: a random 32-byte token in an httponly, SameSite=Lax cookie (`remixfox_session`, 30 days); only its SHA-256 hash is stored. Expired sessions and sign-in codes are deleted as new ones are made. Cross-site requests do not carry the cookie for anything but plain links (SameSite=Lax), the API takes JSON only, and there is no CORS opening.
- HTTPS only: Render redirects HTTP, and responses carry `Strict-Transport-Security: max-age=31536000`.

## In the browser

Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` (never shown inside another site), `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (no camera, microphone or location) and a Content Security Policy: scripts, styles, images, fonts and requests only from this site, no plugins, no framing (`/api/docs` is left out because Swagger UI loads from a CDN). Text from Instagram and from users is inserted with `textContent` or escaped. The signed-in app is `noindex`; only the landing and legal pages are in `sitemap.xml`.

## Who can do what

- Every user sees only their own competitors, reels, plans and jobs; anyone else's is a 404.
- The owner is the account with `OWNER_EMAIL` (decided on every request). Admins are the e-mails the owner adds (up to 10): they see the Admin panel and anyone's fetches, but not the AI card, the admin list or anyone's billing.
- Paid features check the plan or trial on the server, not in the browser.

## Payments

Stripe hosts the payment page and the billing portal; card details never reach Remixfox. The webhook (`/api/stripe/webhook`) checks Stripe's signature by hand (HMAC-SHA256, 5-minute tolerance) before changing anything. A first subscription needs the customer's tick on the Plans page (the Terms and the request to start at once); its time and the Terms' version are kept in Stripe's metadata.

## Personal data

What is kept, why and for how long is in `public/privacy.html`. Covers and reels are public Instagram data. Tests use `example.com` addresses only.
