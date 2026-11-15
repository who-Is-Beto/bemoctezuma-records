import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone

from apiApp.models import PreSaleOrder
from apiApp.services import presales as svc
from apiApp.tests.presale_factories import make_option, make_order

QUOTE = [{'courier': 'Estafeta', 'serviceType': 'Terrestre', 'title': 'Estafeta Terrestre',
          'total': '150.00', 'available': True}]
ADDRESS = {'fullName': 'Ana López', 'phone': '5512345678', 'street': 'Reforma', 'number': '10',
           'neighborhood': 'Centro', 'city': 'Monterrey', 'state': 'Nuevo León', 'zip': '64000'}
FAKE_SESSION = {'id': 'cs_test_1', 'url': 'https://checkout.stripe.com/c/cs_test_1'}


@pytest.fixture
def client(api_client, user):
    user.email_verified = True
    user.save()
    api_client.force_authenticate(user)
    return api_client


def reserve(client, option, **extra):
    data = {'items': [{'option_id': option.id, 'quantity': 1}], 'amount': '400', 'delivery': 'pickup',
            'accept_terms': True}
    data.update(extra)
    with mock.patch('stripe.checkout.Session.create', return_value=FAKE_SESSION) as create:
        res = client.post('/presales/reserve/', data, format='json')
    return res, create


def paid_session(order, amount, purpose='reserve', shipping='0', session_id='cs_test_1'):
    return {
        'id': session_id, 'payment_status': 'paid', 'amount_total': int(Decimal(amount) * 100),
        'currency': 'mxn',
        'metadata': {'kind': 'presale', 'order_id': str(order.id), 'purpose': purpose, 'shipping': shipping},
    }


@pytest.mark.django_db
def test_reserve_requires_terms(client):
    res, create = reserve(client, make_option(), accept_terms=False)
    assert res.status_code == 400
    assert res.json()['error']['code'] == 'terms_required'
    create.assert_not_called()


@pytest.mark.django_db
def test_reserve_requires_login(api_client):
    res = api_client.post('/presales/reserve/', {}, format='json')
    assert res.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize('amount', ['abc', '199', '800.01', '-5', ''])
def test_reserve_rejects_bad_amounts(client, amount):
    res, create = reserve(client, make_option(), amount=amount)
    assert res.status_code == 400
    create.assert_not_called()
    assert PreSaleOrder.objects.count() == 0


@pytest.mark.django_db
def test_reserve_creates_hold_and_stripe_session(client):
    option = make_option()
    res, create = reserve(client, option)
    assert res.status_code == 200
    assert res.json()['checkout_url'] == FAKE_SESSION['url']
    order = PreSaleOrder.objects.get()
    assert order.status == 'pending_payment' and res.json()['code'] == order.code
    kwargs = create.call_args.kwargs
    assert kwargs['line_items'][0]['price_data']['unit_amount'] == 40000
    assert kwargs['metadata'] == {'kind': 'presale', 'order_id': str(order.id), 'purpose': 'reserve', 'shipping': '0.00'}
    assert '/preventas/' in kwargs['cancel_url']


@pytest.mark.django_db
def test_reserve_780_is_full_payment_at_750(client):
    res, create = reserve(client, make_option(), amount='780')
    assert res.status_code == 200
    assert create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'] == 75000


@pytest.mark.django_db
def test_full_reserve_charges_requoted_shipping(client):
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE):
        res, create = reserve(client, make_option(), amount='750', delivery='shipping', shipping_details=ADDRESS)
    assert res.status_code == 200
    items = create.call_args.kwargs['line_items']
    assert [i['price_data']['unit_amount'] for i in items] == [75000, 15000]
    order = PreSaleOrder.objects.get()
    assert order.shipping_cost == Decimal('150.00')


@pytest.mark.django_db
def test_deposit_with_shipping_only_estimates(client):
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE):
        res, create = reserve(client, make_option(), amount='300', delivery='shipping', shipping_details=ADDRESS)
    assert res.status_code == 200
    assert len(create.call_args.kwargs['line_items']) == 1
    order = PreSaleOrder.objects.get()
    assert order.shipping_cost is None and order.shipping_estimate == Decimal('150.00')


@pytest.mark.django_db
def test_reserve_shipping_requires_address(client):
    res, _ = reserve(client, make_option(), delivery='shipping', shipping_details={'zip': '64000'})
    assert res.status_code == 400


@pytest.mark.django_db
def test_presale_fulfilment_is_idempotent(client):
    reserve(client, make_option())
    order = PreSaleOrder.objects.get()
    session = paid_session(order, '400')
    with mock.patch('stripe.Webhook.construct_event', return_value={'id': 'evt', 'type': 'checkout.session.completed', 'data': {'object': session}}), \
            mock.patch('apiApp.views.checkout.settings.STRIPE_WEBHOOK_SECRET', 'whsec', create=True):
        assert client.post('/stripe-webhook/', data=json.dumps({}), content_type='application/json',
                           HTTP_STRIPE_SIGNATURE='sig').status_code == 200
    with mock.patch('stripe.checkout.Session.retrieve', return_value=session):
        res = client.post('/presales/checkout/complete/', {'session_id': 'cs_test_1'}, format='json')
    assert res.status_code == 200
    assert res.json()['paid'] == '400.00'
    assert order.payments.count() == 1
    order.refresh_from_db()
    assert order.status == 'apartado'


@pytest.mark.django_db
def test_unpaid_session_records_nothing(client):
    reserve(client, make_option())
    order = PreSaleOrder.objects.get()
    session = {**paid_session(order, '400'), 'payment_status': 'unpaid'}
    with mock.patch('stripe.checkout.Session.retrieve', return_value=session):
        client.post('/presales/checkout/complete/', {'session_id': 'cs_test_1'}, format='json')
    assert order.payments.count() == 0


@pytest.mark.django_db
def test_late_payment_after_hold_expired_is_kept_and_flagged(client, user):
    option = make_option(copies=1)
    reserve(client, option)
    order = PreSaleOrder.objects.get()
    order.hold_expires_at = timezone.now() - timedelta(minutes=5)
    order.save()
    make_order(option)  # the copy went to someone else meanwhile
    svc.fulfil_presale_session(paid_session(order, '400'))
    order.refresh_from_db()
    assert order.status == 'apartado'
    assert order.oversold is True
    assert svc.paid_amount(order) == Decimal('400')


@pytest.mark.django_db
def test_settle_charges_balance_only(client, user):
    order = make_order(user=user)
    svc.record_payment(order, Decimal('300'), 'cash')
    with mock.patch('stripe.checkout.Session.create', return_value=FAKE_SESSION) as create:
        res = client.post(f'/presales/orders/{order.code}/settle/', format='json')
    assert res.status_code == 200
    kwargs = create.call_args.kwargs
    assert [i['price_data']['unit_amount'] for i in kwargs['line_items']] == [50000]
    assert kwargs['metadata']['purpose'] == 'settle'


@pytest.mark.django_db
def test_settle_includes_known_shipping(client, user):
    order = make_order(user=user, delivery='shipping', shipping_cost=Decimal('150'))
    svc.record_payment(order, Decimal('800'), 'cash')
    with mock.patch('stripe.checkout.Session.create', return_value=FAKE_SESSION) as create:
        client.post(f'/presales/orders/{order.code}/settle/', format='json')
    kwargs = create.call_args.kwargs
    assert [i['price_data']['unit_amount'] for i in kwargs['line_items']] == [15000]
    assert kwargs['metadata']['shipping'] == '150.00'


@pytest.mark.django_db
@pytest.mark.parametrize('amount,cents', [('100', 10000), ('250', 25000), ('500', 50000)])
def test_settle_abono_charges_chosen_amount(client, user, amount, cents):
    order = make_order(user=user)  # balance 500 after the cash below
    svc.record_payment(order, Decimal('300'), 'cash')
    with mock.patch('stripe.checkout.Session.create', return_value=FAKE_SESSION) as create:
        res = client.post(f'/presales/orders/{order.code}/settle/', {'amount': amount}, format='json')
    assert res.status_code == 200, res.json()
    assert [i['price_data']['unit_amount'] for i in create.call_args.kwargs['line_items']] == [cents]


@pytest.mark.django_db
@pytest.mark.parametrize('amount', ['50', '120', '450.50', '550'])
def test_settle_abono_rejects_bad_amounts(client, user, amount):
    order = make_order(user=user)
    svc.record_payment(order, Decimal('300'), 'cash')
    res = client.post(f'/presales/orders/{order.code}/settle/', {'amount': amount}, format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'invalid_partial'


@pytest.mark.django_db
def test_settle_abono_leaves_shipping_for_last(client, user):
    order = make_order(user=user, delivery='shipping', shipping_cost=Decimal('150'))
    svc.record_payment(order, Decimal('300'), 'cash')  # 500 records + 150 shipping due
    with mock.patch('stripe.checkout.Session.create', return_value=FAKE_SESSION) as create:
        client.post(f'/presales/orders/{order.code}/settle/', {'amount': '200'}, format='json')
    kwargs = create.call_args.kwargs
    assert [i['price_data']['unit_amount'] for i in kwargs['line_items']] == [20000]
    assert kwargs['metadata']['shipping'] == '0.00'
    assert client.post(f'/presales/orders/{order.code}/settle/', {'amount': '600'},
                       format='json').json()['error']['code'] == 'invalid_partial'


@pytest.mark.django_db
def test_settle_nothing_due(client, user):
    order = make_order(user=user)
    svc.record_payment(order, Decimal('800'), 'cash')
    res = client.post(f'/presales/orders/{order.code}/settle/', format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'nothing_due'


@pytest.mark.django_db
def test_settle_other_users_order_is_404(client):
    order = make_order()
    assert client.post(f'/presales/orders/{order.code}/settle/', format='json').status_code == 404


@pytest.mark.django_db
def test_mine_lists_own_orders_without_pending(client, user):
    mine = make_order(user=user)
    make_order(user=user, status='pending_payment')
    make_order()
    res = client.get('/presales/orders/mine/')
    assert [o['code'] for o in res.json()] == [mine.code]
