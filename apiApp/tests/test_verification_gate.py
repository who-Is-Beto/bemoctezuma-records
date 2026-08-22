"""Email-verification gate: unverified users are blocked from cart/checkout
flows when REQUIRE_EMAIL_VERIFICATION is enabled, regardless of JWT presence.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

from apiApp.models import Cart, CartItem, Record


@pytest.fixture
def record(db):
    return Record.objects.create(title='Gate Test Record', price='250.00', stock=3)


@pytest.fixture
def unverified_user(db):
    User = get_user_model()
    return User.objects.create_user(
        username='gateuser',
        email='gate@example.com',
        password='GatePass123!',
    )


@pytest.fixture
def verified_user(db, unverified_user):
    unverified_user.email_verified = True
    unverified_user.save(update_fields=['email_verified'])
    return unverified_user


@pytest.fixture
def gate_on(settings):
    settings.REQUIRE_EMAIL_VERIFICATION = True


def _post_add_to_cart(api_client, record_id):
    return api_client.post(
        '/cart/add/',
        {'record_id': record_id},
        format='json',
    )


def test_add_to_cart_blocked_when_unverified_and_gate_on(gate_on, api_client, unverified_user, record):
    api_client.force_authenticate(user=unverified_user)
    resp = _post_add_to_cart(api_client, record.id)

    assert resp.status_code == 403
    assert resp.data['error']['code'] == 'email_not_verified'
    assert not CartItem.objects.exists()


def test_add_to_cart_allowed_when_verified_and_gate_on(gate_on, api_client, verified_user, record):
    api_client.force_authenticate(user=verified_user)
    resp = _post_add_to_cart(api_client, record.id)

    assert resp.status_code == 200
    assert CartItem.objects.count() == 1


def test_add_to_cart_allowed_when_unverified_and_gate_off(settings, api_client, unverified_user, record):
    # Explicitly disable the requirement -> no block.
    settings.REQUIRE_EMAIL_VERIFICATION = False
    api_client.force_authenticate(user=unverified_user)
    resp = _post_add_to_cart(api_client, record.id)

    assert resp.status_code == 200
    assert CartItem.objects.count() == 1


def test_get_carts_blocked_when_unverified_and_gate_on(gate_on, api_client, unverified_user):
    api_client.force_authenticate(user=unverified_user)
    resp = api_client.get(reverse('get-all-carts'))

    assert resp.status_code == 403
    assert resp.data['error']['code'] == 'email_not_verified'


def test_orders_blocked_when_unverified_and_gate_on(gate_on, api_client, unverified_user):
    api_client.force_authenticate(user=unverified_user)
    resp = api_client.get('/orders/')

    assert resp.status_code == 403
    assert resp.data['error']['code'] == 'email_not_verified'


def test_checkout_session_blocked_before_stripe_when_unverified(gate_on, api_client, unverified_user, record):
    # Build a real cart so the only reason to be rejected is the gate.
    cart = Cart.objects.create()
    CartItem.objects.create(cart=cart, record=record, quantity=1)

    api_client.force_authenticate(user=unverified_user)
    resp = api_client.post(
        '/create-checkout-session/',
        {'cart_code': cart.cart_code, 'shipped_to': 'store'},
        format='json',
    )

    assert resp.status_code == 403
    assert resp.data['error']['code'] == 'email_not_verified'
    # No verification email should be sent by the gate (outbox untouched).
    assert not mail.outbox


def test_get_me_returns_email_verified(api_client, unverified_user):
    """GET /auth/me/ must NOT be gated — the frontend uses it to re-sync the
    authoritative verification status for exactly this (unverified) case."""
    api_client.force_authenticate(user=unverified_user)
    resp = api_client.get('/auth/me/')

    assert resp.status_code == 200
    assert resp.data['email'] == 'gate@example.com'
    assert resp.data['email_verified'] is False


def test_get_me_requires_authentication(api_client):
    resp = api_client.get('/auth/me/')

    assert resp.status_code == 401


def test_add_to_cart_out_of_stock_does_not_create_item(settings, api_client, verified_user, record):
    """A record with zero stock must be rejected AND must not leave a phantom
    CartItem behind (regression: get_or_create ran before the stock check)."""
    settings.REQUIRE_EMAIL_VERIFICATION = True
    record.stock = 0
    record.save(update_fields=['stock'])

    api_client.force_authenticate(user=verified_user)
    resp = _post_add_to_cart(api_client, record.id)

    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'stock_insuficiente'
    assert not CartItem.objects.exists()


def test_add_to_cart_over_stock_does_not_create_item(settings, api_client, verified_user, record):
    """Requesting more than the available stock must be rejected without
    creating or mutating a CartItem."""
    settings.REQUIRE_EMAIL_VERIFICATION = True
    record.stock = 1
    record.save(update_fields=['stock'])

    api_client.force_authenticate(user=verified_user)
    resp = api_client.post(
        '/cart/add/',
        {'record_id': record.id, 'quantity': 2},
        format='json',
    )

    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'stock_insuficiente'
    assert not CartItem.objects.exists()


def test_add_to_cart_second_add_over_stock_rejected_without_mutation(settings, api_client, verified_user, record):
    """Adding again when the cart already holds all available stock must fail
    and leave the existing item untouched."""
    settings.REQUIRE_EMAIL_VERIFICATION = True
    record.stock = 1
    record.save(update_fields=['stock'])

    api_client.force_authenticate(user=verified_user)
    first = _post_add_to_cart(api_client, record.id)
    assert first.status_code == 200
    cart_code = first.data['cart_code']

    second = api_client.post(
        '/cart/add/',
        {'record_id': record.id, 'cart_code': cart_code},
        format='json',
    )
    assert second.status_code == 400
    assert second.data['error']['code'] == 'stock_insuficiente'

    cart_item = CartItem.objects.get()
    assert cart_item.quantity == 1


def test_add_to_cart_missing_record_id_returns_400(gate_on, api_client, verified_user):
    """Missing/non-numeric record_id must be a clean 400, not a 500."""
    api_client.force_authenticate(user=verified_user)
    resp = api_client.post('/cart/add/', {}, format='json')

    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'record_id_required'


def test_add_to_cart_invalid_quantity_returns_400(gate_on, api_client, verified_user, record):
    api_client.force_authenticate(user=verified_user)
    resp = api_client.post(
        '/cart/add/',
        {'record_id': record.id, 'quantity': 'abc'},
        format='json',
    )

    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'quantity_invalid'
    assert not CartItem.objects.exists()


def test_add_to_cart_zero_quantity_returns_400(gate_on, api_client, verified_user, record):
    api_client.force_authenticate(user=verified_user)
    resp = api_client.post(
        '/cart/add/',
        {'record_id': record.id, 'quantity': 0},
        format='json',
    )

    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'quantity_invalid'
    assert not CartItem.objects.exists()


def test_add_to_cart_unknown_record_returns_404(gate_on, api_client, verified_user):
    api_client.force_authenticate(user=verified_user)
    resp = api_client.post('/cart/add/', {'record_id': 999999}, format='json')

    assert resp.status_code == 404
    assert resp.data['error']['code'] == 'product_not_found'


def test_update_cart_quantity_invalid_returns_400(gate_on, api_client, verified_user, record):
    cart = Cart.objects.create(user=verified_user)
    cart_item = CartItem.objects.create(cart=cart, record=record, quantity=1)

    api_client.force_authenticate(user=verified_user)
    resp = api_client.put(
        '/cart/update/',
        {'item_id': cart_item.id, 'quantity': 'abc'},
        format='json',
    )

    assert resp.status_code == 400
    assert resp.data['error']['code'] == 'quantity_invalid'


def test_update_cart_quantity_unknown_item_returns_404(gate_on, api_client, verified_user):
    api_client.force_authenticate(user=verified_user)
    resp = api_client.put(
        '/cart/update/',
        {'item_id': 999999, 'quantity': 1},
        format='json',
    )

    assert resp.status_code == 404
    assert resp.data['error']['code'] == 'cart_item_not_found'
