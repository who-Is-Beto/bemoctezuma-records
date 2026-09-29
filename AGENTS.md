# AGENTS.md — Moctezuma Records Backend

You are a general-purpose coding assistant for **Moctezuma Records**, a vinyl-record e-commerce backend built with Django + Django REST Framework. You handle new features, bug fixes, and refactors across the codebase.

> **Session handoff:** read `CONTEXT.md` at the repo root before starting any task —
> it documents the recently implemented email-verification feature, the local Postgres
> setup with the prod data copy, and the current uncommitted state of both repos.

## Stack & Runtime

- Python 3.12, Django 5.2.1, Django REST Framework 3.16
- PostgreSQL (via `psycopg2-binary`), driven by `PG_USER`/`PG_PASSWORD`/`PG_HOST`/`PG_PORT`/`PG_DB` env vars
- Auth: `djangorestframework-simplejwt` (JWT), custom `User` model extending `AbstractUser`
- Payments: `stripe`
- CORS: `django-cors-headers`
- Static files: `whitenoise`
- Prod server: `gunicorn`
- Images: `Pillow`
- Env vars loaded via `python-dotenv`
- Email: `django-anymail` → Resend in prod (Railway blocks SMTP); SMTP or console locally
- Time zone: `TIME_ZONE = 'America/Mexico_City'` (the store's business day); storage stays UTC (`USE_TZ`). `tzdata` covers hosts without system zone data

## Project Layout

```
apiApp/                        # the single Django app — all business logic lives here
  models/                      # one module per domain: catalog (Category, Artist, Genere, Owner, Record),
                               #   orders (Order, OrderItem, Sale, SaleItem), cart, wishlist, reviews,
                               #   bazares, config (SiteConfig), user; __init__ re-exports every model
  views/                       # one module per domain (admin, auth, catalog, cart, checkout, orders,
                               #   sales, bazares, ...); __init__ re-exports them for urls.py
  serilizers/                  # NOTE: package name is misspelled ("serilizers") — keep it as-is, don't "fix" the typo
  services/                    # business logic that shouldn't live in views (emailing, checkout,
                               #   shipping, search, discogs, config, sales)
  admin_panel.py               # Administración tabs + custom-role permissions (single source of truth)
  emails.py                    # send_email(): renders templates/emails/*.html + plain-text part
  templates/emails/
  signals.py                   # wired up in apps.py -> ApiappConfig.ready()
  middleware.py / throttling.py / exception_handler.py / pagination.py / admin.py / urls.py
  management/commands/         # copy_prod_to_local
  migrations/
  tests/                       # pytest suite; shared fixtures in conftest.py
bemoctezuna_recordsAPI/        # project settings
  settings.py
  urls.py
  asgi.py / wsgi.py
manage.py
requirements.txt
```

There is currently **one app** (`apiApp`). Don't create new apps unless explicitly asked — add new functionality to `apiApp` as a domain module inside the `models/` / `views/` / `serilizers/` / `services/` packages (re-exported from each package's `__init__.py`), plus `signals.py` when needed.

## Domain Model Conventions

- Slugged models (`Category`, `Artist`, `Genere`, `Record`) auto-generate a unique `slug` in `save()` via `slugify()` + a counter-based collision check. Follow this exact pattern for any new sluggable model.
- `Record.condition` uses a `CONDITIONS` choices tuple (vinyl grading: Mint, Near Mint, etc.) — reuse this style (`SCREAMING_CASE` tuple of tuples) for any new choice fields.
- Money fields are `DecimalField(max_digits=10, decimal_places=2)`, never `FloatField`.
- FKs to the user model always go through `settings.AUTH_USER_MODEL`, never a direct `User` import.
- `related_name` is set explicitly on every FK — keep doing this for new relations.
- `Order`/`OrderItem` integrate with Stripe via `stripe_checkout_session_id`; be careful with money-unit bugs here (there's already a data-fix migration, `0034_fix_order_amounts.py`, for a cents/currency mismatch — don't reintroduce that class of bug).
- Private record fields (`cost_price`, `final_sale_price`, `owner`) only leave through `RecordAdminSerializer` (admin endpoints). Public serializers exclude them; `RecordListSerializer` is also nested in cart, wishlist and order payloads.
- "Today" and date filters are store days: use `timezone.localdate()` / `__date` lookups, never `date.today()` or UTC dates.

## Working Agreement

- **Explain before you act on anything stateful or destructive.** Before running `makemigrations`, `migrate`, any management command that writes to the DB, or `git` operations, tell me exactly what will change and wait for my go-ahead. Read-only commands (running the dev server, `check`, tests once they exist, linting) don't need pre-approval.
- Prefer the smallest correct change. Don't refactor unrelated code while fixing something else — call out follow-up cleanup separately instead of doing it inline.
- Match existing style exactly (including the `serilizers` and `Genere` naming — these are intentional/legacy, not typos to fix).
- When adding a model field, always generate the migration yourself (after approval) rather than leaving it for me to run manually, and mention the migration file name you created.

## Testing

pytest + pytest-django are set up (`pytest.ini` runs with `--nomigrations`; shared fixtures in `apiApp/tests/conftest.py`).

```bash
python3 -m pytest apiApp/tests/ -q
```

- CI (`.github/workflows/test.yml`) runs the suite on SQLite with no `.env` files, so tests must be hermetic: mock Stripe / shipping / Discogs HTTP, keep emails on the locmem backend (conftest does it) and mock `send_email` for failure paths.
- Put tests for logic worth testing in `apiApp/tests/test_<feature>.py`. Don't block a feature/fix on full coverage unless I ask — flag what's untested instead.
- The frontend has no test runner; `npm run build` (`tsc -b`) and `npm run lint` are its checks.

## Commands

```bash
python manage.py runserver 8008     # matches README convention (not the default 8000)
python manage.py makemigrations     # confirm with me first
python manage.py migrate            # confirm with me first
python manage.py createsuperuser
```

## Things to avoid

- Don't add new third-party packages without checking `requirements.txt` first and telling me what you're adding and why.
- Don't touch `media/` contents or commit binary assets.
- Don't hardcode secrets — use env vars consistent with the existing `python-dotenv` / `PG_*` / Stripe key pattern in `settings.py`.