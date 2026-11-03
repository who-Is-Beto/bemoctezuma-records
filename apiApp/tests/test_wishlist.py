"""Wishlist ("Favoritos"): each user saves records to their own list and
can never read or change anyone else's."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apiApp.models import Artist, Record, Wishlist, WishlistItem


def _client(username):
    user = get_user_model().objects.create_user(
        username=username, email=f'{username}@example.com', password='Pass12345!'
    )
    client = APIClient()
    client.force_authenticate(user)
    client.user = user
    return client


@pytest.fixture
def ana(db):
    return _client('ana')


@pytest.fixture
def beto(db):
    return _client('beto')


def _record(title='Kind of Blue', stock=2):
    return Record.objects.create(
        title=title, price=Decimal('300.00'), stock=stock,
        artist=Artist.objects.get_or_create(name='Miles Davis')[0],
    )


def _save(client, record):
    return client.post('/wishlist/items/', {'record_id': record.id}, format='json')


def test_save_lists_and_ids(ana):
    kind, abbey = _record(), _record('Abbey Road')
    assert _save(ana, kind).status_code == 201
    assert _save(ana, abbey).status_code == 201

    data = ana.get('/wishlist/').json()
    assert data['count'] == 2
    # Newest first, with the record ready for a card.
    assert [i['record']['title'] for i in data['items']] == ['Abbey Road', 'Kind of Blue']
    assert data['items'][0]['record']['artist']['name'] == 'Miles Davis'
    assert sorted(ana.get('/wishlist/ids/').json()['record_ids']) == sorted([kind.id, abbey.id])


def test_saving_twice_keeps_one(ana):
    kind = _record()
    _save(ana, kind)
    assert _save(ana, kind).status_code == 200
    assert WishlistItem.objects.count() == 1
    assert Wishlist.objects.filter(user=ana.user).count() == 1


def test_remove_is_idempotent(ana):
    kind = _record()
    _save(ana, kind)
    assert ana.delete(f'/wishlist/items/{kind.id}/').status_code == 204
    assert ana.delete(f'/wishlist/items/{kind.id}/').status_code == 204
    assert ana.get('/wishlist/').json() == {'count': 0, 'items': []}


def test_empty_before_the_first_save(ana):
    assert ana.get('/wishlist/').json() == {'count': 0, 'items': []}
    assert ana.get('/wishlist/ids/').json() == {'record_ids': []}
    assert not Wishlist.objects.exists()  # created on the first save only


def test_sold_out_records_stay_saved(ana):
    record = _record(stock=0)
    _save(ana, record)
    assert ana.get('/wishlist/').json()['items'][0]['record']['stock'] == 0


def test_lists_are_private(ana, beto):
    kind = _record()
    _save(ana, kind)
    assert beto.get('/wishlist/').json()['count'] == 0
    assert beto.get('/wishlist/ids/').json()['record_ids'] == []
    # Beto removing "his" copy never touches Ana's list.
    beto.delete(f'/wishlist/items/{kind.id}/')
    assert ana.get('/wishlist/').json()['count'] == 1


def test_old_endpoints_that_leaked_every_list_are_gone(ana):
    assert ana.get('/wishlists/').status_code == 404


def test_input_and_auth_are_validated(ana, api_client):
    assert ana.post('/wishlist/items/', {}, format='json').status_code == 400
    assert ana.post('/wishlist/items/', {'record_id': 'abc'}, format='json').status_code == 400
    assert ana.post('/wishlist/items/', {'record_id': 999999}, format='json').status_code == 404
    for method, url in [('get', '/wishlist/'), ('get', '/wishlist/ids/'), ('post', '/wishlist/items/'), ('delete', '/wishlist/items/1/')]:
        assert getattr(api_client, method)(url).status_code == 401


def test_deleting_a_record_removes_it_from_lists(ana):
    kind = _record()
    _save(ana, kind)
    kind.delete()
    assert ana.get('/wishlist/').json()['count'] == 0
