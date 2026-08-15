# AGENTS.md — Moctezuma Records Backend

You are a general-purpose coding assistant for **Moctezuma Records**, a vinyl-record e-commerce backend built with Django + Django REST Framework. You handle new features, bug fixes, and refactors across the codebase.

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

## Project Layout

```
apiApp/                        # the single Django app — all business logic lives here
  models.py                    # User, Category, Artist, Genere, Record, Cart, CartItem,
                                # Wishlist, WishlistItem, Review, RecordRatingSummary, Order, OrderItem
  views.py
  serilizers.py                # NOTE: filename is misspelled ("serilizers") — keep it as-is, don't "fix" the typo
  services.py                  # business logic that shouldn't live in views
  signals.py                   # wired up in apps.py -> ApiappConfig.ready()
  pagination.py
  admin.py
  urls.py
  migrations/
bemoctezuna_recordsAPI/        # project settings
  settings.py
  urls.py
  asgi.py / wsgi.py
manage.py
requirements.txt
```

There is currently **one app** (`apiApp`). Don't create new apps unless explicitly asked — add new functionality to `apiApp` following its existing module split (models / views / serilizers / services / signals).

## Domain Model Conventions

- Slugged models (`Category`, `Artist`, `Genere`, `Record`) auto-generate a unique `slug` in `save()` via `slugify()` + a counter-based collision check. Follow this exact pattern for any new sluggable model.
- `Record.condition` uses a `CONDITIONS` choices tuple (vinyl grading: Mint, Near Mint, etc.) — reuse this style (`SCREAMING_CASE` tuple of tuples) for any new choice fields.
- Money fields are `DecimalField(max_digits=10, decimal_places=2)`, never `FloatField`.
- FKs to the user model always go through `settings.AUTH_USER_MODEL`, never a direct `User` import.
- `related_name` is set explicitly on every FK — keep doing this for new relations.
- `Order`/`OrderItem` integrate with Stripe via `stripe_checkout_session_id`; be careful with money-unit bugs here (there's already a data-fix migration, `0034_fix_order_amounts.py`, for a cents/currency mismatch — don't reintroduce that class of bug).

## Working Agreement

- **Explain before you act on anything stateful or destructive.** Before running `makemigrations`, `migrate`, any management command that writes to the DB, or `git` operations, tell me exactly what will change and wait for my go-ahead. Read-only commands (running the dev server, `check`, tests once they exist, linting) don't need pre-approval.
- Prefer the smallest correct change. Don't refactor unrelated code while fixing something else — call out follow-up cleanup separately instead of doing it inline.
- Match existing style exactly (including the `serilizers.py` and `Genere` naming — these are intentional/legacy, not typos to fix).
- When adding a model field, always generate the migration yourself (after approval) rather than leaving it for me to run manually, and mention the migration file name you created.

## Testing

There is no test setup yet (`apiApp/tests.py` is a stub, no pytest in `requirements.txt`). When a task touches logic worth testing:
1. Propose adding `pytest-django` + a minimal `pytest.ini`/`conftest.py` first, rather than silently assuming Django's `TestCase`.
2. Don't block a feature/fix on writing full test coverage unless I ask — flag what's untested instead.

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