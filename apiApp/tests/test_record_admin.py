"""Record privacy (public vs admin JSON) and the edit form's full-record load."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apiApp.models import Cart, CartItem, Owner, Record, RecordOwner

PRIVATE = ('cost_price', 'final_sale_price', 'owners')


@pytest.fixture
def record(db):
    record = Record.objects.create(
        title='Abbey Road', price=Decimal('350.00'), cost_price=Decimal('120.00'),
        final_sale_price=Decimal('300.00'), stock=2, description='Remaster 2019',
        release_date=1969, featured=False, items_inside=2, weight_grams=280,
    )
    RecordOwner.objects.create(record=record, owner=Owner.objects.create(name='Ana', email='ana@example.com'), quantity=2)
    return record


@pytest.fixture
def admin_client(db):
    admin = get_user_model().objects.create_user(
        username='recadmin', email='recadmin@example.com', password='Pass12345!', role='ADMIN',
    )
    client = APIClient()
    client.force_authenticate(admin)
    return client


def test_public_record_json_hides_private_fields(api_client, user, record):
    record.featured = True
    record.save()
    user.email_verified = True
    user.save()
    customer = APIClient()
    customer.force_authenticate(user)
    cart = Cart.objects.create(user=user)
    CartItem.objects.create(cart=cart, record=record, quantity=1)
    public = [
        api_client.get('/records/').json()['results'][0],
        api_client.get(f'/records/{record.slug}/').json(),
        api_client.get('/search/', {'query': 'abbey'}).json()['results'][0],
        customer.get(f'/cart/{cart.cart_code}/').json()['cart_items'][0]['record'],
    ]
    for payload in public:
        assert payload['title'] == 'Abbey Road'
        assert not set(PRIVATE) & set(payload), payload


def test_edit_form_gets_the_full_record(admin_client, record):
    """List rows lack these fields; the edit form must load them from here."""
    data = admin_client.get(f'/records/{record.id}/update/').json()
    assert data['description'] == 'Remaster 2019'
    assert (data['release_date'], data['featured'], data['items_inside'], data['weight_grams']) == (1969, False, 2, 280)
    assert (data['cost_price'], data['final_sale_price']) == ('120.00', '300.00')
    assert [(o['owner_name'], o['quantity']) for o in data['owners']] == [('Ana', 2)]
    assert data['artist'] is None or 'name' in data['artist']  # nested like the public detail


def test_admin_write_responses_include_private_fields(admin_client, record):
    resp = admin_client.patch(f'/records/{record.id}/update/', {'stock': 5}, format='json')
    assert resp.status_code == 200
    assert set(PRIVATE) <= set(resp.json())


def test_edit_endpoint_needs_permission(api_client, user, record):
    assert api_client.get(f'/records/{record.id}/update/').status_code == 401
    api_client.force_authenticate(user)
    assert api_client.get(f'/records/{record.id}/update/').status_code == 403
