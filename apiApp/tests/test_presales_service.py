from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apiApp.services import presales as svc
from apiApp.services.presales import PresaleError
from apiApp.tests.presale_factories import make_option, make_order, make_presale

D = Decimal


@pytest.fixture
def option(db):
    return make_option()


# ── split_amount ───────────────────────────────────────────────────────────

def test_split_deposit(option):
    assert svc.split_amount([(option, 1)], D('400'), 'pickup', None) == {
        'item_price': D('800'), 'discount': D('0'), 'shipping_cost': None, 'charge': D('400'), 'full': False,
    }


def test_split_full_gets_discount_and_shipping(option):
    assert svc.split_amount([(option, 1)], D('750'), 'shipping', D('150')) == {
        'item_price': D('750'), 'discount': D('50'), 'shipping_cost': D('150'), 'charge': D('900'), 'full': True,
    }


def test_split_between_discounted_and_price_is_full(option):
    result = svc.split_amount([(option, 1)], D('780'), 'pickup', None)
    assert result['charge'] == D('750')
    assert result['discount'] == D('50')


def test_split_rejects_below_minimum(option):
    with pytest.raises(PresaleError) as exc:
        svc.split_amount([(option, 1)], D('199.99'), 'pickup', None)
    assert exc.value.code == 'amount_too_low'


def test_split_rejects_three_decimals(option):
    with pytest.raises(PresaleError) as exc:
        svc.split_amount([(option, 1)], D('300.001'), 'pickup', None)
    assert exc.value.code == 'invalid_amount'


def test_split_above_price_rejected(option):
    with pytest.raises(PresaleError) as exc:
        svc.split_amount([(option, 1)], D('800.01'), 'pickup', None)
    assert exc.value.code == 'invalid_amount'


def test_split_full_shipping_requires_quote(option):
    with pytest.raises(PresaleError) as exc:
        svc.split_amount([(option, 1)], D('750'), 'shipping', None)
    assert exc.value.code == 'shipping_quote_required'


# ── deadline ───────────────────────────────────────────────────────────────

def test_deadline_before_arrival_is_reservation_plus_120(option):
    order = make_order(option)
    assert svc.deadline(order) == timezone.localdate(order.reserved_at) + timedelta(days=120)


def test_deadline_after_late_arrival_is_arrival_plus_15(option):
    order = make_order(option, reserved_at=timezone.now() - timedelta(days=200))
    arrived = timezone.now() - timedelta(days=2)
    order.items.update(arrived_at=arrived)  # the clock starts when the buyer's own copy arrives
    order.refresh_from_db()
    assert svc.deadline(order) == timezone.localdate(arrived) + timedelta(days=15)


def test_deadline_override_wins_when_later(option):
    later = timezone.localdate() + timedelta(days=400)
    order = make_order(option, deadline_override=later)
    assert svc.deadline(order) == later


def test_not_expirable_before_arrival(option):
    order = make_order(option, reserved_at=timezone.now() - timedelta(days=200))
    assert svc.is_expirable(order) is False


def test_expirable_after_arrival_deadline(option):
    option.arrived_at = timezone.now() - timedelta(days=20)
    option.save()
    order = make_order(option, reserved_at=timezone.now() - timedelta(days=200))
    assert svc.is_expirable(order) is True


# ── payments & status ──────────────────────────────────────────────────────

def test_payment_to_full_sets_pagado_and_paid_at(option):
    order = make_order(option)
    svc.record_payment(order, D('200'), 'cash')
    order.refresh_from_db()
    assert order.status == 'apartado' and order.paid_at is None
    svc.record_payment(order, D('600'), 'transfer')
    order.refresh_from_db()
    assert order.status == 'pagado' and order.paid_at is not None
    assert svc.balance(order) == D('0')


def test_pickup_paid_and_arrived_is_listo(option):
    option.arrived_at = timezone.now()
    option.save()
    order = make_order(option)
    svc.record_payment(order, D('800'), 'cash')
    order.refresh_from_db()
    assert order.status == 'listo'


def test_shipping_paid_without_quote_is_pagado_and_shipping_pending(option):
    order = make_order(option, delivery='shipping')
    svc.record_payment(order, D('800'), 'cash')
    order.refresh_from_db()
    assert order.status == 'pagado'
    assert svc.shipping_pending(order) is True


def test_void_reopens_balance_and_clears_paid_at(option, user):
    order = make_order(option)
    svc.record_payment(order, D('200'), 'cash')
    last = svc.record_payment(order, D('600'), 'cash')
    svc.void_payment(last, by=user, reason='Capturado dos veces')
    order.refresh_from_db()
    assert order.status == 'apartado'
    assert order.paid_at is None
    assert svc.balance(order) == D('600')
    last.refresh_from_db()
    assert last.voided_by == user and last.void_reason == 'Capturado dos veces'


def test_void_requires_reason(option, user):
    order = make_order(option)
    payment = svc.record_payment(order, D('200'), 'cash')
    with pytest.raises(PresaleError):
        svc.void_payment(payment, by=user, reason='  ')


def test_record_payment_rejects_over_balance(option):
    order = make_order(option)
    with pytest.raises(PresaleError) as exc:
        svc.record_payment(order, D('800.01'), 'cash')
    assert exc.value.code == 'amount_exceeds_balance'


def test_record_payment_rejects_zero(option):
    with pytest.raises(PresaleError):
        svc.record_payment(make_order(option), D('0'), 'cash')


def test_stripe_payment_is_idempotent(option):
    order = make_order(option)
    a = svc.record_payment(order, D('200'), 'stripe', stripe_session_id='cs_1')
    b = svc.record_payment(order, D('200'), 'stripe', stripe_session_id='cs_1')
    assert a.pk == b.pk
    assert order.payments.count() == 1


# ── create_order & copies ──────────────────────────────────────────────────

def _online(option, user):
    return svc.create_order([(option, 1)], buyer=user, source='online', delivery='pickup',
                            shipping_details=None, amount=D('200'), accept_terms=True)


def test_create_order_requires_terms(option, user):
    with pytest.raises(PresaleError) as exc:
        svc.create_order([(option, 1)], buyer=user, source='online', delivery='pickup',
                         shipping_details=None, amount=D('200'), accept_terms=False)
    assert exc.value.code == 'terms_required'


def test_online_order_without_a_saved_name_uses_the_username(option, user):
    user.first_name = None
    user.last_name = None
    user.save()
    assert _online(option, user).buyer_name == user.username


def test_online_order_is_held(option, user):
    order = _online(option, user)
    assert order.status == 'pending_payment'
    assert order.hold_expires_at > timezone.now()
    assert order.terms_version == svc.TERMS_VERSION
    assert svc.copies_left(option) == 9


def test_reserve_last_copy_is_locked(user):
    option = make_option(copies=1)
    _online(option, user)
    with pytest.raises(PresaleError) as exc:
        _online(option, user)
    assert exc.value.code == 'sold_out'


def test_expired_hold_frees_the_copy(user):
    option = make_option(copies=1)
    held = _online(option, user)
    held.hold_expires_at = timezone.now() - timedelta(minutes=1)
    held.save()
    assert svc.copies_left(option) == 1
    _online(option, user)


def test_create_order_rejects_deadline_passed(user):
    option = make_option(make_presale(purchase_deadline=timezone.localdate() - timedelta(days=1)))
    with pytest.raises(PresaleError) as exc:
        _online(option, user)
    assert exc.value.code == 'deadline_passed'


def test_create_order_rejects_inactive_option(user):
    option = make_option(is_active=False)
    with pytest.raises(PresaleError) as exc:
        _online(option, user)
    assert exc.value.code == 'inactive'


def test_admin_order_records_payment(option, user):
    order = svc.create_order([(option, 1)], buyer={'name': 'Luis', 'email': '', 'phone': '5512345678'},
                             source='instagram', delivery='pickup', shipping_details=None,
                             amount=D('750'), method='cash', created_by=user, accept_terms=True)
    assert order.status == 'pagado'
    assert order.item_price == D('750') and order.discount_applied == D('50')
    assert order.payments.get().registered_by == user


# ── arrival, delivery, reopen ──────────────────────────────────────────────

def test_mark_arrived_moves_paid_pickups_to_listo(option, user):
    order = make_order(option)
    svc.record_payment(order, D('800'), 'cash')
    svc.mark_arrived(option, by=user)
    order.refresh_from_db()
    assert order.status == 'listo'


def test_deliver_requires_zero_balance(option, user):
    order = make_order(option)
    svc.record_payment(order, D('200'), 'cash')
    svc.mark_arrived(option, by=user)
    with pytest.raises(PresaleError) as exc:
        svc.deliver(order, by=user)
    assert exc.value.code == 'paid_not_enough'


def test_deliver_twice_fails(option, user):
    option.arrived_at = timezone.now()
    option.save()
    order = make_order(option)
    svc.record_payment(order, D('800'), 'cash')
    svc.deliver(order, by=user)
    order.refresh_from_db()
    assert order.status == 'entregado' and order.delivered_by == user
    with pytest.raises(PresaleError) as exc:
        svc.deliver(order, by=user)
    assert exc.value.code == 'already_delivered'


def test_reopen_needs_free_copy(user):
    option = make_option(copies=1)
    cancelled = make_order(option)
    svc.cancel(cancelled, by=user, reason='No respondió')
    make_order(option)  # someone else took the copy
    with pytest.raises(PresaleError) as exc:
        svc.reopen(cancelled, by=user)
    assert exc.value.code == 'sold_out'


def test_expire_requires_expirable(option, user):
    with pytest.raises(PresaleError):
        svc.expire(make_order(option), by=user)
