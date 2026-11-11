"""Builders shared by the presale tests (price 800 · deposit 200 · discount 50)."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apiApp.models import PreSale, PreSaleOption, PreSaleOrder


def make_presale(**kw):
    data = dict(title='Kind of Blue', artist='Miles Davis', status='published',
                purchase_deadline=timezone.localdate() + timedelta(days=30))
    data.update(kw)
    return PreSale.objects.create(**data)


def make_option(presale=None, **kw):
    data = dict(name='Vinilo rojo', price=Decimal('800'), min_deposit=Decimal('200'),
                full_payment_discount=Decimal('50'), copies=10)
    data.update(kw)
    return PreSaleOption.objects.create(presale=presale or make_presale(), **data)


def make_order(option=None, **kw):
    data = dict(buyer_name='Ana', buyer_email='ana@example.com', item_price=Decimal('800'),
                reserved_at=timezone.now(), status='apartado', source='store')
    data.update(kw)
    return PreSaleOrder.objects.create(option=option or make_option(), **data)
