import re
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apiApp.models import PreSale, PreSaleOption, PreSaleOrder, PreSaleSubscriber


def make_presale(title='Kind of Blue'):
    return PreSale.objects.create(title=title, artist='Miles Davis', purchase_deadline=date(2030, 1, 1))


def make_option(presale, **kw):
    data = dict(name='Vinilo rojo', price=Decimal('800'), min_deposit=Decimal('200'),
                full_payment_discount=Decimal('50'), copies=10)
    data.update(kw)
    return PreSaleOption.objects.create(presale=presale, **data)


@pytest.mark.django_db
def test_presale_slug_is_unique():
    a, b = make_presale(), make_presale()
    assert a.slug == 'kind-of-blue'
    assert b.slug != a.slug


@pytest.mark.django_db
def test_order_code_format():
    option = make_option(make_presale())
    order = PreSaleOrder.objects.create(
        buyer_name='Ana', item_price=Decimal('800'), reserved_at=timezone.now()
    )
    assert re.fullmatch(r'PV-[A-HJ-NP-Z2-9]{4}', order.code)


@pytest.mark.django_db
def test_option_clean_rejects_deposit_ge_price():
    option = PreSaleOption(presale=make_presale(), name='CD', price=Decimal('300'),
                           min_deposit=Decimal('300'), copies=5)
    with pytest.raises(ValidationError):
        option.clean()


@pytest.mark.django_db
def test_option_clean_rejects_discount_ge_price():
    option = PreSaleOption(presale=make_presale(), name='CD', price=Decimal('300'),
                           min_deposit=Decimal('100'), full_payment_discount=Decimal('300'), copies=5)
    with pytest.raises(ValidationError):
        option.clean()


@pytest.mark.django_db
def test_subscriber_email_is_lowercased():
    sub = PreSaleSubscriber.objects.create(email='Ana@Example.COM')
    assert sub.email == 'ana@example.com'


@pytest.mark.django_db
def test_order_lines_total_and_copies():
    from apiApp.tests import presale_factories as f
    red = f.make_option(copies=10)
    black = f.make_option(red.presale, name='Negro', price=Decimal('600'))
    order = f.make_order(lines=[(red, 2), (black, 3)])
    assert order.item_price == Decimal('3400')
    assert [i.quantity for i in order.items.order_by('id')] == [2, 3]
    assert order.items.order_by('id').first().line_total == Decimal('1600')
