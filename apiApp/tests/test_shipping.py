"""Tests for the Envíos Perros shipping integration and the improved search.

Shipping: POST /shipping/quote/ quotes home delivery via Envíos Perros; the
checkout re-quotes server-side (never trusting browser-sent amounts) and adds
the cheapest Estafeta option as a Stripe line item.

Search: 'zoe' must find 'Zoé' and 'trex' must find 'T. Rex' via normalized
slug matching.
"""
from decimal import Decimal
from unittest import mock

import pytest
import requests as requests_lib
from django.contrib.auth import get_user_model
from django.urls import reverse

from apiApp.models import Artist, Cart, CartItem, Order, Record, Category
from apiApp.services import (
    PACKAGE_TARE_GRAMS,
    ShippingQuoteError,
    _normalize_quote,
    build_package_from_cart,
    normalize_zip_code,
    record_unit_weight_grams,
)


# ── Helpers ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _gate_off(settings):
    """Shipping/search tests are not about the email-verification gate
    (covered in test_verification_gate.py); disable it for determinism since
    local .env may enable it."""
    settings.REQUIRE_EMAIL_VERIFICATION = False


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests_lib.HTTPError(f'status {self.status_code}')


def _make_quote(courier='Estafeta', total=100, service='Económica'):
    return {
        'title': f'{courier} - {service}',
        'total': total,
        'currency': 'MXN',
        'courier': courier,
        'serviceType': service,
        'deliveryCommitment': '2 a 5 días hábiles',
        'reshipment': False,
        'pickup': False,
        'deliveryAtOffice': False,
    }


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(
        username='shipuser',
        email='ship@example.com',
        password='ShipPass123!',
    )


@pytest.fixture
def cart(db, user):
    return Cart.objects.create(user=user)


@pytest.fixture
def lp_category(db):
    return Category.objects.create(name='LP')


@pytest.fixture
def cart_with_record(db, cart, lp_category):
    record = Record.objects.create(
        title='Disco De Prueba',
        price=Decimal('500.00'),
        stock=5,
        items_inside=2,
        category=lp_category,
    )
    CartItem.objects.create(cart=cart, record=record, quantity=1)
    return cart


# ── ZIP normalization ────────────────────────────────────────────────────


def test_normalize_zip_code_keeps_leading_zero():
    assert normalize_zip_code('04460') == '04460'


def test_normalize_zip_code_pads_short_input():
    assert normalize_zip_code('4460') == '04460'
    # JSON numbers strip the leading zero before Django sees it.
    assert normalize_zip_code(4460) == '04460'


def test_normalize_zip_code_rejects_garbage():
    assert normalize_zip_code('abcde') is None
    assert normalize_zip_code('123456') is None
    assert normalize_zip_code('') is None
    assert normalize_zip_code(None) is None


# ── Package building / weights ───────────────────────────────────────────


def test_record_unit_weight_defaults(lp_category, db):
    cd = Category.objects.create(name='CD')
    seven = Category.objects.create(name="7'")
    lp = Record.objects.create(title='LP', price=1, stock=1, category=lp_category)
    cd_r = Record.objects.create(title='CD', price=1, stock=1, category=cd)
    seven_r = Record.objects.create(title='Sencillo', price=1, stock=1, category=seven)
    assert record_unit_weight_grams(lp) == 300
    assert record_unit_weight_grams(cd_r) == 85
    assert record_unit_weight_grams(seven_r) == 100


def test_explicit_weight_grams_wins(lp_category, db):
    record = Record.objects.create(
        title='Boxset Pesado', price=1, stock=1, weight_grams=1200, category=lp_category,
    )
    assert record_unit_weight_grams(record) == 1200


def test_build_package_scales_weight_and_height(db, cart_with_record):
    package = build_package_from_cart(cart_with_record)
    # 2 units inside × 300g LP default + 300g tare = 900g -> clamped to 1kg min
    assert package['weight'] == 1.0
    assert package['type'] == 'Box'
    assert package['height'] == 4  # 2cm per unit × 2 units


def test_build_package_clamps_to_upstream_minimum(db, cart):
    """Envíos Perros rejects packages under 1 kg; light carts must be padded."""
    cd_category = Category.objects.create(name='CD')
    record = Record.objects.create(
        title='CD Ligero', price=Decimal('100.00'), stock=3,
        items_inside=1, category=cd_category,
    )
    CartItem.objects.create(cart=cart, record=record, quantity=1)
    package = build_package_from_cart(cart)
    # 85g + 300g tare = 0.385kg raw -> clamped up to 1.0
    assert package['weight'] == 1.0


# ── POST /shipping/quote/ ────────────────────────────────────────────────


def test_shipping_quote_picks_cheapest_estafeta(api_client, user, cart_with_record):
    api_client.force_authenticate(user=user)
    quotes = [
        _make_quote(courier='DHL', total=150),
        _make_quote(courier='Estafeta', total=120),
        _make_quote(courier='Estafeta', total=97.5),
    ]
    with mock.patch('apiApp.services.shipping.requests.post', return_value=FakeResponse(200, quotes)):
        resp = api_client.post(
            reverse('shipping-quote'),
            {'cart_code': cart_with_record.cart_code, 'zip': '01000'},
            format='json',
        )
    assert resp.status_code == 200, resp.content
    assert resp.data['selected']['courier'] == 'Estafeta'
    assert Decimal(str(resp.data['selected']['total'])) == Decimal('97.5')
    assert len(resp.data['quotes']) == 3


def test_shipping_quote_without_estafeta_returns_404(api_client, user, cart_with_record):
    api_client.force_authenticate(user=user)
    quotes = [_make_quote(courier='DHL', total=150)]
    with mock.patch('apiApp.services.shipping.requests.post', return_value=FakeResponse(200, quotes)):
        resp = api_client.post(
            reverse('shipping-quote'),
            {'cart_code': cart_with_record.cart_code, 'zip': '01000'},
            format='json',
        )
    assert resp.status_code == 404
    assert resp.data['error']['code'] == 'shipping_unavailable'


def test_shipping_quote_invalid_zip(api_client, user, cart_with_record):
    api_client.force_authenticate(user=user)
    resp = api_client.post(
        reverse('shipping-quote'),
        {'cart_code': cart_with_record.cart_code, 'zip': 'abc'},
        format='json',
    )
    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'invalid_zip_code'


def test_shipping_quote_upstream_failure_returns_502(api_client, user, cart_with_record):
    api_client.force_authenticate(user=user)
    with mock.patch(
        'apiApp.services.shipping.requests.post',
        side_effect=requests_lib.ConnectionError('boom'),
    ):
        resp = api_client.post(
            reverse('shipping-quote'),
            {'cart_code': cart_with_record.cart_code, 'zip': '01000'},
            format='json',
        )
    assert resp.status_code == 502
    assert resp.data['error']['code'] == 'shipping_quote_error'


# ── Prod response shape (captured 2026-08-20 from app.enviosperros.com) ──

# Prod nests everything under "details" and marks unavailable couriers with
# available=false + details=null — different from the flat staging/blueprint
# shape. Payload below is the real prod response for CDMX, trimmed.
PROD_RATES_PAYLOAD = [
    {
        "summary": "Estafeta Económica",
        "available": True,
        "details": {
            "courier": "Estafeta", "service": "Económica",
            "total": 150, "shippingTotal": 150, "insuranceTotal": None,
            "counterPrice": 274.52, "counterDiscountPercentage": 45.36,
            "rating": 4.5, "imageUrl": "https://app.enviosperros.com/images/aliados/estafeta.svg",
            "currency": "MXN", "deliveryCommitment": "2 a 5 días hábiles",
            "reshipment": False, "pickup": False, "deliveryAtOffice": False,
        },
        "comment": None,
    },
    {
        "summary": "PaqueteExpress Económica",
        "available": True,
        "details": {
            "courier": "PaqueteExpress", "service": "Económica",
            "total": 143, "currency": "MXN",
        },
        "comment": None,
    },
    {
        "summary": "Estafeta Express",
        "available": True,
        "details": {"courier": "Estafeta", "service": "Express", "total": 165, "currency": "MXN"},
        "comment": None,
    },
    {
        # Unavailable couriers carry no price at all.
        "summary": "DHL Express",
        "available": False,
        "details": None,
        "comment": "Tu cuenta alcanzo el limite de envíos, verifica tu identidad para seguir enviando.",
    },
]


def test_normalize_quote_handles_prod_nested_shape():
    normalized = _normalize_quote(PROD_RATES_PAYLOAD[0])
    assert normalized == {
        'title': 'Estafeta Económica',
        'total': Decimal('150.00'),
        'currency': 'MXN',
        'courier': 'Estafeta',
        'serviceType': 'Económica',
        'deliveryCommitment': '2 a 5 días hábiles',
    }


def test_normalize_quote_drops_unavailable_and_garbage():
    assert _normalize_quote(PROD_RATES_PAYLOAD[3]) is None  # unavailable
    assert _normalize_quote(None) is None
    assert _normalize_quote('nope') is None
    assert _normalize_quote({'courier': 'X'}) is None  # no total
    assert _normalize_quote({'total': 'abc', 'courier': 'X'}) is None  # bad total


def test_normalize_quote_keeps_flat_staging_shape():
    quote = _make_quote(courier='Estafeta', total=97.5)
    normalized = _normalize_quote(quote)
    assert normalized['title'] == 'Estafeta - Económica'
    assert normalized['total'] == Decimal('97.50')
    assert normalized['courier'] == 'Estafeta'


def test_shipping_quote_with_real_prod_payload(api_client, user, cart_with_record):
    """End-to-end against the captured prod response: cheapest Estafeta wins,
    the unavailable DHL entry is dropped from the list."""
    api_client.force_authenticate(user=user)
    with mock.patch(
        'apiApp.services.shipping.requests.post',
        return_value=FakeResponse(200, PROD_RATES_PAYLOAD),
    ):
        resp = api_client.post(
            reverse('shipping-quote'),
            {'cart_code': cart_with_record.cart_code, 'zip': '01000'},
            format='json',
        )
    assert resp.status_code == 200, resp.content
    assert resp.data['selected']['courier'] == 'Estafeta'
    assert Decimal(str(resp.data['selected']['total'])) == Decimal('150.00')
    assert resp.data['selected']['title'] == 'Estafeta Económica'
    # DHL (unavailable) filtered out; PaqueteExpress cheaper than Estafeta but
    # store policy pins Estafeta.
    assert len(resp.data['quotes']) == 3


# ── GET /shipping/locations/ (Sepomex colonias per ZIP) ──────────────────

# Real prod response for 64000 (Monterrey): one ZIP, several colonias.
LOCATIONS_PAYLOAD = [
    {"zipCode": "64000", "neighborhood": "Monterrey Centro",
     "city": "Monterrey", "state": "Nuevo Leon"},
    {"zipCode": "64000", "neighborhood": "La Finca",
     "city": "Monterrey", "state": "Nuevo Leon"},
]


@pytest.fixture(autouse=True)
def _clear_locations_cache():
    from django.core.cache import cache
    yield
    cache.clear()


def test_shipping_locations_lists_colonias(api_client, user):
    api_client.force_authenticate(user=user)
    with mock.patch(
        'apiApp.services.shipping.requests.get',
        return_value=FakeResponse(200, LOCATIONS_PAYLOAD),
    ) as get_mock:
        resp = api_client.get(reverse('shipping-locations'), {'zip': '64000'})
    assert resp.status_code == 200, resp.content
    assert [loc['neighborhood'] for loc in resp.data['locations']] == [
        'Monterrey Centro', 'La Finca'
    ]
    assert resp.data['locations'][0]['city'] == 'Monterrey'
    called_zip = get_mock.call_args.kwargs['params']['zipCode']
    assert called_zip == '64000'


def test_shipping_locations_cached(api_client, user):
    api_client.force_authenticate(user=user)
    with mock.patch(
        'apiApp.services.shipping.requests.get',
        return_value=FakeResponse(200, LOCATIONS_PAYLOAD),
    ) as get_mock:
        api_client.get(reverse('shipping-locations'), {'zip': '64000'})
        api_client.get(reverse('shipping-locations'), {'zip': '64000'})
    assert get_mock.call_count == 1  # second hit served from cache


def test_shipping_locations_invalid_zip(api_client, user):
    api_client.force_authenticate(user=user)
    resp = api_client.get(reverse('shipping-locations'), {'zip': 'abc'})
    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'invalid_zip_code'


def test_shipping_locations_unknown_zip_returns_empty(api_client, user):
    api_client.force_authenticate(user=user)
    with mock.patch(
        'apiApp.services.shipping.requests.get',
        return_value=FakeResponse(200, []),
    ):
        resp = api_client.get(reverse('shipping-locations'), {'zip': '00000'})
    assert resp.status_code == 200
    assert resp.data['locations'] == []


def test_shipping_locations_upstream_failure_returns_502(api_client, user):
    api_client.force_authenticate(user=user)
    with mock.patch(
        'apiApp.services.shipping.requests.get',
        side_effect=requests_lib.ConnectionError('boom'),
    ):
        resp = api_client.get(reverse('shipping-locations'), {'zip': '64000'})
    assert resp.status_code == 502


# ── Admin orders tab: list + update status/shipping_link ─────────────────

@pytest.fixture
def admin(db):
    User = get_user_model()
    return User.objects.create_user(
        username='shipadmin',
        email='shipadmin@example.com',
        password='AdminPass123!',
        role='ADMIN',
    )


def _make_order(**overrides):
    defaults = dict(
        stripe_checkout_session_id=f'cs_test_admin_{overrides.get("user_email", "x")}',
        amount=Decimal('100.00'),
        currency='mxn',
        user_email='buyer@example.com',
        shipped_to='home',
        status='paid',
    )
    defaults.update(overrides)
    return Order.objects.create(**defaults)


def test_admin_list_orders_requires_admin(api_client, user, db):
    api_client.force_authenticate(user=user)
    resp = api_client.get(reverse('admin-list-orders'))
    assert resp.status_code == 403


def test_admin_list_orders(api_client, admin, db):
    _make_order(stripe_checkout_session_id='cs_test_a', user_email='a@x.com')
    _make_order(stripe_checkout_session_id='cs_test_b', user_email='b@x.com')
    api_client.force_authenticate(user=admin)
    resp = api_client.get(reverse('admin-list-orders'))
    assert resp.status_code == 200
    assert len(resp.data) == 2
    # Newest first
    assert resp.data[0]['user_email'] == 'b@x.com'
    assert 'order_items' in resp.data[0]
    assert 'shipping_link' in resp.data[0]


def test_admin_update_order_status_and_link(api_client, admin, db):
    order = _make_order(stripe_checkout_session_id='cs_test_c')
    api_client.force_authenticate(user=admin)
    resp = api_client.patch(
        reverse('admin-update-order', args=[order.id]),
        {'status': 'shipped', 'shipping_link': 'https://estafeta.com/track/ABC123'},
        format='json',
    )
    assert resp.status_code == 200, resp.content
    assert resp.data['status'] == 'shipped'
    assert resp.data['shipping_link'] == 'https://estafeta.com/track/ABC123'
    order.refresh_from_db()
    assert order.status == 'shipped'


def test_admin_update_order_rejects_invalid_status(api_client, admin, db):
    order = _make_order(stripe_checkout_session_id='cs_test_d')
    api_client.force_authenticate(user=admin)
    resp = api_client.patch(
        reverse('admin-update-order', args=[order.id]),
        {'status': 'flying'},
        format='json',
    )
    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'invalid_status'
    order.refresh_from_db()
    assert order.status == 'paid'


def test_admin_update_order_validates_link_length(api_client, admin, db):
    order = _make_order(stripe_checkout_session_id='cs_test_e')
    api_client.force_authenticate(user=admin)
    resp = api_client.patch(
        reverse('admin-update-order', args=[order.id]),
        {'shipping_link': 'x' * 256},
        format='json',
    )
    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'invalid_shipping_link'
    # Empty string clears the link.
    ok = api_client.patch(
        reverse('admin-update-order', args=[order.id]),
        {'shipping_link': ''},
        format='json',
    )
    assert ok.status_code == 200
    assert ok.data['shipping_link'] == ''


# ── Shipped notification email ───────────────────────────────────────────

def test_marking_shipped_sends_email(api_client, admin, db):
    order = _make_order(stripe_checkout_session_id='cs_test_ship1')
    api_client.force_authenticate(user=admin)
    with mock.patch('apiApp.views.admin.send_order_shipped_email') as send_mock:
        resp = api_client.patch(
            reverse('admin-update-order', args=[order.id]),
            {'status': 'shipped', 'shipping_link': 'https://estafeta.com/track/XYZ'},
            format='json',
        )
    assert resp.status_code == 200
    send_mock.assert_called_once()
    emailed_order = send_mock.call_args.args[0]
    assert emailed_order.id == order.id
    # Email sees the link saved in this same request.
    assert emailed_order.shipping_link == 'https://estafeta.com/track/XYZ'


def test_link_only_update_on_shipped_order_does_not_resend(api_client, admin, db):
    order = _make_order(
        stripe_checkout_session_id='cs_test_ship2',
        status='shipped',
        shipping_link='https://estafeta.com/track/OLD',
    )
    api_client.force_authenticate(user=admin)
    with mock.patch('apiApp.views.admin.send_order_shipped_email') as send_mock:
        resp = api_client.patch(
            reverse('admin-update-order', args=[order.id]),
            {'shipping_link': 'https://estafeta.com/track/NEW'},
            format='json',
        )
    assert resp.status_code == 200
    send_mock.assert_not_called()


def test_other_status_changes_do_not_email(api_client, admin, db):
    order = _make_order(stripe_checkout_session_id='cs_test_ship3')
    api_client.force_authenticate(user=admin)
    with mock.patch('apiApp.views.admin.send_order_shipped_email') as send_mock:
        api_client.patch(
            reverse('admin-update-order', args=[order.id]),
            {'status': 'delivered'},
            format='json',
        )
    send_mock.assert_not_called()


# ── Checkout integration ─────────────────────────────────────────────────


SHIPPING_DETAILS = {
    'fullName': 'Roberto Cortes',
    'phone': '5512345678',
    'street': 'Av Siempre Viva',
    'number': '742',
    'neighborhood': 'Centro',
    'city': 'CDMX',
    'state': 'CDMX',
    'zip': '01000',
}


def _fake_stripe_session():
    return {
        'id': 'cs_test_shipping',
        'url': 'https://checkout.stripe.com/test',
        'client_reference_id': 'cart-code',
    }


def test_checkout_adds_estafeta_line_item(api_client, user, cart_with_record, settings):
    settings.ENVIOS_PERROS_TOKEN = 'test-token'
    api_client.force_authenticate(user=user)
    quotes = [_make_quote(courier='Estafeta', total=97.5)]
    fake_session = _fake_stripe_session()
    with mock.patch('apiApp.views.checkout.get_shipping_quotes', return_value=quotes), \
         mock.patch('stripe.checkout.Session.create', return_value=fake_session) as stripe_create:
        resp = api_client.post(
            reverse('create-checkout-session'),
            {
                'cart_code': cart_with_record.cart_code,
                'shipped_to': 'home',
                'shippingDetails': SHIPPING_DETAILS,
            },
            format='json',
        )
    assert resp.status_code == 200, resp.content
    kwargs = stripe_create.call_args.kwargs
    shipping_items = [li for li in kwargs['line_items'] if 'Envío' in li['price_data']['product_data']['name']]
    assert len(shipping_items) == 1
    assert shipping_items[0]['price_data']['unit_amount'] == 9750
    assert kwargs['metadata']['shipping_cost'] == '97.50'
    assert kwargs['metadata']['shipping_courier'] == 'Estafeta'
    # The stored ZIP is normalized even if the frontend sends a number.
    assert '01000' in kwargs['metadata']['shipping_details']


def test_checkout_home_blocked_when_no_estafeta(api_client, user, cart_with_record, settings):
    settings.ENVIOS_PERROS_TOKEN = 'test-token'
    api_client.force_authenticate(user=user)
    quotes = [_make_quote(courier='DHL', total=150)]
    with mock.patch('apiApp.views.checkout.get_shipping_quotes', return_value=quotes), \
         mock.patch('stripe.checkout.Session.create') as stripe_create:
        resp = api_client.post(
            reverse('create-checkout-session'),
            {
                'cart_code': cart_with_record.cart_code,
                'shipped_to': 'home',
                'shippingDetails': SHIPPING_DETAILS,
            },
            format='json',
        )
    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'shipping_unavailable'
    stripe_create.assert_not_called()


def test_checkout_pickup_skips_quoting(api_client, user, cart_with_record, settings):
    settings.ENVIOS_PERROS_TOKEN = 'test-token'
    api_client.force_authenticate(user=user)
    fake_session = _fake_stripe_session()
    with mock.patch('apiApp.views.checkout.get_shipping_quotes') as quote_mock, \
         mock.patch('stripe.checkout.Session.create', return_value=fake_session):
        resp = api_client.post(
            reverse('create-checkout-session'),
            {'cart_code': cart_with_record.cart_code, 'shipped_to': 'store'},
            format='json',
        )
    assert resp.status_code == 200, resp.content
    quote_mock.assert_not_called()


# ── Fulfillment persistence ──────────────────────────────────────────────


def test_fulfill_checkout_persists_shipping_fields(db, cart_with_record):
    from apiApp.services import fulfill_checkout

    session = {
        'id': 'cs_test_fulfill',
        'amount_total': 59750,  # 500.00 products + 97.50 shipping
        'currency': 'mxn',
        'customer_email': 'ship@example.com',
        'metadata': {
            'cart_code': cart_with_record.cart_code,
            'shipped_to': 'home',
            'shipping_details': '{"fullName": "Roberto", "zip": 1000}',
            'shipping_cost': '97.50',
            'shipping_courier': 'Estafeta',
            'shipping_service': 'Económica',
        },
    }
    fulfill_checkout(session, cart_with_record.cart_code)
    order = Order.objects.get(stripe_checkout_session_id='cs_test_fulfill')
    assert order.amount == Decimal('597.50')
    assert order.shipping_cost == Decimal('97.50')
    assert order.shipping_courier == 'Estafeta'
    assert order.shipping_service == 'Económica'
    assert order.shipping_link == 'Preparando para envío'
    # Address values are coerced to strings (note the int zip -> '1000').
    assert order.shipping_details['zip'] == '1000'
    assert isinstance(order.shipping_details['fullName'], str)


# ── Search: accents & punctuation ────────────────────────────────────────


@pytest.fixture
def zoe_record(db):
    artist = Artist.objects.create(name='Zoé')
    return Record.objects.create(title='Reptilectric', artist=artist, price=1, stock=1)


@pytest.fixture
def trex_record(db):
    artist = Artist.objects.create(name='T. Rex')
    return Record.objects.create(title='Electric Warrior', artist=artist, price=1, stock=1)


def test_artist_search_ignores_accents(api_client, zoe_record):
    resp = api_client.get('/artists/search/', {'q': 'zoe'})
    assert resp.status_code == 200
    names = [a['name'] for a in resp.json()]
    assert 'Zoé' in names


def test_artist_search_ignores_punctuation(api_client, trex_record):
    resp = api_client.get('/artists/search/', {'q': 'trex'})
    assert resp.status_code == 200
    names = [a['name'] for a in resp.json()]
    assert 'T. Rex' in names


def test_record_search_finds_by_normalized_artist(api_client, zoe_record, trex_record):
    resp = api_client.get('/search/', {'query': 'zoe'})
    assert resp.status_code == 200
    titles = [r['title'] for r in resp.data['results']]
    assert 'Reptilectric' in titles

    resp = api_client.get('/search/', {'query': 'trex'})
    titles = [r['title'] for r in resp.data['results']]
    assert 'Electric Warrior' in titles
