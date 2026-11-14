"""Orders with several version lines: amounts, limits, stock, arrivals, partial pickup."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apiApp.services import presales as svc
from apiApp.services import shipping
from apiApp.services.presales import PresaleError
from apiApp.tests.presale_factories import make_option, make_order, make_presale

D = Decimal


@pytest.fixture
def red(db):
    return make_option(name='Rojo', price=D('800'), min_deposit=D('200'), full_payment_discount=D('50'))


@pytest.fixture
def black(red):
    return make_option(red.presale, name='Negro', price=D('600'), min_deposit=D('200'),
                       full_payment_discount=D('50'))


def _online(lines, user, amount):
    return svc.create_order(lines, buyer=user, source='online', delivery='pickup', shipping_details=None,
                            amount=amount, accept_terms=True)


def test_split_amount_sums_lines(red, black):
    lines = [(red, 2), (black, 3)]
    deposit = svc.split_amount(lines, D('1000'), 'pickup', None)
    assert (deposit['item_price'], deposit['discount'], deposit['full']) == (D('3400'), D('0'), False)
    full = svc.split_amount(lines, D('3150'), 'pickup', None)
    assert (full['item_price'], full['discount'], full['full']) == (D('3150'), D('250'), True)
    with pytest.raises(PresaleError) as exc:
        svc.split_amount(lines, D('999'), 'pickup', None)
    assert exc.value.code == 'amount_too_low'


def test_full_payment_stores_unit_discounts(red, black, user):
    order = _online([(red, 2), (black, 1)], user, D('2050'))
    assert [(i.quantity, i.unit_discount) for i in order.items.all()] == [(2, D('50')), (1, D('50'))]
    assert order.item_price == sum(i.line_total for i in order.items.all())


@pytest.mark.parametrize('raw', [
    [], 'x', [{'option_id': 'RED', 'quantity': 0}], [{'option_id': 'RED', 'quantity': -1}],
    [{'option_id': 'RED', 'quantity': 1}, {'option_id': 'RED', 'quantity': 2}],
    [{'option_id': 'OTHER', 'quantity': 1}], [{'option_id': 999999, 'quantity': 1}],
])
def test_parse_items_rejects_bad_input(red, raw):
    other = make_option(make_presale(title='Otro'))
    ids = {'RED': red.id, 'OTHER': other.id}
    raw = [dict(e, option_id=ids.get(e['option_id'], e['option_id'])) for e in raw] if isinstance(raw, list) else raw
    with pytest.raises(PresaleError) as exc:
        svc.parse_items(raw, presale=red.presale)
    assert exc.value.code == 'invalid_items'


def test_parse_items_reads_lines(red, black):
    lines = svc.parse_items([{'option_id': red.id, 'quantity': 2}, {'option_id': str(black.id), 'quantity': '1'}])
    assert [(o.pk, q) for o, q in lines] == [(red.pk, 2), (black.pk, 1)]


def test_limit_counts_existing_and_live_holds(red, user):
    red.max_per_buyer = 2
    red.save()
    make_order(red, user=user, status='pagado')
    _online([(red, 1)], user, D('200'))  # live hold
    with pytest.raises(PresaleError) as exc:
        _online([(red, 1)], user, D('200'))
    assert exc.value.code == 'limit_exceeded'
    assert 'Rojo' in exc.value.message
    admin = svc.create_order([(red, 1)], buyer={'name': 'Ana'}, source='store', delivery='pickup',
                             shipping_details=None, amount=D('200'), method='cash', accept_terms=True)
    assert admin.items.get().quantity == 1


def test_copies_left_counts_quantities(red, black):
    make_order(lines=[(red, 2), (black, 3)])
    assert (svc.copies_left(red), svc.copies_left(black)) == (8, 7)


def test_sold_out_names_the_version(red, user):
    red.copies = 1
    red.save()
    with pytest.raises(PresaleError) as exc:
        _online([(red, 2)], user, D('400'))
    assert exc.value.code == 'sold_out' and 'Rojo' in exc.value.message


def test_delivered_lines_never_return_to_stock(red, black, user):
    order = make_order(lines=[(red, 2), (black, 1)])
    svc.record_payment(order, D('1600'), 'cash')
    svc.mark_arrived(red, by=user)
    svc.deliver(order, by=user, item_ids=[order.items.get(option=red).pk])
    svc.cancel(order, by=user, reason='No vino por el resto')
    assert (svc.copies_left(red), svc.copies_left(black)) == (8, 10)


def test_deadline_waits_for_last_arrival(red, black, user):
    order = make_order(lines=[(red, 1), (black, 1)], reserved_at=timezone.now() - timedelta(days=200))
    base = timezone.localdate(order.reserved_at) + timedelta(days=120)
    svc.mark_arrived(red, by=user)
    assert svc.deadline(order) == base and not svc.all_arrived(order)
    assert not svc.is_expirable(order)
    svc.mark_arrived(black, by=user)
    assert svc.deadline(order) == timezone.localdate() + timedelta(days=15)


def test_partial_pickup_needs_paid_lines(red, black, user):
    order = make_order(lines=[(red, 2), (black, 1)])  # 2200
    svc.record_payment(order, D('1000'), 'cash')
    svc.mark_arrived(red, by=user)
    red_line = order.items.get(option=red)
    with pytest.raises(PresaleError) as exc:
        svc.deliver(order, by=user, item_ids=[red_line.pk])
    assert exc.value.code == 'paid_not_enough' and '$600.00' in exc.value.message
    svc.record_payment(order, D('600'), 'cash')
    svc.deliver(order, by=user, item_ids=[red_line.pk])
    order.refresh_from_db()
    assert order.status != 'entregado'
    with pytest.raises(PresaleError):  # black hasn't arrived
        svc.deliver(order, by=user, item_ids=[order.items.get(option=black).pk])
    svc.mark_arrived(black, by=user)
    svc.record_payment(order, D('600'), 'cash')
    order.refresh_from_db()
    assert order.status == 'listo'
    svc.deliver(order, by=user)
    order.refresh_from_db()
    assert order.status == 'entregado'


def test_shipping_waits_for_all_arrived(red, black, user):
    order = make_order(lines=[(red, 1), (black, 1)], delivery='shipping', shipping_cost=D('150'))
    svc.record_payment(order, D('1550'), 'cash')
    svc.mark_arrived(red, by=user)
    with pytest.raises(PresaleError) as exc:
        svc.mark_shipped(order, tracking='x', by=user)
    assert exc.value.code == 'invalid_status'


def test_package_for_items_counts_units(red, black):
    package = shipping.build_package_for_items([(red, 2), (black, 3)])
    assert (package['height'], package['weight']) == (10, 1.8)
