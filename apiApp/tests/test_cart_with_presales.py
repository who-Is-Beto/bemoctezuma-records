"""A presale paid in the same cart checkout as store records; shipping all in one
box (one fee) or the records first (two fees); presale shipments in Pedidos going
through enviado → entregado; favorites counts in the cart and favorites."""
from decimal import Decimal as D
from unittest import mock

import pytest
from django.utils import timezone

from apiApp.models import Cart, CartItem, Category, Order, PreSaleOption, Record, Wishlist, WishlistItem
from apiApp.services import presales as svc
from apiApp.services.checkout import fulfill_checkout
from apiApp.tests.presale_factories import make_option, make_order
from apiApp.tests.test_presales_admin import role_user
from apiApp.tests.test_presales_checkout import ADDRESS, QUOTE

FAKE_SESSION = {'id': 'cs_combo', 'url': 'https://checkout.stripe.com/c/cs_combo'}
RECORD_QUOTE = [{'courier': 'Estafeta', 'serviceType': 'Terrestre', 'title': 'Estafeta', 'total': D('99'),
                 'currency': 'MXN'}]


@pytest.fixture
def client(api_client, user):
    user.email_verified = True
    user.save()
    api_client.force_authenticate(user)
    return api_client


@pytest.fixture
def cart(user):
    cart = Cart.objects.create(user=user)
    record = Record.objects.create(title='Blue Train', price=D('500'), stock=3, category=Category.objects.create(name='LP'))
    CartItem.objects.create(cart=cart, record=record, quantity=1)
    return cart


def checkout(client, cart, option, *, shipped_to='pickup', amount='800', together=None):
    data = {'cart_code': cart.cart_code, 'shipped_to': shipped_to,
            'presale': {'items': [{'option_id': option.id, 'quantity': 1}], 'amount': amount, 'accept_terms': True}}
    if shipped_to == 'home':
        data['shippingDetails'] = ADDRESS
        data['presale']['ship_together'] = together
    with mock.patch('stripe.checkout.Session.create', return_value=FAKE_SESSION) as create, \
            mock.patch('apiApp.views.checkout.get_shipping_quotes', return_value=RECORD_QUOTE) as record_quote, \
            mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE) as presale_quote:
        res = client.post('/create-checkout-session/', data, format='json')
    return res, create, record_quote, presale_quote


def paid(create):
    """The Stripe session as it comes back paid, built from what was sent."""
    kwargs = create.call_args.kwargs
    total = sum(line['price_data']['unit_amount'] * line['quantity'] for line in kwargs['line_items'])
    return {'id': 'cs_combo', 'payment_status': 'paid', 'amount_total': total, 'currency': 'mxn',
            'customer_email': 'buyer@example.com', 'metadata': kwargs['metadata']}


def fulfil(session):
    svc.fulfil_presale_session(session)
    with mock.patch('stripe.checkout.Session.list_line_items', side_effect=Exception('offline')):
        fulfill_checkout(session, session['metadata']['cart_code'])
    return Order.objects.get(stripe_checkout_session_id='cs_combo')


@pytest.mark.django_db
def test_presale_and_records_pay_in_one_checkout(client, cart):
    option = make_option()
    res, create, _, _ = checkout(client, cart, option, amount='200')
    assert res.status_code == 200, res.json()
    names = [line['price_data']['product_data']['name'] for line in create.call_args.kwargs['line_items']]
    assert names[0] == 'Blue Train' and 'apartado' in names[1]
    order = fulfil(paid(create))
    presale = svc.PreSaleOrder.objects.get()
    assert order.amount == D('500') and order.order_items.count() == 1
    assert presale.delivery == 'pickup' and svc.paid_amount(presale) == D('200') and presale.status == 'apartado'
    assert not cart.cart_items.exists()


@pytest.mark.django_db
def test_shipping_together_is_one_fee_for_one_box(client, cart):
    res, create, record_quote, presale_quote = checkout(client, cart, make_option(), shipped_to='home', together=True)
    assert res.status_code == 200, res.json()
    record_quote.assert_not_called()  # no separate fee for the records
    package = presale_quote.call_args.args[1]
    assert package['height'] == 4  # presale copy + record, one box
    lines = create.call_args.kwargs['line_items']
    assert [line['price_data']['product_data']['name'] for line in lines].count('Envío (todo junto en una caja)') == 1
    order = fulfil(paid(create))
    presale = svc.PreSaleOrder.objects.get()
    assert order.ships_with == presale and order.shipping_cost is None and order.amount == D('500')
    assert presale.shipping_cost == D('150') and svc.shipping_paid(presale) == D('150')

    # It leaves with the presale, same guide, and arrives with it.
    svc.mark_arrived(PreSaleOption.objects.get(), by=None)
    presale.refresh_from_db()
    svc.mark_shipped(presale, tracking='https://t.co/9', by=None)
    order.refresh_from_db()
    assert order.status == 'shipped' and order.shipping_link == 'https://t.co/9'
    svc.mark_received(presale, by=None)
    order.refresh_from_db()
    assert order.status == 'delivered'


@pytest.mark.django_db
def test_shipping_separately_charges_two_fees(client, cart):
    res, create, record_quote, _ = checkout(client, cart, make_option(), shipped_to='home', together=False)
    assert res.status_code == 200, res.json()
    record_quote.assert_called_once()
    names = [line['price_data']['product_data']['name'] for line in create.call_args.kwargs['line_items']]
    assert 'Envío de la preventa' in names and any(n.startswith('Envío (') for n in names)
    order = fulfil(paid(create))
    assert order.ships_with is None and order.shipping_cost == D('99')


@pytest.mark.django_db
def test_presale_deposit_with_shipping_together_requotes_the_whole_box(client, cart):
    option = make_option()
    res, create, _, _ = checkout(client, cart, option, shipped_to='home', together=True, amount='200')
    assert res.status_code == 200
    fulfil(paid(create))
    presale = svc.PreSaleOrder.objects.get()
    assert presale.shipping_cost is None  # charged when it arrives
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE) as quote:
        svc.requote_shipping(presale)
    assert quote.call_args.args[1]['height'] == 4


@pytest.mark.django_db
def test_presale_checkout_needs_terms(client, cart):
    data = {'cart_code': cart.cart_code, 'shipped_to': 'pickup',
            'presale': {'items': [{'option_id': make_option().id, 'quantity': 1}], 'amount': '800'}}
    res = client.post('/create-checkout-session/', data, format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'terms_required'


@pytest.mark.django_db
def test_pedidos_takes_presale_shipments_to_delivered(api_client):
    order = make_order(make_option(arrived_at=timezone.now()), delivery='shipping', shipping_cost=D('150'),
                       shipping_details={'zip': '15400'})
    svc.record_payment(order, D('950'), 'cash', shipping_amount=D('150'))
    api_client.force_authenticate(role_user('tab_manage_orders', 'change_order'))
    url = f'/presales/admin/orders/{order.id}/'
    assert api_client.post(url + 'received/', {}, format='json').status_code == 400  # not shipped yet
    assert api_client.post(url + 'ship/', {'tracking': 'abc'}, format='json').json()['status'] == 'enviado'
    res = api_client.post(url + 'ship/', {'tracking': 'https://t.co/fixed'}, format='json')
    assert res.json()['shipping_link'] == 'https://t.co/fixed'  # the guide can be corrected
    assert api_client.post(url + 'received/', {}, format='json').json()['status'] == 'entregado'
    assert [o['code'] for o in api_client.get('/presales/admin/shipments/').json()] == [order.code]


@pytest.mark.django_db
def test_cart_and_favorites_show_how_many_have_it_in_favorites(client, cart, user, django_user_model):
    record = cart.cart_items.get().record
    for name in ('a', 'b'):
        other = django_user_model.objects.create_user(username=name, email=f'{name}@x.com', password='x')
        WishlistItem.objects.create(wishlist=Wishlist.objects.create(user=other), record=record)
    res = client.get(f'/cart/{cart.cart_code}/')
    assert res.status_code == 200 and res.json()['cart_items'][0]['favorites_count'] == 2


@pytest.mark.django_db
def test_presale_estimate_can_include_the_cart_records(api_client, cart):
    data = {'items': [{'option_id': make_option().id, 'quantity': 1}], 'zip': '64000', 'cart_code': cart.cart_code}
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE) as quote:
        res = api_client.post('/presales/shipping-estimate/', data, format='json')
    assert res.status_code == 200 and quote.call_args.args[1]['height'] == 4
