from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('apiApp', '0022_remove_category_image'),
    ]

    operations = [

        # 1️⃣ cambiar a text temporalmente
        migrations.RunSQL(
            sql="""
                ALTER TABLE "apiApp_record"
                ALTER COLUMN release_date TYPE text;
            """,
            reverse_sql="""
                ALTER TABLE "apiApp_record"
                ALTER COLUMN release_date TYPE date;
            """
        ),

        # 2️⃣ limpiar valores (sacar solo el año)
        migrations.RunSQL(
            sql="""
                UPDATE "apiApp_record"
                SET release_date = SUBSTRING(release_date FROM 1 FOR 4)
                WHERE release_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$';
            """,
            reverse_sql="""
                UPDATE "apiApp_record"
                SET release_date = NULL;
            """
        ),
    ]
