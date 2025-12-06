from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app = 'apiApp';")
    rows = cursor.fetchall()
    for row in rows:
        print(row[0])
