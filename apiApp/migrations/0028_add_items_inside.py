from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("apiApp", "0024_release_date_integer"),
    ]

    operations = [
        migrations.AddField(
            model_name='record',
            name='items_inside',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
