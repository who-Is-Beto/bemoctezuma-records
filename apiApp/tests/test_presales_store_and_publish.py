"""Shipping buyers picking up at the store, presale shipments in Pedidos,
publishing several presales with one email, and the finer presale permissions."""
from decimal import Decimal as D
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone

from apiApp.models import PreSaleSubscriber
from apiApp.services import presales as svc
from apiApp.services.emailing import _presale_email_context
from apiApp.services.sales import presale_figures
from apiApp.tests.presale_factories import make_option, make_order, make_presale
from apiApp.tests.test_presales_admin import role_user
from apiApp.tests.test_presales_newsletter import SyncThread
from apiApp.tests.test_sales_metrics import admin_client  # noqa: F401


def shipping_order(**kw):
    option = make_option(arrived_at=timezone.now())
    return make_order(option, delivery='shipping', shipping_cost=D('150'),
                      shipping_details={'zip': '15400'}, **kw)


@pytest.mark.django_db
def test_paid_shipping_is_given_back_when_picked_up_at_the_store(admin_client, user):
    order = shipping_order()
    svc.record_payment(order, D('950'), 'stripe', stripe_session_id='cs_ship', shipping_amount=D('150'))
    res = admin_client.post(f'/presales/admin/orders/{order.id}/to-pickup/', {}, format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'refund_shipping_required'

    res = admin_client.post(f'/presales/admin/orders/{order.id}/to-pickup/', {'shipping_refunded': True}, format='json')
    assert res.status_code == 200, res.json()
    order.refresh_from_db()
    assert order.delivery == 'pickup' and order.shipping_cost is None and order.status == 'listo'
    assert svc.balance(order) == 0 and svc.shipping_paid(order) == 0
    figures = presale_figures(order)
    assert figures['gross'] == D('800') and figures['shipping'] == 0 and set(figures['split']) == {'stripe'}
    svc.deliver(order, by=user)
    assert order.status == 'entregado'


@pytest.mark.django_db
def test_unpaid_shipping_buyer_settles_records_only_at_the_store(admin_client):
    order = shipping_order()
    svc.record_payment(order, D('200'), 'cash')
    res = admin_client.post(f'/presales/admin/orders/{order.id}/to-pickup/', {}, format='json')
    assert res.status_code == 200
    order.refresh_from_db()
    assert svc.balance(order) == D('600')  # no shipping left to pay
    assert order.payments.count() == 1  # nothing to give back


@pytest.mark.django_db
def test_pedidos_lists_paid_presale_shipments_and_can_ship_them(api_client):
    paid = shipping_order()
    svc.record_payment(paid, D('950'), 'cash', shipping_amount=D('150'))
    owing = shipping_order()
    svc.record_payment(owing, D('200'), 'cash')
    api_client.force_authenticate(role_user('tab_manage_orders', 'change_order'))
    res = api_client.get('/presales/admin/shipments/')
    assert res.status_code == 200 and [o['code'] for o in res.json()] == [paid.code]
    res = api_client.post(f'/presales/admin/orders/{paid.id}/ship/', {'tracking': 'https://t.co/1'}, format='json')
    assert res.status_code == 200 and res.json()['status'] == 'enviado'
    # Shipping is all that role may do to a presale.
    assert api_client.post(f'/presales/admin/orders/{owing.id}/cancel/', {'reason': 'x'}, format='json').status_code == 403


@pytest.mark.django_db
def test_order_emails_always_show_what_the_buyer_pays():
    order = shipping_order()
    svc.record_payment(order, D('300'), 'cash')
    context = _presale_email_context(order, 'payment')
    assert context['records_total'] == '$800.00' and context['shipping'] == '$150.00'
    assert context['total'] == '$950.00' and context['balance'] == '$650.00'
    assert 'envío de $150.00 incluido' in _presale_email_context(order, 'arrived')['intro']


@pytest.mark.django_db
def test_publishing_several_presales_sends_one_email(admin_client):
    PreSaleSubscriber.objects.create(email='ana@example.com', confirmed_at=timezone.now())
    first, second = make_presale(title='Kind of Blue', status='draft'), make_presale(title='Abbey Road', status='draft')
    make_option(first)
    make_option(second, description='2 LP + póster', items_inside=2)
    with mock.patch('apiApp.services.emailing.threading.Thread', SyncThread):
        res = admin_client.post('/presales/admin/publish/', {'ids': [first.id, second.id], 'announce': True},
                                format='json')
    assert res.status_code == 200 and res.json() == {'published': 2, 'recipients': 1}
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == second.status == 'published' and first.announced_at and second.announced_at
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert 'Kind of Blue' in body and 'Abbey Road' in body


@pytest.mark.django_db
def test_presale_permissions_are_split(api_client):
    presale = make_presale(status='draft')
    option = make_option(presale)
    api_client.force_authenticate(role_user('tab_presales', 'change_presale'))
    assert api_client.post(f'/presales/admin/{presale.id}/publish/', {}, format='json').status_code == 403
    assert api_client.post(f'/presales/admin/options/{option.id}/arrived/', {}, format='json').status_code == 403
    assert api_client.patch(f'/presales/admin/options/{option.id}/', {'copies': 12}, format='json').status_code == 200
