import os
import psycopg2

user = os.environ["PG_USER"]
password = os.environ["PG_PASSWORD"]
host = os.environ["PG_HOST"]
port = os.environ["PG_PORT"]
db = os.environ["PG_DB"]

url = f"postgresql://{user}:{password}@{host}:{port}/{db}"

print("Connecting to:", url)

conn = psycopg2.connect(url)
cur = conn.cursor()

cur.execute("SELECT app, name FROM django_migrations ORDER BY app, name;")
rows = cur.fetchall()

print("\n--- MIGRATIONS IN DATABASE ---")
for app, name in rows:
    print(f"{app} → {name}")

cur.close()
conn.close()
