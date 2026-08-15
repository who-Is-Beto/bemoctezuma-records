from django.db import migrations, models
from decimal import Decimal


def fix_amounts(apps, schema_editor):
    Order = apps.get_model('apiApp', 'Order')
    for order in Order.objects.all():
        if order.amount is None:
            continue
        # If amount looks like cents (e.g., >= 1000 mxn -> 100000 cents), divide by 100
        if order.amount >= Decimal('1000'):
            order.amount = (order.amount / Decimal('100')).quantize(Decimal('0.01'))
            order.save(update_fields=['amount'])


class Migration(migrations.Migration):

    dependencies = [
        ('apiApp', '0033_order_ship_link'),
    ]

    operations = [
        migrations.RunPython(fix_amounts, migrations.RunPython.noop),
    ]
