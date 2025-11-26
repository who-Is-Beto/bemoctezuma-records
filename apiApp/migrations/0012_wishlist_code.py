import uuid

from django.db import migrations, models


def populate_wishlist_codes(apps, schema_editor):
    Wishlist = apps.get_model('apiApp', 'Wishlist')
    for wishlist in Wishlist.objects.filter(wishlist_code__isnull=True):
        wishlist.wishlist_code = uuid.uuid4()
        wishlist.save(update_fields=['wishlist_code'])


class Migration(migrations.Migration):

    dependencies = [
        ('apiApp', '0011_wishlist'),
    ]

    operations = [
        migrations.AddField(
            model_name='wishlist',
            name='wishlist_code',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_wishlist_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='wishlist',
            name='wishlist_code',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
