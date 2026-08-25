# CONTEXT.md — Moctezuma Records (session handoff)

> **Read this before doing anything.** This file captures the current state of the
> project so a fresh agent session can pick up where the last one left off.
> Conventions live in `AGENTS.md` — this file is the *what we just did* snapshot.

Last updated: 2026-08-24

---

## 1. Repos & stack

| Repo | Path | Stack |
|---|---|---|
| Backend | `bemoctezuma_records` (this repo, workspace root) | Django 5.2.1, DRF 3.16, PostgreSQL (psycopg2), SimpleJWT, stripe, pytest |
| Frontend | `femoctezuma-records` (sibling of this repo) | React 19, Vite, TypeScript, react-router v7, Tailwind |

Note: the macOS home directory contains a **curly apostrophe** — always use the literal
path `~/Documents/Documents - Roberto's MacBook Air/...` (curly `'`, not straight `'`).

## 2. Local dev setup

- Local Postgres 14 (Homebrew), `brew services start postgresql@14`.
- Role `moctezuma_dev`, database `moctezuma_records_dev`.
- `.env.local` (untracked, gitignored) points at local DB. `.env` points at prod Railway.
- **Never modify `.env`; prod migrations happen via Railway deploys, not from this machine.**
- `.env.local` is only loaded when `RAILWAY_ENVIRONMENT` is unset (prevents `railway run`
  from silently hitting the local DB — was a real prod incident, see §7).
- Prod data copied locally via `scripts/copy_prod_to_local.py`.

## 3. Tests

```bash
python3 -m pytest apiApp/tests/ -q   # 167 passed (+2 pre-existing throttle flakes)
```

Note: the `ecommerceEnv` venv has NO pytest — run with system `python3 -m pytest`.

- `test_emails.py` — verification email tests.
- `test_verification_gate.py` — cart/checkout/orders gate tests.
- `test_password_reset.py` — password reset + throttle tests.
- `test_register_repro.py` — register returns 201 even when email backend fails.
- `test_slug_generation.py` — slug truncation/collision (incl. end-to-end POST).
- `test_shipping.py` — Envíos Perros quote/locations, admin orders, shipped email.
- `test_search.py` — tokenized multi-word search.
- `test_bazares.py` — full Bazar coverage: slugs, public list, admin CRUD,
  checkout gate, fulfillment, order serialization, emails (37 tests).
- Known flakes: `test_emails.py::test_resend_throttled`,
  `test_password_reset.py::test_request_throttled` fail due to test-cache
  isolation, not regressions.
- Frontend: `npm run build` (`tsc -b`) is the real check → zero errors.

## 4. Key conventions & gotchas

- `apiApp/serilizers.py` is intentionally misspelled; `Genere` model is intentional. Don't "fix".
- Money is `DecimalField` everywhere; Stripe unit amounts are `int(price * 100)` cents (mxn).
- FKs to the user model always go through `settings.AUTH_USER_MODEL`.
- All slugged models (`Category`, `Artist`, `Genere`, `Record`) auto-generate unique slugs
  in `save()` via `slugify(self.name)` + counter collision check against **their own table**.
  Only `Record.save()` also auto-calculates `sell_price` from `price` + `discount_porcentage`.
- The backend speaks snake_case; the frontend uses camelCase (`emailVerified`). Don't regress.
- Permission rules (opencode.json): `psql*`/`dropdb*`/`rm*`/`git push*` are DENIED;
  `makemigrations`/`migrate`/`createsuperuser`/`git commit`/`pip install` require `ask`.

## 5. Making the first admin in prod

After deploying, the first user needs the ADMIN role to access the admin panel:

```bash
# Option A: Railway shell (if you already have a superuser / Django admin account)
railway run python manage.py shell -c "
from apiApp.models import User
u = User.objects.get(email='your@email.com')
u.role = 'ADMIN'
u.save()
print(f'{u.username} ahora es ADMIN')
"

# Option B: Django admin panel (requires a Django superuser)
# 1. Create superuser:  railway run python manage.py createsuperuser
# 2. Go to https://your-app.up.railway.app/admin/
# 3. Find the user → change the "Role" field to ADMIN → Save
```

The frontend checks `useAuth().role === "ADMIN"` to gate the `/admin` page.

## 6. Admin panel & discount pricing system (2026-08-16 → 2026-08-18)

### Frontend
- **AdminPage.tsx**: tabbed container (Agregar disco / Discos / Usuarios), admin-only guard.
- **ManageRecordsTab.tsx**: search, paginated list, sell modal, Vender/Editar, PriceDisplay
  with discount badge. Responsive: table on `md+`, cards on smaller. Modal animated.
- **ManageUsersTab.tsx**: role dropdown, delete with confirmation, email-verified badges.
- **AddRecordPage.tsx**: add/edit mode, Discogs search, live price preview with discount.
- **Card.tsx**: coral `-XX%` badge top-right of cover image, stacked price display.
- **RecordDetailPage.tsx**: discount badge, strikethrough original, responsive mobile layout.
- **CartPage.tsx**: per-item discount display with strikethrough.

### Backend endpoints
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/artists/create/` | Admin | Create artist (returns existing if name matches) |
| `POST` | `/generes/create/` | Admin | Create genre (returns existing if name matches) |
| `GET` | `/auth/users/` | Admin | List all users |
| `PATCH` | `/auth/users/<id>/` | Admin | Update user role/status |
| `DELETE` | `/auth/users/<id>/delete/` | Admin | Delete user |
| `PATCH` | `/records/<id>/update/` | Admin | Update record (stock, price, discount) |

### Discount pricing model
- `Record.price` = original price (never changes).
- `Record.discount_porcentage` = % off (field name typo is intentional per AGENTS.md).
- `Record.sell_price` = auto-calculated in `Record.save()`: `Decimal(price) * (1 - discount/100)`.
- `Record.final_sale_price` = actual sale price at purchase time.
- All backend money paths (cart, Stripe checkout, fulfillment, emails) use `sell_price`.
- Frontend `getEffectivePrice(record)` derives prices from `price` + `discount_porcentage`
  directly, resilient to stale `sell_price`.

### Email templates
- Both `order_created.html` and `order_notification.html` show per-item `-X%` red badge
  and ~~original~~ strikethrough price. Context built in `services.py` `_order_email_context()`.

### Tailwind config additions
- Keyframes: `overlayIn` (fade), `modalIn` (scale-up spring), `toastIn`, `toastOut`.
- Animations: `animate-overlay-in`, `animate-modal-in`, `animate-toast-in`, `animate-toast-out`.

## 7. Slug save() bug fix (2026-08-19)

**Bug:** `Artist.save()` and `Genere.save()` had copy-pasted Record code referencing
fields they don't have (`self.discount_porcentage`, `self.price`, `self.sell_price`).
All three non-Record slugged models (`Category`, `Artist`, `Genere`) also used
`slugify(self.title)` instead of `slugify(self.name)` and checked collision against
`Record.objects` instead of their own table.

**Fix:** Each model's `save()` now only does slug generation from `self.name` with
collision check against its own model. Record's `save()` is the only one with the
`sell_price` calculation. Additionally, `Record.save()` now wraps `self.price` in
`Decimal(str(...))` to handle string inputs gracefully.

**Result:** `POST /artists/create/` now returns `201` correctly. Tests went from
56 passed + 10 errors → **66 passed**.

## 8. Slug overflow 500 on record create (2026-08-20)

**Bug:** `POST /records/create/` returned `{"error": {"code": "server_error"}}` 500 for
the title "La Nube En El Jardín En Vivo Desde Sala Nezahualcóyotl". Root cause:
all four slugged models declare `SlugField` without `max_length`, so the column is
`varchar(50)` (Django default). That title slugifies to **54 chars**; `slug` isn't in
the create serializer's fields so DRF never validates it, and Postgres raised
`DataError` → caught by the exception handler's catch-all as `server_error`.
Secondary bug: the collision check was an `if`, not a `while` — only survived one
duplicate slug (`-1`); a third insert raised IntegrityError → same 500.

**Fix:** all four `save()` methods now truncate the base slug to the field's
`max_length` (reserving room for the `-N` suffix) and loop collisions with `while`.
No migration needed (pure Python fix). Tests: `apiApp/tests/test_slug_generation.py`
(6 tests incl. an end-to-end POST of the failing payload shape) → suite now
**72 passed** (+2 pre-existing throttle flakes).

**Follow-ups (not done):**
- Optionally widen slug columns to `max_length=255` (needs migration + Railway migrate).
- `normalized_exception_handler` swallows unhandled exceptions without logging them,
  so prod 500s leave no server-side trace — add `logging.exception` in the
  `response is None` branch.

## 9. Envíos Perros shipping integration + search fix (2026-08-20)

**Shipping (Envíos Perros V3 API):**
- `POST /shipping/quote/` (auth + email gate): `{cart_code, zip}` → cheapest
  **Estafeta** quote (store policy) + full quote list. Package built server-side
  from cart: per-unit weights LP 300 g / 7" 100 g / CD 85 g (by category slug:
  contains 'cd' → 85, slug '7' → 100, else 300) × items_inside × qty + 300 g tare;
  Box 33×33 cm, height 2 cm/unit capped at 30.
- Checkout (`create-checkout-session`) **re-quotes server-side** for home delivery —
  never trusts browser amounts — and appends one Stripe line item "Envío (…)" so
  `Order.amount` (= Stripe amount_total/100) includes shipping automatically.
  ZIP normalized to zero-padded 5-digit string end-to-end ('4460'→'04460').
- `Order` new fields: `shipping_cost` (Decimal), `shipping_courier`,
  `shipping_service`; `ship_link` renamed → `shipping_link`
  (migration `0042_rename_ship_link_order_shipping_link_and_more.py`).
  Address JSON values coerced to strings on fulfillment.
- Config in settings.py: `ENVIOS_PERROS_TOKEN`, `ENVIOS_PERROS_API_URL`
  (.env points to **prod** https://app.enviosperros.com/api/v3 — see token note
  below), `ORIGIN_ZIP_CODE=15400`, `ENVIOS_PERROS_TIMEOUT=10`.
  **Token/environment reality (verified live 2026-08-20):** the account token
  works on PROD only — staging returns 401 "Unauthenticated." despite the docs
  tab claiming one key works for both. If staging testing is ever needed, a
  separate staging token is required. Rates calls are read-only; label
  generation will consume real balance.
  API docs blueprint: enviosperrosv3.docs.apiary.io/api-description-document.
- **Prod `/rates` response shape differs from the blueprint/staging**: prod
  returns `{summary, available, comment, details:{courier, service, total,
  currency, deliveryCommitment,...}|null}` with `available:false` +
  `details:null` for skipped couriers (e.g. DHL gated behind identity
  verification). `_normalize_quote()` maps both shapes to the canonical flat
  form and drops unavailable entries. Verified end-to-end against prod:
  CDMX route → Estafeta Económica $150 MXN selected.
- Upstream requires **minimum package weight of 1 kg** ("El campo peso del
  paquete debe ser de al menos 1") — light carts are clamped up in
  `build_package_from_cart` (`MIN_PACKAGE_WEIGHT_KG`).
- **dotenv trap (fixed in settings.py):** `runserver`'s autoreload child
  inherits the parent's env, and python-dotenv defaults to NOT overriding
  existing vars — so `.env` edits never reached reloaded servers (caused a
  confusing "still calling staging" 401 after the URL was fixed). Plain
  `load_dotenv()` now uses `override=not RAILWAY_ENVIRONMENT`: locally `.env`
  wins over inherited values; under `railway run` Railway-injected vars stay
  authoritative.
- `Record.weight_grams` (nullable): explicit value wins, else category default.
  Discogs release detail now returns `weight_grams_suggestion` (format-based,
  fallback to Discogs estimated_weight); AddRecordPage prefills it.

**Search fix:** 'zoe' now finds 'Zoé', 'trex' finds 'T. Rex'. `_slug_contains()`
annotates `Replace(slug, '-', '')` and icontains the slugified query — works on
Record/Artist/Genere/Category slugs, no schema change. Applied to `/search/` and
`/artists/search/`.

**Frontend:** CartPage debounced quote call when ZIP complete, shows Envío row +
updated total; AddRecordPage weight field; OrdersPage updated to `shipping_link`.

**Order fulfillment gap (fixed 2026-08-21):** orders are created by the Stripe
webhook OR by the redirect fallbacks (`GET /checkout/success/`,
`POST /checkout/complete/`). The frontend never called the fallbacks, so local
purchases (webhook can't reach 127.0.0.1) never became orders. OrdersPage now
calls `POST /checkout/complete/` when it sees `?session_id=` on return from
Stripe (idempotent — backend checks for an existing order by session id), then
strips the param and refetches. `cartService.completeCheckoutSession()` added.
Note: in prod both paths race safely; webhook remains primary.

**Quote refresh on quantity change (fixed 2026-08-21):** CartPage's quote effect
keyed on `items.length`, so same-line quantity edits (weight changes!) kept a
stale quote. Now keyed on a `cartSignature` (`recordIdxN` joined) that captures
which records × quantities; any add/remove/quantity change re-quotes after the
600ms debounce. Checkout still re-quotes server-side, so the charged amount is
always correct regardless of UI staleness.

**Colonia/ZIP handling (2026-08-21):** `/rates` prices by ZIP pair only — no
neighborhood param, so quotes don't need colonia data. But one ZIP can cover
several colonias (64000 → 'Monterrey Centro' + 'La Finca') and future label
generation (`POST /labels`) requires the EXACT Sepomex `neighborhood` name.
New `GET /shipping/locations/?zip=` endpoint (auth+verified) proxies Envíos
Perros `/locations`, cached 24h via Django cache. CartPage now shows a colonia
**dropdown** of official names once the ZIP is complete and pre-fills
city/state; falls back to free text when a ZIP has no Sepomex match.

**Admin orders tab (2026-08-21):** new `GET /orders/all/` + `PATCH
/orders/<id>/update/` (both `_require_admin`), status validated against
`Order.status_choices` ('canceled' — one L, keep in sync with frontend),
shipping_link ≤255 chars. Frontend: "Pedidos" tab in inventario
(`ManageOrdersTab.tsx`) — search, status filter, per-order status select +
tracking-link editor. The tracking-link editor renders **only** for orders
with `shipped_to === 'home'` AND `shipping_details` present (store/bazar
pickups have nothing to track).

⚠️ **views.py incident + cleanup done 2026-08-21:** while adding the admin
endpoints, discovered the file had COMMITTED duplicate function definitions
(complete_checkout_session ×2, checkout_success ×2, get_user_orders ×2 — last
def silently wins) and a corrupted spot where get_cart's header had been
overwritten by a stray duplicate header. All duplicates removed (verified
byte-identical before deletion); get_cart restored. Lesson: never trust
"it works at runtime" when duplicates exist — Python shadows them silently.
A botched scripted deletion during cleanup truncated the file tail; it was
reconstructed verbatim from git HEAD's canonical copies (which matched the
working copy's live versions, gate included). Suite green after recovery.

Tests: `apiApp/tests/test_shipping.py` (22 tests, incl. the captured prod
response shape). Suite: **94 passed** (+2 pre-existing throttle flakes).

**Follow-ups (not done):**
- Label generation (`POST /labels`) needs Sepomex-exact colonia via
  `GET /locations?zipCode=` — the frontend neighborhood may differ.
- `_resolve_cart_code_from_session` fallback subtracts metadata shipping_cost;
  sessions without metadata can't be matched when shipping is present.

## 10. Admin "Pedidos" tab + shipped email (2026-08-21)

Admin order management in Inventario → Pedidos (`ManageOrdersTab.tsx`):
search (email/#order/title/session), status filter, cards with items/address,
status select, tracking-link editor (only for `shipped_to=home` orders with
`shipping_details`). Backend: `GET /orders/all/` + `PATCH /orders/<id>/update/`
(`_require_admin`; status validated against `Order.status_choices` — note
"canceled" has one L; link ≤255 chars).

Mobile fixes: AdminPage tab bar is horizontally scrollable with full labels;
order cards stack on mobile with real truncation (`min-w-0` on grid children,
`break-words` on address).

Draft-save UX: typing a tracking link auto-selects status "shipped" (only from
pending/paid — never downgrades delivered/canceled); nothing saves until
"Guardar" (saves status + link together). Pickup/bazar orders get a
"Guardar estado" button. Select shows an orange ring while dirty.

Shipped email: first transition into `shipped` sends
`send_order_shipped_email(order)` (services.py) using
`templates/emails/order_shipped.html` — courier/service, clickable "Rastrear mi
paquete" button when the link is a URL (plain codes render as text), delivery
address, link to `/mis-ordenes`. Never raises; link-only edits on
already-shipped orders do NOT re-send. Tests: 3 new email-trigger tests +
admin-order tests → test_shipping.py at 35 tests; suite **107 passed**
(+2 pre-existing throttle flakes: `test_resend_throttled`,
`test_request_throttled`).

## 11. Permanent record delete + search overhaul (2026-08-21)

**Permanent delete** (`DELETE /records/<id>/delete/`, admin-only): migration
`0043_alter_orderitem_record` changed `OrderItem.record` to nullable SET_NULL —
deleting a sold record keeps order history (qty + snapshotted price; UI shows
"(disco eliminado)"). Gestionar Discos has a trash-icon button → confirm modal →
vanish animation (`recordVanish` keyframes in tailwind.config.js).

**Signal bug found by the delete tests**: `signals.py` post_delete on Review
re-created `RecordRatingSummary` while its Record was mid-cascade-delete → FK
violation at commit, i.e. deleting ANY reviewed record 500'd. Fixed with a
`_records_being_deleted` set toggled by pre/post_delete on Record.

**Search overhaul**: old code joined the whole query into ONE token
("pink floyd"→"pinkfloyd"), so multi-word or cross-field queries ("floyd pink",
"pink dark side") found nothing. Now `_query_tokens()` splits into normalized
tokens; every token must match any of record/artist/genere/category norm-slugs
(AND across tokens, OR across fields). `artist_search` tokenized the same way
(via Q objects — combining annotated querysets with `&` collides on duplicate
annotations). Tests: new `test_search.py` (8).

**Frontend fixes**: OrdersPage statusLabel had "cancelled"/"failed" keys that
don't exist in backend choices ("canceled", one L) → canceled orders showed raw
text. Tracking-link pill truncates + is clickable when URL (external-link icon,
stopPropagation so it doesn't toggle the accordion). HomePage filters redesigned:
glass filter bar with per-format icons (💿/📀/🔘/📦/🎁), scrollable chip row,
active gradient chip + removable category chip.

Suite: **120 passed** (+2 known throttle flakes). tsc clean.

## 12. Perf: self-hosted fonts + real TS build check (2026-08-21)

Lighthouse "render-blocking requests" fix: `src/index.css` had a CSS `@import`
of fonts.googleapis.com (CSS → font CSS → woff2 chain, ~800 ms). Now **self-
hosted**: `public/fonts/{krona-one-latin,work-sans-var-latin}.woff2` (latin,
60 KB total; Work Sans is a variable file covering 400–700), `@font-face` with
`font-display: swap` + unicode-range, and `<link rel="preload">` in index.html.
No third-party font connections remain.

Extras: Card got a `priority` prop (first catalog row loads covers eager +
fetchPriority=high instead of lazy — lazy above-fold images delay LCP); navbar
logo.png downscaled 1031×747/1.14 MB → 320×232/138 KB (renders at 48 px).
Original backed up at $TMPDIR/logo-original-backup.png.

Tooling lesson: `npx tsc --noEmit` uses the loose root tsconfig and misses
errors — **`npm run build` (`tsc -b`) is the real check**. It surfaced the TS
issues the user reported: missing `completeCheckoutSession` in CartRepository
type, `vanishingId` typed number vs string ids. Fixed.

## 13. Bazares (flea-market pickup) — full feature (2026-08-22 → 2026-08-24)

New "Bazar" concept: recurring flea-market events where the store sets up a
stand and customers can choose **recoger en bazar** at checkout.

### Backend
- `Bazar` model (`models.py`): `name`, `date` (DateField), `schedule`,
  `address`, `google_maps_url`, `image` (ImageField → `media/bazares/`),
  slug auto-generated like the other slugged models but **non-unique** on
  purpose (recurring events repeat names; ordering is `Meta.ordering=['date']`).
  Migrations: `0044_bazar.py`, `0045_order_pickup_bazar.py`.
- Endpoints (`apiApp/urls.py`, all under `/bazares/`):
  | Method | Path | Auth |
  |--------|------|------|
  | `GET` | `/bazares/` | public — upcoming only (`date >= today`), soonest first |
  | `GET` | `/bazares/all/` | admin — full list incl. past |
  | `POST` | `/bazares/create/` | admin — multipart (optional image) |
  | `PATCH` | `/bazares/<id>/update/` | admin — partial; past-date edits allowed for backfill |
  | `DELETE` | `/bazares/<id>/delete/` | admin |
- Checkout gate in `create-checkout-session`: when `shipped_to='bazar'`,
  requires a valid non-past bazar id → error codes
  `missing_bazar` / `invalid_bazar` / `bazar_in_past`. Bazar id travels to
  Stripe as checkout **metadata**; no shipping quote or line item is added.
- Fulfillment (webhook + `/checkout/complete/` + `/checkout/success/`)
  resolves `pickup_bazar` from metadata with tolerance for deleted/garbage
  ids. `Order.pickup_bazar` is `SET_NULL` on delete, so order history keeps.
- Order serializer nests `pickup_bazar`; order emails render a "Recoger en
  bazar" block (context dict built in `services.py::_order_email_context`,
  date as dd/mm/yyyy).

### Frontend (see frontend CONTEXT.md §2 for architecture)
Public `/bazares` page, checkout BazarPicker, admin tab Manejo de bazares,
orders show pickup info block.

### Tests
`apiApp/tests/test_bazares.py` — 37 tests: slug collision suffix + truncation,
public list filters, admin CRUD auth matrix (401/403), image upload with
tmp MEDIA_ROOT, SET_NULL order history, every checkout rejection code, today
allowed, metadata travel, fulfillment tolerance, nested serialization, email
context + rendered template blocks. Suite total **167 passed** (+2 known
throttle flakes).

### Hardening session (2026-08-24)
Frontend refactor for SOLID/clean architecture + SEO/a11y (details in the
frontend repo's CONTEXT.md): shared `domain/bazares.ts` types, repository
`bazarService.ts`, `lib/format.ts` formatters, `useSeo` hook (canonical/og/
twitter/robots, runtime origin — production domain intentionally NOT baked in),
accessible `Modal`/`ConfirmDialog` primitives, decomposed CartPage (~1118 →
~640 lines) into `CartItemRow` / `DeliveryOptions` / `BazarPicker` /
`ShippingAddressFields`, extracted `BazarCard`, `BazarFormModal`, `BazarRow`,
`PickupBazarInfo`. JSON-LD MusicStore added to index.html. Backend untouched
this pass; suite still 167 passed.

## 14. Historical sessions (compressed)

### Email verification (2026-08-15)
Backend + frontend implement email verification on signup (`User.email_verified`, signed
links, purchase gate via `_require_email_verified`). Migrations `0036`/`0037`. See README
for full details.

### Storefront fixes (2026-08-15, round 2)
Stock validation fixed (before `get_or_create`), orders accordion, seller notification
email, order-confirmation email layout fix, Stripe checkout in Spanish, share button.
60 tests passed.

### Pre-commit review fixes (2026-08-16)
N+1 fix (`prefetch_related`), case-insensitive resend, input validation guards, README
typo fix. 67 tests passed.

### Commits & prod incidents (2026-08-16)
All pushed and merged. Procfile added (Railway runs `migrate` on deploy). `.env.local`
footgun fixed (`RAILWAY_ENVIRONMENT` guard). Register 500 root-caused to hung SMTP
(`EMAIL_TIMEOUT` fix). Email switched to Resend via django-anymail.

### Security note
Live prod secrets were pasted into chat twice (2026-08-16). Rotate: `STRIPE_SECRET_KEY`,
`PG_PASSWORD`, `DJANGO_SECRET_KEY`, `WEBHOOK_SECRET`. Never paste `railway variables`
output into a chat.
