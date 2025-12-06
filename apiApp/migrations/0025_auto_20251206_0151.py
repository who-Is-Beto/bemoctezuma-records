from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('apiApp', '0024_auto_20251206_0146'),
    ]

    operations = [
        # 1. Cambiar el tipo del campo a PositiveIntegerField
        migrations.AlterField(
            model_name='record',
            name='release_date',
            field=models.PositiveIntegerField(blank=True, null=True, default=2025),
        ),

        # 2. Convertir la fecha almacenada a solo el año
        migrations.RunSQL(
            sql=r"""
                UPDATE "apiApp_record"
                SET release_date = EXTRACT(YEAR FROM release_date)
                WHERE release_date IS NOT NULL;
            """,
            reverse_sql=r"""
                UPDATE "apiApp_record"
                SET release_date = NULL;
            """
        ),
    ]
