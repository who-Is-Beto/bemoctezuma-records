"""Copy prod (Railway) data into the local dev database — READ-ONLY on prod.

Reads PG_* from .env (prod) and writes into the DB configured in .env.local.
Truncates all local data tables first. Requires a local superuser role
(or FK-safe insert order).

Usage (from repo root):
    python scripts/copy_prod_to_local.py
"""

import psycopg2
from psycopg2.extras import Json
from dotenv import dotenv_values

prod_env = dotenv_values('.env')
local_env = dotenv_values('.env.local')

def connect(env):
    return psycopg2.connect(
        dbname=env['PG_DB'], host=env['PG_HOST'], port=env['PG_PORT'],
        user=env['PG_USER'], password=env['PG_PASSWORD'],
    )

prod = connect(prod_env)
local = connect(local_env)
prod.autocommit = True
local.autocommit = True

pcur = prod.cursor()
lcur = local.cursor()

# Local-only: bypass FK checks during bulk copy (role is superuser locally)
lcur.execute('SET session_replication_role = replica')

# Wipe local data (dev DB is a pure copy target), keep schema + django_migrations
lcur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    AND table_name != 'django_migrations'
""")
local_tables = [r[0] for r in lcur.fetchall()]
lcur.execute('TRUNCATE ' + ', '.join('"%s"' % t for t in local_tables) + ' CASCADE')
print('[wipe] truncated %d local tables' % len(local_tables))

pcur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name
""")
tables = [r[0] for r in pcur.fetchall() if r[0] != 'django_migrations']

def columns(cur, table):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position
    """, (table,))
    return [r[0] for r in cur.fetchall()]

total = 0
print('%-40s %8s' % ('table', 'rows'))
print('-' * 50)
for table in tables:
    prod_cols = columns(pcur, table)
    local_cols = columns(lcur, table)
    common = [c for c in local_cols if c in prod_cols]
    if not common:
        print('%-40s %8s  (no common columns)' % (table, 0))
        continue

    extra_defaults = {c: True if c == 'email_verified' else None
                      for c in local_cols if c not in prod_cols}

    sel = ', '.join('"%s"' % c for c in prod_cols)
    pcur.execute('SELECT %s FROM "%s"' % (sel, table))
    rows = pcur.fetchall()

    if rows:
        local_rows = []
        for r in rows:
            d = dict(zip(prod_cols, r))
            local_rows.append(tuple(Json(d[c]) if isinstance(d.get(c), (dict, list)) else (d[c] if c in d else extra_defaults[c]) for c in local_cols))
        ins_cols = ', '.join('"%s"' % c for c in local_cols)
        ph = ', '.join(['%s'] * len(local_cols))
        lcur.executemany('INSERT INTO "%s" (%s) VALUES (%s)' % (table, ins_cols, ph), local_rows)
        total += len(rows)

        seq = None
        if 'id' in local_cols:
            lcur.execute("SELECT pg_get_serial_sequence('public.\"%s\"', 'id')" % table)
            seq = lcur.fetchone()
        if seq and seq[0]:
            lcur.execute('SELECT COALESCE(MAX(id), 1) FROM "%s"' % table)
            max_id = lcur.fetchone()[0]
            lcur.execute('SELECT setval(%s, %s)', (seq[0], max_id))
    print('%-40s %8s' % (table, len(rows)))

lcur.execute('SET session_replication_role = origin')
print('-' * 50)
print('TOTAL rows copied: %d' % total)
prod.close(); local.close()
