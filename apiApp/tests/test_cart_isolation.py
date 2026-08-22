"""Cart isolation: every user gets their own cart.

Regression tests for the prod incident where GET /carts returned ALL carts
and clients took the first one — everyone ended up sharing a single cart.
"""
import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apiApp.models import Cart, Record

User = get_user_model()


def _verified_user(username: str):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="Pass123!",
        email_verified=True,
    )


@pytest.fixture
def record(db):
    return Record.objects.create(
        title="Test LP", price=Decimal("100.00"), stock=10
    )


def _add(api_client, payload):
    return api_client.post(reverse("add-to-cart"), payload, format="json")


# ── /carts listing ────────────────────────────────────────────────────────

def test_carts_listing_returns_only_own_carts(api_client, db):
    alice = _verified_user("alice")
    bob = _verified_user("bob")
    mine = Cart.objects.create(user=alice)
    Cart.objects.create(user=bob)

    api_client.force_authenticate(user=alice)
    resp = api_client.get(reverse("get-all-carts"))

    assert resp.status_code == 200
    codes = [c["cart_code"] for c in resp.data]
    assert codes == [mine.cart_code]


# ── add_to_cart ownership ────────────────────────────────────────────────

def test_add_without_code_creates_owned_cart_per_user(api_client, record, db):
    """THE regression: two users, no cart_code → two separate carts."""
    alice = _verified_user("alice2")
    bob = _verified_user("bob2")
    client_alice = APIClient()
    client_bob = APIClient()
    client_alice.force_authenticate(user=alice)
    client_bob.force_authenticate(user=bob)

    resp_a = _add(client_alice, {"record_id": record.id})
    assert resp_a.status_code == 200
    code_a = resp_a.data["cart_code"]
    assert resp_a.data["user"] == alice.id

    resp_b = _add(client_bob, {"record_id": record.id})
    assert resp_b.status_code == 200
    code_b = resp_b.data["cart_code"]

    assert code_a != code_b


def test_add_claims_anonymous_cart_on_first_use(api_client, record, db):
    user = _verified_user("claimer")
    legacy = Cart.objects.create(user=None)  # pre-fix anonymous cart

    api_client.force_authenticate(user=user)
    resp = _add(
        api_client, {"cart_code": legacy.cart_code, "record_id": record.id}
    )
    assert resp.status_code == 200
    legacy.refresh_from_db()
    assert legacy.user_id == user.id


def test_add_rejects_foreign_cart(record, db):
    owner = _verified_user("owner")
    intruder = _verified_user("intruder")
    foreign = Cart.objects.create(user=owner)

    intruder_client = APIClient()
    intruder_client.force_authenticate(user=intruder)
    resp = _add(
        intruder_client,
        {"cart_code": foreign.cart_code, "record_id": record.id},
    )
    assert resp.status_code == 404


def test_get_cart_hides_foreign_cart(db):
    owner = _verified_user("ownercart")
    peeker = _verified_user("peeker")
    foreign = Cart.objects.create(user=owner)

    peeker_client = APIClient()
    peeker_client.force_authenticate(user=peeker)
    resp = peeker_client.get(reverse("get-cart", args=[foreign.cart_code]))
    assert resp.status_code == 404


# ── get_cart auto-creates on unknown code ────────────────────────────────

def test_get_cart_unknown_code_creates_empty_owned_cart(api_client, db):
    """A stale/unknown cart_code returns a fresh empty cart instead of 404."""
    user = _verified_user("freshcart")
    before = Cart.objects.count()

    api_client.force_authenticate(user=user)
    resp = api_client.get(reverse("get-cart", args=["no-such-cart-code"]))

    assert resp.status_code == 200
    assert resp.data["cart_code"] != "no-such-cart-code"
    assert resp.data["user"] == user.id
    assert resp.data["cart_items"] == []
    assert resp.data["total_price"] == 0
    assert Cart.objects.count() == before + 1
    assert Cart.objects.filter(
        user=user, cart_code=resp.data["cart_code"]
    ).exists()


def test_get_cart_foreign_cart_creates_nothing(db):
    """The isolation branch keeps 404-ing and never creates a cart."""
    owner = _verified_user("ownerpeek")
    peeker = _verified_user("peekerpeek")
    foreign = Cart.objects.create(user=owner)
    before = Cart.objects.count()

    peeker_client = APIClient()
    peeker_client.force_authenticate(user=peeker)
    resp = peeker_client.get(reverse("get-cart", args=[foreign.cart_code]))

    assert resp.status_code == 404
    assert Cart.objects.count() == before


def test_add_still_accepts_own_cart_code(api_client, record, db):
    user = _verified_user("returning")
    own = Cart.objects.create(user=user)

    api_client.force_authenticate(user=user)
    resp = _add(api_client, {"cart_code": own.cart_code, "record_id": record.id})
    assert resp.status_code == 200
    assert resp.data["cart_code"] == own.cart_code
