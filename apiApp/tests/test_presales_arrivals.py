"""Copies arriving in batches go to buyers by priority: paid in full first, then by first payment."""
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone

from apiApp.services import presales as svc
from apiApp.tests.presale_factories import make_option, make_order
from apiApp.tests.test_sales_metrics import admin_client  # noqa: F401


def pay(order, amount, minutes_ago):
    payment = svc.record_payment(order, D(amount), 'cash')
    payment.created_at = timezone.now() - timedelta(minutes=minutes_ago)
    payment.save(update_fields=['created_at'])
    if order.paid_at:
        order.paid_at = payment.created_at
        order.save(update_fields=['paid_at'])
    return payment


def arrived(order):
    return [bool(i.arrived_at) for i in order.items.all()]


@pytest.mark.django_db
def test_paid_in_full_first_then_first_payment():
    option = make_option(copies=3)
    early_deposit = make_order(option, buyer_email='a@example.com')
    paid_full = make_order(option, buyer_email='b@example.com')
    late_deposit = make_order(option, buyer_email='c@example.com')
    pay(early_deposit, '200', 30)
    pay(paid_full, '800', 10)
    pay(late_deposit, '200', 5)
    mail.outbox.clear()
    svc.mark_arrived(option, by=None, copies=2)
    for order in (early_deposit, paid_full, late_deposit):
        order.refresh_from_db()
    assert arrived(paid_full) == [True] and arrived(early_deposit) == [True] and arrived(late_deposit) == [False]
    assert paid_full.status == 'listo'
    assert sorted(m.to[0] for m in mail.outbox) == ['a@example.com', 'b@example.com']
    # The next batch reaches the one still waiting.
    svc.mark_arrived(option, by=None, copies=1)
    late_deposit.refresh_from_db()
    assert arrived(late_deposit) == [True] and option.arrived_at is not None


@pytest.mark.django_db
def test_a_line_gets_all_its_copies_or_waits():
    option = make_option(copies=3)
    two = make_order(option, quantity=2)
    one = make_order(option)
    pay(two, '1600', 20)
    pay(one, '200', 10)
    svc.mark_arrived(option, by=None, copies=1)
    assert arrived(two) == [False] and arrived(one) == [True]
    svc.mark_arrived(option, by=None, copies=2)
    assert arrived(two) == [True]


@pytest.mark.django_db
def test_cancelled_order_releases_its_copy_to_the_next(user):
    option = make_option(copies=2)
    first, second = make_order(option), make_order(option)
    pay(first, '800', 20)
    pay(second, '200', 10)
    svc.mark_arrived(option, by=None, copies=1)
    assert arrived(first) == [True] and arrived(second) == [False]
    svc.cancel(first, by=user, reason='Reembolso')
    assert arrived(first) == [False] and arrived(second) == [True]


@pytest.mark.django_db
def test_payment_after_arrival_takes_a_spare_copy():
    option = make_option(copies=2)
    svc.mark_arrived(option, by=None, copies=1)  # nobody had bought yet
    order = make_order(option)
    pay(order, '800', 0)
    order.refresh_from_db()
    assert arrived(order) == [True] and order.status == 'listo'


@pytest.mark.django_db
def test_admin_registers_a_partial_arrival(admin_client):
    option = make_option(copies=5)
    order = make_order(option)
    pay(order, '200', 5)
    with mock.patch('apiApp.services.emailing.send_presale_email'):
        res = admin_client.post(f'/presales/admin/options/{option.id}/arrived/', {'copies': 2}, format='json')
    assert res.status_code == 200, res.json()
    option.refresh_from_db()
    assert option.arrived_copies == 2 and option.arrived_at is None and arrived(order) == [True]
    bad = admin_client.post(f'/presales/admin/options/{option.id}/arrived/', {'copies': 0}, format='json')
    assert bad.status_code == 400


# ── refund + void ───────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_voiding_a_stripe_payment_refunds_it_first(admin_client):
    order = make_order()
    payment = svc.record_payment(order, D('300'), 'stripe', stripe_session_id='cs_refund')
    with mock.patch('stripe.checkout.Session.retrieve', return_value={'payment_intent': 'pi_1'}), \
            mock.patch('stripe.Refund.create', return_value={'id': 're_1'}) as refund:
        res = admin_client.post(f'/presales/admin/payments/{payment.id}/void/', {'reason': 'Cliente canceló'},
                                format='json')
    assert res.status_code == 200, res.json()
    assert refund.call_args.kwargs['payment_intent'] == 'pi_1' and refund.call_args.kwargs['amount'] == 30000
    payment.refresh_from_db()
    assert payment.voided_at and 're_1' in payment.void_reason


@pytest.mark.django_db
def test_failed_refund_keeps_the_payment(admin_client):
    import stripe
    order = make_order()
    payment = svc.record_payment(order, D('300'), 'stripe', stripe_session_id='cs_fail')
    with mock.patch('stripe.checkout.Session.retrieve', side_effect=stripe.InvalidRequestError('nope', None)):
        res = admin_client.post(f'/presales/admin/payments/{payment.id}/void/', {'reason': 'X'}, format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'refund_failed'
    payment.refresh_from_db()
    assert payment.voided_at is None


@pytest.mark.django_db
def test_voiding_cash_never_touches_stripe(admin_client):
    order = make_order()
    payment = svc.record_payment(order, D('300'), 'cash')
    with mock.patch('stripe.Refund.create') as refund:
        admin_client.post(f'/presales/admin/payments/{payment.id}/void/', {'reason': 'Error'}, format='json')
    refund.assert_not_called()
