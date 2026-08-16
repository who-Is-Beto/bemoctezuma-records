# CONTEXT.md — Moctezuma Records (session handoff)

> **Read this before doing anything.** This file captures the current state of the
> project so a fresh agent session can pick up where the last one left off.
> Conventions live in `AGENTS.md` — this file is the *what we just did* snapshot.

Last updated: 2026-08-16

---

## 1. Repos & stack

| Repo | Path | Stack |
|---|---|---|
| Backend | `bemoctezuma_records` (this repo, workspace root) | Django 5.2.1, DRF 3.16, PostgreSQL (psycopg2), SimpleJWT, stripe, pytest |
| Frontend | `femoctezuma-records` (sibling of this repo) | React 19, Vite, TypeScript, react-router v7, Tailwind |

Note: the macOS home directory contains a **curly apostrophe** — always use the literal
path `~/Documents/Documents - Roberto’s MacBook Air/...` (curly `’`, not straight `'`).

## 2. Email verification ("2FA por email") — just implemented

Backend + frontend both implement email verification on signup:

- New `User.email_verified = BooleanField(default=False)` (`apiApp/models.py`).
- `settings.py`: `REQUIRE_EMAIL_VERIFICATION` env flag (default `False`), `email_verify`
  throttle scope `5/hour`, and `load_dotenv(".env.local", override=True)`.
- `apiApp/emails.py`: `send_verification_email(user, link, expiry_hours=24)` + template
  `apiApp/templates/emails/verify_email.html` (extends `emails/base.html`).
- Endpoints (`apiApp/urls.py`):
  - `POST /auth/register/` — sends welcome + verification email, returns `email_verified`.
  - `POST /auth/login/` — returns `403 { error: { code: "email_not_verified" } }` when the
    flag is on and the user hasn't verified.
- `POST /auth/verify-email/` — body `{ uid, token }`, validated with
  `default_token_generator`, idempotent; bad token → `400 { token: ["Invalid or expired verification link"] }`.
- `POST /auth/verify-email/resend/` — throttled `email_verify`, generic response.
- `GET /auth/me/` (NEW this session) — authenticated profile incl. `email_verified`;
  the frontend's `authService.getProfile()` already hit this path (was 404) and now
  uses it to re-sync the authoritative verification status on app load.
- Verification link: `{FRONTEND_URL}/verificar-correo?uid=…&token=…`.
- **Purchase gate** (`apiApp/views.py`): helper `_require_email_verified(request)` returns
  `403 email_not_verified` for unverified authenticated users on ALL cart endpoints
  (`/carts/`, `/carts/<code>/`, `/cart-items/`, `/cart/add/`, `/cart/update/`,
  `/cart/remove/`, `/cart/remove-all/`, `/cart/delete/`), checkout
  (`/create-checkout-session/`, `/checkout/complete/`) and `/orders/` — only when the flag is on.
- Migrations (COMMITTED in `079c3e9`; applied in prod via Railway — `release` phase now runs
  `migrate` thanks to the new Procfile, see section 9):
  - `0036_user_email_verified.py` — adds the field.
  - `0037_mark_existing_users_email_verified.py` — data migration marking existing users verified.

Frontend (sibling repo, committed in `f1653aa` on branch `2fa`):
- `src/app/domain/auth.ts`, `src/app/services/authService.ts`: `emailVerified` in session,
  `verifyEmail()`, `resendVerification()`.
- **Bug fixed this session:** `verifyEmail()` was returning the raw backend response
  (`email_verified`, snake_case) while `VerifyEmailPage` read `res.emailVerified`
  (camelCase) → always `undefined` → the page ALWAYS showed the error branch even when
  verification succeeded (the DB was actually updated). Now mapped to `emailVerified`.
- **Session persistence:** `AuthProvider` moved the session from `sessionStorage` to
  `localStorage` + `storage`-event listener (cross-tab sync — verifying in the email-opened
  tab now updates the original tab), and re-syncs `emailVerified` from `GET /auth/me/` on
  mount. Cart code (`moctezuma-cart-code`) also moved to `localStorage` and is cleared on
  logout so a different user never inherits the previous user's cart.
- **Gate fix:** `requiresVerification` now treats `emailVerified !== true` as unverified
  (was `=== false`), so legacy sessions with `null` correctly lock add-to-cart (in
  `Card.tsx`, `RecordDetailPage.tsx`, `CartPage.tsx`, `OrdersPage.tsx`).
- Verification badge lives ONLY on the profile page (`DashboardPage`); the Navbar stays
  clean by design (user's request).
- `src/pages/auth/VerifyEmailPage.tsx` (NEW): `/verificar-correo` landing; calls
  `markEmailVerified()` on success.
- Gate UX: `Card.tsx` + `RecordDetailPage.tsx` disable "Añadir al carrito" for
  `emailVerified !== true`, with hover tooltip (`title`) "Verifica tu correo para poder
  agregar al carrito…".
- `src/pages/cart/CartPage.tsx` (NEW, route `/carritos`): not-logged-in → login CTA;
  unverified → blocked view + resend; verified → functional cart (quantities, remove,
  empty, total, Stripe checkout via `createCheckoutSession(cartCode, "store")`).
- `src/pages/orders/OrdersPage.tsx` (NEW, route `/mis-ordenes`): gated orders list
  (also the checkout `success_url` destination).
- `src/pages/dashboard/DashboardPage.tsx`: verification badge + "Reenviar enlace de
  verificación" panel (resend anytime from profile).
- Restored from git history (`24f091a^`): `src/app/services/cartService.ts` (+ added
  `getCart()`), `src/components/Toast.tsx` (rebuilt with current design tokens).
- Routes rebuilt in `src/app/router/routes.tsx` (was empty during WIP refactor).

## 3. Local Postgres + prod data copy — just implemented

- Local Postgres 14 (Homebrew `postgresql@14`), service running on `127.0.0.1:5432`
  (start with `brew services start postgresql@14`).
- Role `moctezuma_dev` (LOGIN + CREATEDB + SUPERUSER — superuser only so
  `scripts/copy_prod_to_local.py` can bypass FK checks) and database
  `moctezuma_records_dev`, owned by `moctezuma_dev`.
- `.env.local` (untracked, gitignored) now points at the local DB:
  `DB=True`, `PG_HOST=127.0.0.1`, `PG_PORT=5432`, `PG_USER=moctezuma_dev`,
  `PG_DB=moctezuma_records_dev`, `DEBUG=True`, `REQUIRE_EMAIL_VERIFICATION=true`,
  `FRONTEND_URL=http://localhost:5173`. **Secrets live here and in `.env` — never print
  values, never commit.**
- `.env` (untracked) still points at prod Railway (`interchange.proxy.rlwy.net:30618`,
  DB `railway`) — **leave untouched; never migrate/write to prod from this machine.**
- Prod data was copied into the local dev DB (read-only from prod) by
  `scripts/copy_prod_to_local.py` (runnable from repo root; reads `.env` → writes into
  `.env.local` DB; truncates local data first): 431 records, 252 artists, 38 genres,
  11 categories, 11 users (all `email_verified=True`), 15 orders / 17 order items,
  1035 admin log entries, 23 sessions. Sequences were reset.
- Verified: order `amount` == Σ `quantity*price` (Decimal MXN), Django resolves to
  postgresql, 52 tests pass against the local DB.

## 4. Tests

```bash
python -m pytest apiApp/tests/ -q   # 52 passed
```

- `apiApp/tests/test_emails.py` — verification email tests (12) + login flag tests.
  `test_login_returns_email_verified_flag` explicitly sets `settings.REQUIRE_EMAIL_VERIFICATION = False`
  (the env default is now True because `.env.local` sets it).
- `apiApp/tests/test_verification_gate.py` (NEW) — 8 tests: add-to-cart blocked/allowed,
  carts blocked, orders blocked, checkout blocked *before* Stripe, flag-off allows,
  plus `GET /auth/me/` (returns `email_verified`, unauthenticated → 401).
- `conftest.py` (in `apiApp/tests/`) provides `user`, `order`, `api_client` fixtures.
- Frontend: `npx tsc -b --force` exits 0. Run from the frontend repo.
- `python -m pytest apiApp/tests/ -q` → **54 passed**.

## 5. Key decisions & gotchas

- "2FA" here = email verification (signed link), NOT numeric codes.
- The gate is off by default in code (`REQUIRE_EMAIL_VERIFICATION` defaults False) but
  `.env.local` enables it locally. Prod must apply migrations 0036/0037 before enabling
  the flag (0037 prevents locking out existing users).
- Registration returns tokens immediately (unverified user has a session); the purchase
  gate exists because of that.
- `load_dotenv(".env.local", override=True)` only overrides keys present in `.env.local` —
  add an explicit empty line to "unset" a prod key (that's why `.env.local` has `DB=` lines).
- Django tables are mixed-case (`apiApp_user`) — always quote them in raw SQL.
- `apiApp/serilizers.py` is intentionally misspelled; `Genere` model is intentional. Don't "fix".
- Money is `DecimalField` everywhere; Stripe unit amounts are `int(price * 100)` cents (mxn).
- Two `UserSerializer` classes exist in `serilizers.py` (double-spaced and single-spaced) —
  keep both in sync when changing user fields.
- The backend speaks snake_case (`email_verified`); the frontend auth domain uses camelCase
  (`emailVerified`). `authService` must map every auth endpoint response (login/register map
  via `mapTokens`; `verifyEmail` and `getProfile` were fixed this session — don't regress this).
- Session + cart code are in `localStorage` (shared across tabs) so verifying in the tab
  opened from the email link propagates to the tab where the user registered. JWT in
  localStorage is XSS-readable — accepted trade-off, same as sessionStorage.
- Permission rules (opencode.json): `psql*`/`dropdb*`/`rm*`/`git push*` are DENIED;
  `makemigrations`/`migrate`/`createsuperuser`/`git commit`/`pip install` require `ask`.
  Use full paths under `/opt/homebrew/opt/postgresql@14/bin/` for pg tools.
- Never modify `.env`; prod migrations happen via the deploy (Railway), not from here.

## 6. Open items / next steps

- Test the full flow live (backend `runserver 8008`, frontend `npm run dev`, register a
  fresh account → real Gmail SMTP email → verify → cart/checkout against Stripe test mode).
  The "No pudimos verificar tu correo" bug was a frontend camelCase mapping bug (fixed);
  if it still appears after deploying, check that the deployed backend actually has
  `/auth/verify-email/` + migrations 0036/0037 (deploy ordering: backend first).
- Apply migrations 0036/0037 to prod via normal deploy (NOT from this machine), then
  optionally set `REQUIRE_EMAIL_VERIFICATION=true` in prod. — **DONE / resolved: see §9.**
- Both repos are now committed: backend `emails` branch (`079c3e9` + Procfile `2f1ce33`),
  frontend `2fa` branch (`f1653aa`) — **neither has been pushed to GitHub yet.**
- Frontend deploy gotcha: `vite.config.ts` proxy was removed, so prod must set
  `VITE_API_URL` in Vercel pointing at the Railway backend (default is `http://localhost:8008`).
  The frontend README still mentions the vite proxy — stale doc.
- Future/out-of-scope (mentioned by user): "admin role can handle the database from an
  admin page" — not started.

## 7. Latest session (2026-08-15, round 2) — 6 storefront fixes

- **Stock bug fixed (backend + frontend):** `add_to_cart` validated stock AFTER
  `get_or_create`, so an over-stock/out-of-stock add left a phantom `CartItem` (quantity 0)
  in the DB even though the API returned `400 stock_insuficiente`. Now validates stock
  BEFORE creating/updating. Frontend: add button disabled when `record.stock <= 0`
  (shows "Agotado") in `Card.tsx` and `RecordDetailPage.tsx`. 3 regression tests added
  (out-of-stock no row, over-stock no row, second-add over-stock untouched).
- **Orders accordion (`OrdersPage`):** each order is now a collapsible card (chevron +
  CSS `grid-template-rows` transition animation). Expanded panel lists items with cover
  image, title, artist, unit price, quantity; shows the shipping address block when
  `shipped_to === "home"`. `OrderItemResponse.record` typed properly.
- **Seller notification email (NEW):** on payment webhook (`fulfill_checkout`),
  `send_order_notification_email(order)` emails `SELLER_NOTIFY_EMAILS`
  (setting in `settings.py`, env var comma-separated, defaults
  `moctezumarecords0@gmail.com,whoisbeto@gmail.com`) with order summary + customer email
  + items + address, `reply_to` = customer. Template `emails/order_notification.html`.
  3 tests added.
- **Order-confirmation email layout:** the Total/Envío row in `order_created.html` was
  `display:flex; justify-content:space-between` (unreliable in email clients); replaced
  with a two-cell `<table>` (Total left, Envío right-aligned) — guaranteed separation.
- **Stripe Checkout in Spanish:** `locale='es'` added to `stripe.checkout.Session.create`.
- **Share button (`RecordDetailPage`):** "Compartir" button uses `navigator.share` (Share
  API, with AbortError = cancel ignored); fallback to `navigator.clipboard.writeText` +
  success toast; error toast otherwise.
- Tests: `python -m pytest apiApp/tests/ -q` → **60 passed**. Frontend `npx tsc -b --force`
  → exit 0. `python manage.py check` → no issues.
- Note: pre-existing phantom `CartItem`s (quantity 0) created by the old bug may still be
  in the DB; new adds won't create them. No data cleanup migration written yet.
- No migrations were created this session (no model changes).

## 8. Pre-commit review fixes (2026-08-16) — applied after review

Pre-commit review fixes applied to the uncommitted `emails` branch (no new migrations):

- **N+1 fix:** `get_user_orders` now uses `.prefetch_related('order_items__record')`.
- **Case-insensitive resend:** `resend_verification_email` looks up with
  `email__iexact` (`.filter(...).first()` — avoids MultipleObjectsReturned 500).
- **Input validation guards:** `add_to_cart` and `update_cart_quantity` now return clean
  `400 quantity_invalid` / `400 record_id_required` / `404 product_not_found` /
  `404 cart_item_not_found` instead of unhandled `500`s on malformed input.
- **README typo:** `createdb --owner moctezuma` → `--owner moctezuma_dev`.
- Tests: 7 new (6 cart-validation + 1 case-insensitive resend) → **67 passed**.
- Non-issue discovered during review: `PASSWORD_RESET_TIMEOUT = 60*60*24` was already
  configured in committed code, so signed links already matched the "24 horas" copy —
  nothing to change there.

## 9. Commits, Procfile, prod incident (2026-08-16)

### Commits — ALL PUSHED & MERGED to main as of 2026-08-16
- Backend (`bemoctezuma_records`, branch `emails`):
  - `079c3e9` — email verification, seller notifications, cart input hardening
    (includes migrations `0036`/`0037`, templates, tests, docs, `scripts/`). → PR #22, merged.
  - `2f1ce33` — `Procfile` added. → PR #23, merged.
  - `9a6f229` — CONTEXT.md update.
  - `a299fd9` — **`.env.local` footgun fix** (see below).
  - `78215d2` — `EMAIL_TIMEOUT` fix (see §10) → pushed, merged, deployed. Register now returns 201.
  - `3664bfd` — CONTEXT.md update (was local-only, now pushed with 78215d2).
- Frontend (`femoctezuma-records`, branch `2fa`): `f1653aa` — email verification UX,
  cart & orders pages, share button, session persistence. → PR #15, merged.

### Procfile (new)
- `release: python manage.py migrate` → Railway runs migrations BEFORE the web process
  starts on every deploy. `web: gunicorn bemoctezuna_recordsAPI.wsgi:application
  --bind 0.0.0.0:${PORT:-8000}`.
- This fixes the root cause of the prod incident below: deploys previously had NO
  migrate step (no Procfile/Dockerfile in repo — Railway default buildpack only).

### Prod incident (act 2): `railway run` silently hit the LOCAL DB
- Root cause of the *persistent* register 500: `settings.py` called
  `load_dotenv(".env.local", override=True)` — `override=True` means local values BEAT
  the env vars `railway run` injects, so `railway run python manage.py migrate` (and
  `showmigrations`, `shell`) connected to `moctezuma_records_dev` @ `127.0.0.1`, NOT prod
  (`railway` @ `interchange.proxy.rlwy.net`). The `[X] 0036/0037` list seen was the LOCAL
  DB state. Prod still had no column → register (and login, `SELECT` fetches all columns)
  kept 500ing.
- **Fix (`a299fd9`):** `.env.local` is now only loaded when `RAILWAY_ENVIRONMENT` is
  unset (deployed services and `railway run` both set it) → CLI commands hit prod.
- **RESOLVED:** `railway run showmigrations` now hits the real prod DB
  (`interchange.proxy.rlwy.net`), and `[X] 0036/0037` are confirmed applied there.
  Existing customers are NOT locked out (0037 marked them verified; verified via
  `/auth/login/` returning 401 for bad creds instead of a 500 on the missing column).
- Lesson: never trust `railway run ...` output without confirming the DB host; the
  `.env.local` override was a silent footgun (now fixed in code).

### Security incident: LIVE prod secrets pasted into chat (2026-08-16)
- The user's `railway variables` output (pasted into the assistant session) exposed real
  working credentials: `STRIPE_SECRET_KEY` (sk_live), `PG_PASSWORD`, `EMAIL_HOST_PASSWORD`
  (Gmail app password), `DJANGO_SECRET_KEY` + `SECRET_KEY`, `WEBHOOK_SECRET`.
- **Recommended action: rotate all of the above** (Stripe key first — it can move money),
  update Railway vars + local `.env`. Rotating `SECRET_KEY` logs everyone out and
  invalidates outstanding verification/reset links. `STRIPE_PUBLISHABLE_KEY` is public by
  design — no action. Never paste `railway variables` output into a chat again.

### Deploy order (backend first)
1. `git push -u origin emails` (backend) → PR → merge to `main` → Railway deploy runs
   migrations via `release`.
2. Verify live: `POST /auth/verify-email/` and `GET /auth/me/` exist (not 404). (Confirmed
   already: they return 401/400 — new code IS live; only the migrate was missing.)
3. Check Railway env: `EMAIL_HOST`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/`EMAIL_USE_TLS`
   and `FRONTEND_URL` (else the welcome/verify emails silently fail and links point at
   localhost). — All present, plus `FRONTEND_URL=https://moctezumarecords.com/`.
4. `REQUIRE_EMAIL_VERIFICATION=true` — **already set in prod**; ensure 0037 runs (marks
   existing users verified) before trusting the flag.
5. `git push -u origin 2fa` (frontend) → PR → merge to `main` → Vercel auto-deploys.
6. **Must set `VITE_API_URL` in Vercel** pointing at the Railway backend — the dev proxy
   was removed from `vite.config.ts`, and the fallback default is `http://localhost:8008`.
7. Smoke test: register → real email → verify → cart → Stripe (test mode) → `/mis-ordenes`.

## 10. Register 500 root-caused + email switched to Resend (2026-08-16)

### The register 500 was NEVER migrations — it was a hung SMTP connect
- Migrations 0036/0037 are applied on prod (confirmed via `railway run showmigrations`
  hitting the real DB + `/auth/login/` returning 401, not 500).
- Real root cause (from `railway logs`): `WORKER TIMEOUT` on every `/auth/register/`.
  The container cannot reach `smtp.gmail.com:587` — the TCP `connect()` hung (no
  `EMAIL_TIMEOUT` set → indefinite block), the gunicorn worker died at its 30s timeout,
  and the client got a 500. The view's `try/except` can't catch a *hang*, only a raised
  exception.
- **Fix (`78215d2`, pushed/merged/deployed):** `EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '10'))`
  in `settings.py` + regression test `apiApp/tests/test_register_repro.py`
  (register returns 201 even when the email backend raises). Register now returns **201**
  and logs `Verification email failed for user N: [Errno 101] Network is unreachable`.
- **Definitive diagnosis:** `[Errno 101] Network is unreachable` on `sock.connect` →
  Railway blocks outbound SMTP (ports 25/465/587). SMTP from the container is dead;
  emails have likely never been deliverable from prod.

### Email delivery switched to Resend via django-anymail (UNCOMMITTED)
- SMTP is a dead end on Railway → HTTP email API on port 443 (same path as Stripe).
- `requirements.txt`: `+ django-anymail==15.1` (installed locally).
- `settings.py`: `EMAIL_BACKEND` auto-selects `anymail.backends.resend.EmailBackend`
  when `RESEND_API_KEY` is present, else console (local dev keeps working); added
  `ANYMAIL = {'RESEND_API_KEY': os.getenv('RESEND_API_KEY')}`. `emails.py` untouched —
  Anymail is a drop-in Django email backend.
- `EMAIL_TIMEOUT` still applies (Anymail honors it).
- Tests: **68 passed**, `manage.py check` clean.

### Railway deploy steps for the email switch (user action)
1. Create a Resend account; **verify the `moctezumarecords.com` domain** (DNS records).
2. Railway vars: add `RESEND_API_KEY`; **delete `EMAIL_BACKEND`** (it's currently set to
   the SMTP backend and would override the auto-selection) or set it explicitly to
   `anymail.backends.resend.EmailBackend`.
3. Change `DEFAULT_FROM_EMAIL` in Railway to an `@moctezumarecords.com` address (Resend
   will not send from the gmail address). The old SMTP vars (`EMAIL_HOST`,
   `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`) become inert — safe to remove.
4. Push/merge `emails` → Railway deploy → `railway ssh` egress to `smtp.gmail.com` is no
   longer required (nothing in the app talks SMTP anymore).
5. Smoke: register → check Resend dashboard for welcome + verify emails.

### Environment findings (non-secret)
- Railway has `DEBUG=False`, `FRONTEND_URL=https://moctezumarecords.com/`,
  `REQUIRE_EMAIL_VERIFICATION=true`, `DJANGO_SECRET_KEY` set (settings reads
  `DJANGO_SECRET_KEY`, so the inert `SECRET_KEY=django-insecure-…` var is dead weight —
  safe to delete). CORS is hardcoded in `settings.py` and already covers
  `https://moctezumarecords.com` + Vercel preview domains.
- `DB=True` is sloppy but harmless (`settings.py` treats any truthy string as "use Postgres").
- **SECURITY NOTE:** `railway variables` output leaked into the chat a SECOND time
  (same live secrets). Rotate: `STRIPE_SECRET_KEY` (sk_live, first), `PG_PASSWORD`,
  `EMAIL_HOST_PASSWORD`, `DJANGO_SECRET_KEY`, `WEBHOOK_SECRET`. Do NOT paste env values
  into chats; list names only, or use `railway run python -c "print(os.getenv(...))"` for
  specific non-secret keys.
- A probe user `smoketest_x1` (id 20) exists in prod from the smoke test — harmless, can
  be deleted via admin.
