from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.utils import timezone

from apiApp.models import PreSaleOrder, PreSaleSubscriber
from apiApp.services.emailing import announce_presale, presale_recipient_count
from apiApp.tests.presale_factories import make_option, make_order, make_presale


class SyncThread:
    """Runs the announcement 'thread' inline so the test sees the emails."""
    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def confirm_token(email):
    return signing.dumps(email, salt='presale-subscribe')


@pytest.mark.django_db
def test_subscribe_sends_confirmation(api_client):
    res = api_client.post('/presales/subscribe/', {'email': 'Ana@Example.com'}, format='json')
    assert res.status_code == 200
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ['ana@example.com']
    assert '/preventas/confirmar?token=' in mail.outbox[0].body
    assert PreSaleSubscriber.objects.get().confirmed_at is None


@pytest.mark.django_db
def test_subscribe_same_response_for_existing(api_client):
    PreSaleSubscriber.objects.create(email='ana@example.com', confirmed_at=timezone.now())
    first = api_client.post('/presales/subscribe/', {'email': 'ana@example.com'}, format='json')
    new = api_client.post('/presales/subscribe/', {'email': 'luis@example.com'}, format='json')
    assert first.json() == new.json()
    assert [m.to for m in mail.outbox] == [['luis@example.com']]


@pytest.mark.django_db
def test_subscribe_rejects_invalid_email(api_client):
    assert api_client.post('/presales/subscribe/', {'email': 'nope'}, format='json').status_code == 400


@pytest.mark.django_db
def test_honeypot_sends_nothing(api_client):
    res = api_client.post('/presales/subscribe/', {'email': 'bot@x.com', 'website': 'spam'}, format='json')
    assert res.status_code == 200
    assert mail.outbox == [] and PreSaleSubscriber.objects.count() == 0


@pytest.mark.django_db
def test_confirm_with_token(api_client):
    PreSaleSubscriber.objects.create(email='ana@example.com')
    res = api_client.post('/presales/subscribe/confirm/', {'token': confirm_token('ana@example.com')}, format='json')
    assert res.status_code == 200
    assert PreSaleSubscriber.objects.get().confirmed_at is not None


@pytest.mark.django_db
def test_confirm_rejects_bad_token(api_client):
    assert api_client.post('/presales/subscribe/confirm/', {'token': 'x'}, format='json').status_code == 400


@pytest.mark.django_db
def test_confirm_then_announce_sends_once():
    PreSaleSubscriber.objects.create(email='ana@example.com', confirmed_at=timezone.now())
    PreSaleSubscriber.objects.create(email='pending@example.com')
    presale = make_presale()
    make_option(presale)
    assert presale_recipient_count() == 1
    with mock.patch('apiApp.services.emailing.threading.Thread', SyncThread):
        assert announce_presale(presale) == 1
        assert announce_presale(presale) == 0
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ['ana@example.com']
    assert f'/preventas/{presale.slug}' in message.body
    assert '/preventas/baja?token=' in message.body
    assert 'List-Unsubscribe' in message.extra_headers


@pytest.mark.django_db
def test_unsubscribed_gets_nothing(api_client):
    PreSaleSubscriber.objects.create(email='ana@example.com', confirmed_at=timezone.now())
    token = signing.dumps('ana@example.com', salt='presale-unsubscribe')
    assert api_client.post('/presales/unsubscribe/', {'token': token}, format='json').status_code == 200
    presale = make_presale()
    with mock.patch('apiApp.services.emailing.threading.Thread', SyncThread):
        assert announce_presale(presale) == 0
    assert mail.outbox == []


@pytest.mark.django_db
def test_logged_in_toggle_skips_confirmation(api_client, user):
    user.email_verified = True
    user.save()
    api_client.force_authenticate(user)
    assert api_client.get('/presales/subscription/').json() == {'subscribed': False}
    assert api_client.patch('/presales/subscription/', {'subscribed': True}, format='json').json() == {'subscribed': True}
    sub = PreSaleSubscriber.objects.get()
    assert sub.user == user and sub.confirmed_at is not None
    assert mail.outbox == []
    assert api_client.patch('/presales/subscription/', {'subscribed': False}, format='json').json() == {'subscribed': False}


@pytest.mark.django_db
def test_verification_links_admin_orders(api_client):
    from apiApp.serilizers.user import VerifyEmailSerializer  # noqa: F401 (ensures module exists)
    customer = get_user_model().objects.create_user(username='ana', email='ana@example.com', password='x')
    order = make_order(buyer_email='ANA@example.com')
    from apiApp.services.presales import link_orders_to_user
    link_orders_to_user(customer)
    order.refresh_from_db()
    assert order.user == customer
