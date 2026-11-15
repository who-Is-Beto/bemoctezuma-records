"""Regressions from the final review of the presales branch."""
import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from apiApp.models import PreSaleOrder
from apiApp.services import presales as svc
from apiApp.tests.presale_factories import make_option, make_order

D = Decimal


def paid(order, amount, session_id, purpose='reserve', shipping='0'):
    return {'id': session_id, 'payment_status': 'paid', 'amount_total': int(D(amount) * 100),
            'metadata': {'kind': 'presale', 'order_id': str(order.id), 'purpose': purpose, 'shipping': shipping}}


@pytest.mark.django_db
def test_last_copy_paid_within_hold_is_not_oversold(settings, user):
    settings.SELLER_NOTIFY_EMAILS = ['store@x.com']
    option = make_option(copies=1)
    order = svc.create_order([(option, 1)], buyer=user, source='online', delivery='pickup', shipping_details=None,
                             amount=D('200'), accept_terms=True)
    svc.fulfil_presale_session(paid(order, '200', 'cs_last'))
    order.refresh_from_db()
    assert order.oversold is False
    assert not any('Sobreventa' in m.subject for m in mail.outbox)


@pytest.mark.django_db
def test_stripe_session_expires_with_the_hold(api_client, user):
    user.email_verified = True
    user.save()
    api_client.force_authenticate(user)
    option = make_option()
    with mock.patch('stripe.checkout.Session.create', return_value={'id': 'cs', 'url': 'u'}) as create:
        api_client.post('/presales/reserve/', {'items': [{'option_id': option.id, 'quantity': 1}], 'amount': '200', 'delivery': 'pickup',
                                               'accept_terms': True}, format='json')
    expires = create.call_args.kwargs['expires_at']
    order = PreSaleOrder.objects.get()
    assert 30 * 60 <= expires - time.time() <= 31 * 60 + 5
    # Stripe can never complete after the copy is released.
    assert order.hold_expires_at.timestamp() > expires


@pytest.mark.django_db
def test_double_stripe_payment_alerts_the_store(settings, user):
    settings.SELLER_NOTIFY_EMAILS = ['store@x.com']
    order = make_order(user=user)
    svc.record_payment(order, D('300'), 'cash')
    svc.fulfil_presale_session(paid(order, '500', 'cs_a', purpose='settle'))
    svc.fulfil_presale_session(paid(order, '500', 'cs_b', purpose='settle'))
    assert any('Pago de más' in m.subject for m in mail.outbox if m.to == ['store@x.com'])


@pytest.mark.django_db
def test_qr_does_not_reveal_which_codes_exist(api_client):
    order = make_order()
    real = api_client.get(f'/presales/orders/{order.code}/qr.png')
    fake = api_client.get('/presales/orders/PV-ZZZZ/qr.png')
    assert real.status_code == fake.status_code == 200
    assert api_client.get('/presales/orders/not-a-code/qr.png').status_code == 400


@pytest.mark.django_db
def test_unquoted_shipping_alone_never_forfeits():
    option = make_option(arrived_at=timezone.now() - timedelta(days=40))
    order = make_order(option, delivery='shipping', reserved_at=timezone.now() - timedelta(days=200))
    svc.record_payment(order, D('800'), 'cash')  # record fully paid, shipping never quoted
    order.refresh_from_db()
    assert svc.is_expirable(order) is False


@pytest.mark.django_db
def test_no_reminder_when_only_unquoted_shipping_is_left():
    option = make_option(arrived_at=timezone.now() - timedelta(days=20))
    order = make_order(option, delivery='shipping', reserved_at=timezone.now() - timedelta(days=150),
                       deadline_override=timezone.localdate() + timedelta(days=7))
    svc.record_payment(order, D('800'), 'cash')
    mail.outbox.clear()
    call_command('presale_reminders')
    assert mail.outbox == []
