# Railway runs `release` before starting `web`, so migrations always land
# before the app accepts traffic. This prevents the app from ever running
# against a schema that doesn't match the models (e.g. the email_verified
# column missing on register).
release: python manage.py migrate

web: gunicorn bemoctezuna_recordsAPI.wsgi:application --bind 0.0.0.0:${PORT:-8000}
