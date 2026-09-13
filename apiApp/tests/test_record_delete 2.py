"""Permanent record deletion (admin): endpoint behavior + history safety."""
import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.urls import reverse

from apiApp.models import Cart, CartItem, Order, OrderItem, Record, Review, Wishlist, WishlistItem


@pytest.fixture
def admin(db):
    User = get_user_model()
    return User.objects.create_user(
        username='deladmin',
        email='deladmin@example.com',
        password='AdminPass123!',
        role='ADMIN',
    )


@pytest.fixture
def record(db):
    return Record.objects.create(
        title='Disco a eliminar',
        price=Decimal('250.00'),
        stock=3,
    )


# ── Permissions ──────────────────────────────────────────────────────────

def test_delete_requires_authentication(api_client, db):
    resp = api_client.delete(reverse('admin-delete-record', args=[1]))
    assert resp.status_code in (401, 403)


def test_delete_requires_admin(api_client, user, record, db):
    api_client.force_authenticate(user=user)
    resp = api_client.delete(reverse('admin-delete-record', args=[record.id]))
    assert resp.status_code == 403


def test_delete_unknown_record_404(api_client, admin, db):
    api_client.force_authenticate(user=admin)
    resp = api_client.delete(reverse('admin-delete-record', args=[99999]))
    assert resp.status_code == 404
    assert resp.data['error']['code'] == 'record_not_found'


# ── Deletion semantics ───────────────────────────────────────────────────

def test_delete_removes_record_and_dependents(api_client, admin, record, db):
    cart = Cart.objects.create()
    CartItem.objects.create(cart=cart, record=record, quantity=1)
    wishlist = Wishlist.objects.create()
    WishlistItem.objects.create(wishlist=wishlist, record=record)
    Review.objects.create(record=record, user=admin, rating=5, review='x')

    api_client.force_authenticate(user=admin)
    resp = api_client.delete(reverse('admin-delete-record', args=[record.id]))

    assert resp.status_code == 200
    assert not Record.objects.filter(pk=record.id).exists()
    assert not CartItem.objects.count()
    assert not WishlistItem.objects.count()
    assert not Review.objects.count()


def test_delete_preserves_order_history(api_client, admin, record, db):
    """The critical one: deleting a sold record must NOT erase order items."""
    order = Order.objects.create(
        stripe_checkout_session_id='cs_test_del_hist',
        amount=Decimal('500.00'),
        currency='mxn',
        user_email='buyer@example.com',
        shipped_to='home',
        status='shipped',
    )
    OrderItem.objects.create(
        order=order,
        record=record,
        quantity=2,
        price=Decimal('250.00'),
    )

    api_client.force_authenticate(user=admin)
    resp = api_client.delete(reverse('admin-delete-record', args=[record.id]))
    assert resp.status_code == 200

    # Order + line item survive, detached from the deleted record.
    assert Order.objects.filter(pk=order.id).exists()
    item = OrderItem.objects.get(order=order)
    assert item.record is None
    assert item.quantity == 2
    assert item.price == Decimal('250.00')

    # Serializer renders the orphaned item as record: null.
    listing = api_client.get(reverse('admin-list-orders'))
    serialized_item = listing.data[0]['order_items'][0]
    assert serialized_item['record'] is None
    assert serialized_item['quantity'] == 2
