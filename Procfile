# Migrations run as Railway's pre-deploy step (railway.json), before the new
# version takes traffic: the app never runs against a schema that doesn't
# match its models. A failed migration stops the deploy; the old one keeps serving.

web: gunicorn bemoctezuna_recordsAPI.wsgi:application --bind 0.0.0.0:${PORT:-8000}
