from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from apiApp.services import presales as svc
from apiApp.services.emailing import send_presale_email
from apiApp.tests.presale_factories import make_option, make_order

D = Decimal


@pytest.mark.django_db
def test_order_email_shows_how_much_is_paid():
    order = make_order()
    svc.record_payment(order, D('200'), 'cash')
    send_presale_email(order, 'payment')
    assert 'width:25%' in mail.outbox[-1].alternatives[0][0]


@pytest.mark.django_db
def test_reserved_email_has_code_balance_and_qr():
    order = make_order()
    svc.record_payment(order, D('300'), 'cash')
    assert send_presale_email(order, 'reserved') is True
    message = mail.outbox[-1]
    assert message.to == ['ana@example.com']
    assert order.code in message.subject or order.code in message.body
    html = message.alternatives[0][0]
    assert '$500.00' in html and 'cid:presale-qr' in html
    assert any(part.get('Content-ID') == '<presale-qr>' for part in message.attachments)


@pytest.mark.django_db
def test_email_skipped_without_address():
    order = make_order(buyer_email='')
    assert send_presale_email(order, 'reserved') is False
    assert mail.outbox == []


@pytest.mark.django_db
def test_email_failure_does_not_break_payment():
    order = make_order()
    with mock.patch('apiApp.services.emailing.send_email', side_effect=RuntimeError('smtp down')):
        assert send_presale_email(order, 'payment') is False
        svc.record_payment(order, D('100'), 'cash')
    assert svc.paid_amount(order) == D('100')


@pytest.mark.django_db
def test_stripe_reservation_emails_customer_and_store(settings, user):
    settings.SELLER_NOTIFY_EMAILS = ['store@x.com']
    order = make_order(user=user, status='pending_payment', hold_expires_at=timezone.now() + timedelta(minutes=10))
    svc.fulfil_presale_session({'id': 'cs_1', 'payment_status': 'paid', 'amount_total': 30000,
                                'metadata': {'kind': 'presale', 'order_id': str(order.id), 'purpose': 'reserve'}})
    recipients = sorted(m.to[0] for m in mail.outbox)
    assert recipients == ['store@x.com', 'test@example.com']


@pytest.mark.django_db
def test_qr_png_is_png(api_client):
    order = make_order()
    res = api_client.get(f'/presales/orders/{order.code}/qr.png')
    assert res.status_code == 200
    assert res['Content-Type'] == 'image/png'
    assert res.content[:8] == b'\x89PNG\r\n\x1a\n'


def _arrived_order(days_left):
    # Reserved 150 days ago and arrived 20 days ago: both computed deadlines are
    # past, so the admin override sets exactly how many days are left.
    option = make_option(arrived_at=timezone.now() - timedelta(days=20))
    target = timezone.localdate() + timedelta(days=days_left)
    return make_order(option, reserved_at=timezone.now() - timedelta(days=150), deadline_override=target)


@pytest.mark.django_db
def test_reminders_sent_once_at_30_and_7():
    at_30 = _arrived_order(30)
    at_7 = _arrived_order(7)
    _arrived_order(12)
    call_command('presale_reminders')
    call_command('presale_reminders')
    assert sorted(m.to[0] for m in mail.outbox) == ['ana@example.com', 'ana@example.com']
    at_30.refresh_from_db(); at_7.refresh_from_db()
    assert at_30.reminders_sent == [30] and at_7.reminders_sent == [7]


@pytest.mark.django_db
def test_reminders_skip_unarrived_and_paid():
    make_order(make_option(), deadline_override=timezone.localdate() + timedelta(days=30))
    paid = _arrived_order(7)
    svc.record_payment(paid, D('800'), 'cash')
    call_command('presale_reminders')
    assert mail.outbox == []
