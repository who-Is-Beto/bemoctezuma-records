"""API for orders with several version lines."""
from decimal import Decimal
from unittest import mock

import pytest

from apiApp.models import PreSaleOrder
from apiApp.services import presales as svc
from apiApp.tests.presale_factories import make_option, make_order
from apiApp.tests.test_presales_admin import admin_client  # noqa: F401
from apiApp.tests.test_presales_checkout import ADDRESS, FAKE_SESSION, QUOTE, client  # noqa: F401

D = Decimal


@pytest.fixture
def red(db):
    return make_option(name='Rojo')


@pytest.fixture
def black(red):
    return make_option(red.presale, name='Negro', price=D('600'))


def post_reserve(client, items, **extra):
    data = {'items': items, 'amount': '600', 'delivery': 'pickup', 'accept_terms': True, **extra}
    with mock.patch('stripe.checkout.Session.create', return_value=FAKE_SESSION) as create:
        res = client.post('/presales/reserve/', data, format='json')
    return res, create


def test_reserve_two_versions_one_order(client, red, black):
    res, create = post_reserve(client, [{'option_id': red.id, 'quantity': 2}, {'option_id': black.id, 'quantity': 1}])
    assert res.status_code == 200, res.json()
    order = PreSaleOrder.objects.get()
    assert [(i.option_id, i.quantity) for i in order.items.all()] == [(red.id, 2), (black.id, 1)]
    lines = create.call_args.kwargs['line_items']
    assert len(lines) == 2
    assert sum(line['price_data']['unit_amount'] for line in lines) == 60000
    assert 'Rojo ×2' in lines[0]['price_data']['product_data']['name']


@pytest.mark.parametrize('items', [[], [{'option_id': 0, 'quantity': 1}], 'x'])
def test_reserve_rejects_bad_items(client, red, items):
    res, create = post_reserve(client, items)
    assert res.status_code == 400 and res.json()['error']['code'] == 'invalid_items'
    create.assert_not_called()


def test_reserve_over_limit_is_409(client, red):
    red.max_per_buyer = 1
    red.save()
    res, _ = post_reserve(client, [{'option_id': red.id, 'quantity': 2}], amount='400')
    assert res.status_code == 409 and res.json()['error']['code'] == 'limit_exceeded'


def test_reserve_keeps_reference(client, red):
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE):
        res, _ = post_reserve(client, [{'option_id': red.id, 'quantity': 1}], amount='200', delivery='shipping',
                              shipping_details={**ADDRESS, 'reference': 'Portón verde'})
    assert res.status_code == 200, res.json()
    assert PreSaleOrder.objects.get().shipping_details['reference'] == 'Portón verde'


def test_public_option_shows_bought_for_user(client, user, red):
    make_order(red, quantity=2, user=user)
    options = client.get(f'/presales/{red.presale.slug}/').json()['options']
    assert options[0]['bought'] == 2 and 'max_per_buyer' in options[0]


def test_estimate_takes_items(client, red, black):
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE) as quotes:
        res = client.post('/presales/shipping-estimate/', {
            'items': [{'option_id': red.id, 'quantity': 2}, {'option_id': black.id, 'quantity': 1}], 'zip': '64000',
        }, format='json')
    assert res.status_code == 200 and res.json()['shipping_estimate'] == '150.00'
    assert quotes.call_args.args[1]['height'] == 6


def test_order_payload_lists_items(client, user, red, black):
    make_order(lines=[(red, 2), (black, 1)], user=user)
    order = client.get('/presales/orders/mine/').json()[0]
    assert [(i['option_name'], i['quantity'], i['line_total']) for i in order['items']] == [
        ('Rojo', 2, '1600.00'), ('Negro', 1, '600.00')]
    assert order['lines_label'] == 'Rojo ×2, Negro ×1' and order['all_arrived'] is False


def test_admin_deliver_some_lines(admin_client, red, black):
    order = make_order(lines=[(red, 1), (black, 1)])
    svc.record_payment(order, D('800'), 'cash')
    svc.mark_arrived(red, by=None)
    red_line = order.items.get(option=red)
    res = admin_client.post(f'/presales/admin/orders/{order.id}/deliver/', {'item_ids': [red_line.id]}, format='json')
    assert res.status_code == 200, res.json()
    red_line.refresh_from_db()
    assert red_line.delivered_at is not None and res.json()['status'] != 'entregado'


def test_all_arrived_sends_one_email_per_order(admin_client, red, black):
    from django.core import mail
    order = make_order(lines=[(red, 1), (black, 1)])
    mail.outbox.clear()
    res = admin_client.post(f'/presales/admin/{red.presale_id}/arrived/', format='json')
    assert res.status_code == 200
    assert [m.to[0] for m in mail.outbox if order.code in m.subject] == ['ana@example.com']
