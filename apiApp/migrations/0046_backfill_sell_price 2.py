# Generated data migration to backfill stale sell_price values.
# Records imported via raw SQL (copy_prod_to_local.py) or created before
# migration 0039 may have sell_price=0 despite having a valid price.
from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    Record = apps.get_model('apiApp', 'Record')
    # Recalculate sell_price = price * (1 - discount / 100) for every record.
    records = Record.objects.all()
    updated = 0
    for r in records:
        price = Decimal(str(r.price)) if r.price is not None else Decimal('0')
        discount = Decimal(str(r.discount_porcentage or 0))
        correct_sell = (price * (1 - discount / 100)).quantize(Decimal('0.01'))
        if r.sell_price != correct_sell:
            r.sell_price = correct_sell
            r.save(update_fields=['sell_price'])
            updated += 1
    print(f'  sell_price backfilled for {updated} / {records.count()} records')


def backwards(apps, schema_editor):
    """No-op: we can't undo this since we don't know the original values."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('apiApp', '0045_order_pickup_bazar'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
