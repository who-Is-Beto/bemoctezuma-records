# CONTEXT.md — Moctezuma Records (session handoff)

> **Read this before doing anything.** This file captures the current state of the
> project so a fresh agent session can pick up where the last one left off.
> Conventions live in `AGENTS.md` — this file is the *what we just did* snapshot.

Last updated: 2026-08-19

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
python -m pytest apiApp/tests/ -q   # 66 passed (2 pre-existing throttle flukes)
```

- `test_emails.py` — verification email tests.
- `test_verification_gate.py` — cart/checkout/orders gate tests.
- `test_password_reset.py` — password reset + throttle tests.
- `test_register_repro.py` — register returns 201 even when email backend fails.
- Frontend: `npx tsc --noEmit` → zero errors.

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

## 8. Historical sessions (compressed)

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
