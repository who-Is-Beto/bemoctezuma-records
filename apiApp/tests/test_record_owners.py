"""Several owners per record: stock split per owner, Punto de venta picking
whose copy is sold, online orders taking the oldest owner first, and the
add form's "this record already exists" lookup."""
import threading
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from rest_framework.test import APIClient

from apiApp.models import Artist, Cart, CartItem, Order, Owner, Record, RecordOwner, SaleItem
from apiApp.services import fulfill_checkout, take_stock


@pytest.fixture
def admin(db):
    user = get_user_model().objects.create_user(
        username='ownersadmin', email='ownersadmin@example.com', password='Pass12345!', role='ADMIN',
    )
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def ana(db):
    return Owner.objects.create(name='Ana', email='ana@example.com')


@pytest.fixture
def beto(db):
    return Owner.objects.create(name='Beto', email='beto@example.com')


def _record(*owner_quantities, title='Kid A', artist='Radiohead', store=0):
    """A record whose stock is ``store`` units plus each (owner, quantity), in FIFO order."""
    record = Record.objects.create(
        title=title, price=Decimal('900.00'), stock=store + sum(q for _, q in owner_quantities),
        artist=Artist.objects.get_or_create(name=artist)[0],
    )
    for owner, quantity in owner_quantities:
        RecordOwner.objects.create(record=record, owner=owner, quantity=quantity)
    return record


def _stock(record):
    record.refresh_from_db()
    return record.stock, sorted((r.owner.name, r.quantity) for r in record.owner_stock.select_related('owner'))


# ── take_stock ───────────────────────────────────────────────────────────


def test_take_stock_is_fifo_across_owners(ana, beto):
    record = _record((ana, 1), (beto, 2))
    with transaction.atomic():
        assert take_stock(record.pk, 2) == [(ana.pk, 1), (beto.pk, 1)]
    assert _stock(record) == (1, [('Ana', 0), ('Beto', 1)])


def test_take_stock_from_one_owner_or_nothing(ana, beto):
    record = _record((ana, 1), (beto, 2))
    with transaction.atomic():
        assert take_stock(record.pk, 2, owner_id=ana.pk) is None
        assert take_stock(record.pk, 4) is None
    assert _stock(record) == (3, [('Ana', 1), ('Beto', 2)])
    with transaction.atomic():
        assert take_stock(record.pk, 2, owner_id=beto.pk) == [(beto.pk, 2)]
    assert _stock(record) == (1, [('Ana', 1), ('Beto', 0)])


def test_store_stock_has_no_owner(db):
    record = _record(store=2)
    with transaction.atomic():
        assert take_stock(record.pk, 2) == [(None, 2)]
    assert _stock(record) == (0, [])


@pytest.mark.django_db(transaction=True)
def test_two_buyers_of_the_last_copy_only_one_wins():
    if connection.vendor != 'postgresql':
        pytest.skip('row locks need Postgres (SQLite serializes writers anyway)')
    ana = Owner.objects.create(name='Ana', email='ana@example.com')
    record = _record((ana, 1))
    barrier, results = threading.Barrier(2), []

    def buy():
        try:
            barrier.wait()
            with transaction.atomic():
                results.append(take_stock(record.pk, 1))
        finally:
            connection.close()

    threads = [threading.Thread(target=buy) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=bool) == [None, [(ana.pk, 1)]]
    assert _stock(record) == (0, [('Ana', 0)])


# ── Punto de venta ───────────────────────────────────────────────────────


def _sell(client, *lines):
    return client.post('/sales/create/', {'items': list(lines), 'payment_method': 'cash'}, format='json')


def test_pos_must_say_whose_copy_when_several_owners(admin, ana, beto):
    record = _record((ana, 1), (beto, 2))
    resp = _sell(admin, {'record': record.id, 'quantity': 1})
    assert resp.status_code == 400 and 'varios dueños' in str(resp.json())

    assert _sell(admin, {'record': record.id, 'quantity': 1, 'owner': beto.id}).status_code == 201
    assert _stock(record) == (2, [('Ana', 1), ('Beto', 1)])
    assert SaleItem.objects.get().owner == beto


def test_pos_ticket_splits_one_record_between_owners(admin, ana, beto):
    """The ticket sends one line per owner when the admin sells 3 = Ana 1 + Beto 2."""
    record = _record((ana, 2), (beto, 2))
    resp = _sell(admin, {'record': record.id, 'quantity': 1, 'owner': ana.id},
                 {'record': record.id, 'quantity': 2, 'owner': beto.id})
    assert resp.status_code == 201
    assert sorted((i['owner']['name'], i['quantity']) for i in resp.json()['sale']['items']) == [('Ana', 1), ('Beto', 2)]
    assert _stock(record) == (1, [('Ana', 1), ('Beto', 0)])


def test_pos_rejects_an_owner_without_that_record_or_stock(admin, ana, beto):
    record = _record((ana, 1), (beto, 1))
    other = Owner.objects.create(name='Caro', email='caro@example.com')
    assert 'Caro' in str(_sell(admin, {'record': record.id, 'quantity': 1, 'owner': other.id}).json())
    resp = _sell(admin, {'record': record.id, 'quantity': 2, 'owner': ana.id})
    assert resp.json()['error']['code'] == 'insufficient_stock'
    assert 'de Ana (disponible: 1)' in resp.json()['error']['message']
    assert _stock(record) == (2, [('Ana', 1), ('Beto', 1)])


def test_pos_single_owner_or_store_stock_needs_no_choice(admin, ana):
    mine, store = _record((ana, 2)), _record(title='Amnesiac', store=1)
    assert _sell(admin, {'record': mine.id, 'quantity': 1}, {'record': store.id, 'quantity': 1}).status_code == 201
    assert {(i.record_id, i.owner_id) for i in SaleItem.objects.all()} == {(mine.id, ana.id), (store.id, None)}


# ── Online orders ────────────────────────────────────────────────────────


def test_online_order_takes_oldest_owner_first_and_splits_lines(ana, beto):
    record = _record((ana, 1), (beto, 2))
    cart = Cart.objects.create()
    CartItem.objects.create(cart=cart, record=record, quantity=2)
    session = {'id': 'cs_owners', 'amount_total': 180000, 'currency': 'mxn',
               'customer_email': 'buyer@example.com', 'metadata': {'shipped_to': 'store'}}
    with mock.patch('stripe.checkout.Session.list_line_items', side_effect=RuntimeError('offline tests')):
        fulfill_checkout(session, cart.cart_code)

    items = Order.objects.get().order_items.order_by('id')
    assert [(i.owner_id, i.quantity, i.price) for i in items] == [
        (ana.id, 1, Decimal('900.00')), (beto.id, 1, Decimal('900.00')),
    ]
    assert _stock(record) == (1, [('Ana', 0), ('Beto', 1)])


# ── "Ya existe" lookup on the add form ───────────────────────────────────


def test_matches_ignore_case_accents_and_punctuation(admin, ana):
    aztlan = _record((ana, 1), title='Aztlán', artist='Zoé')
    other_artist = _record(title='Aztlán', artist='Otro Artista')
    remixes = _record(title='Aztlán Remixes', artist='Zoé')
    _record(title='Reptilectric', artist='Zoé')

    # Discogs names come as 'Zoé (2)'; exact + same artist first, then the
    # similar title by the same artist, then the exact title by someone else.
    found = admin.get('/records/matches/', {'title': 'aztlan', 'artist': 'ZOE (2)'}).json()
    assert [r['id'] for r in found] == [aztlan.id, remixes.id, other_artist.id]
    assert found[0]['owners'] == [{'owner': ana.id, 'owner_name': 'Ana', 'quantity': 1}]
    assert admin.get('/records/matches/', {'title': ''}).json() == []


def test_matches_survive_odd_slugs_and_edition_text(admin):
    # Slugs typed by hand / never updated on rename used to hide records.
    record = _record(title='Sarajevo', artist='Los Bunkers')
    Record.objects.filter(pk=record.pk).update(slug='serajevo')
    assert [r['id'] for r in admin.get('/records/matches/', {'title': 'Sarajevo (Remastered)'}).json()] == [record.id]
    # Short titles still match exactly, but don't match by containment.
    am = _record(title='AM', artist='Arctic Monkeys')
    assert [r['id'] for r in admin.get('/records/matches/', {'title': 'am'}).json()] == [am.id]
    assert admin.get('/records/matches/', {'title': 'Sa'}).json() == []


def test_matches_need_permission(api_client, user):
    assert api_client.get('/records/matches/', {'title': 'x'}).status_code == 401
    api_client.force_authenticate(user)
    assert api_client.get('/records/matches/', {'title': 'x'}).status_code == 403
