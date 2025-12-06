from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('apiApp', '0023_prepare_release_date_text'),
    ]

    operations = [
        migrations.AlterField(
            model_name='record',
            name='release_date',
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
    ]
