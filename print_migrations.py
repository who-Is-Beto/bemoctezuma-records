import psycopg2
import os

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("SELECT app, name FROM django_migrations ORDER BY app, name;")
rows = cur.fetchall()

for app, name in rows:
    print(f"{app}: {name}")
