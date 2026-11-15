"""Builders shared by the presale tests (price 800 · deposit 200 · discount 50)."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apiApp.models import PreSale, PreSaleOption, PreSaleOrder, PreSaleOrderItem


def make_presale(**kw):
    data = dict(title='Kind of Blue', artist='Miles Davis', status='published',
                purchase_deadline=timezone.localdate() + timedelta(days=30))
    data.update(kw)
    return PreSale.objects.create(**data)


def make_option(presale=None, **kw):
    data = dict(name='Vinilo rojo', price=Decimal('800'), min_deposit=Decimal('200'),
                full_payment_discount=Decimal('50'), copies=10)
    data.update(kw)
    if data.get('arrived_at'):  # an arrived version has every copy in (as migration 0060 backfills)
        data.setdefault('arrived_copies', data['copies'])
    return PreSaleOption.objects.create(presale=presale or make_presale(), **data)


def make_order(option=None, quantity=1, lines=None, **kw):
    """An order of ``lines`` [(option, qty)], or of ``quantity`` copies of ``option``."""
    lines = lines or [(option or make_option(), quantity)]
    data = dict(buyer_name='Ana', buyer_email='ana@example.com',
                item_price=sum((o.price * q for o, q in lines), Decimal('0')),
                reserved_at=timezone.now(), status='apartado', source='store')
    data.update(kw)
    order = PreSaleOrder.objects.create(**data)
    # A discount on a one-line order is that line's (keeps item_price = Σ line totals).
    unit_discount = order.discount_applied / lines[0][1] if len(lines) == 1 else Decimal('0')
    for opt, qty in lines:
        PreSaleOrderItem.objects.create(order=order, option=opt, quantity=qty, unit_price=opt.price,
                                        unit_discount=unit_discount, arrived_at=opt.arrived_at)
    return order
