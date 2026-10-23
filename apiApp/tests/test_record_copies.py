"""The same release in several conditions (Record.variant_of): one catalog card
per group, every copy in the detail page's condition picker."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apiApp.models import Artist, Record


@pytest.fixture
def admin_client(db):
    admin = get_user_model().objects.create_user(
        username='copiesadmin', email='copiesadmin@example.com', password='Pass12345!', role='ADMIN'
    )
    client = APIClient()
    client.force_authenticate(admin)
    return client


def _record(condition, price, stock, variant_of=None, title='Kind of Blue'):
    return Record.objects.create(
        title=title, condition=condition, price=Decimal(price), stock=stock, variant_of=variant_of,
        artist=Artist.objects.get_or_create(name='Miles Davis')[0],
    )


@pytest.fixture
def group(db):
    """First record VG+ $300 sold out; NM $450 and G $150 copies in stock."""
    first = _record('VG+', '300.00', 0)
    near_mint = _record('NM', '450.00', 2, first)
    good = _record('G', '150.00', 1, first)
    return first, near_mint, good


def _results(client, url):
    return client.get(url).json()['results']


def test_catalog_shows_one_card_per_group_with_every_copy(group):
    first, near_mint, good = group
    other = _record('M', '500.00', 1, title='Abbey Road')
    rows = _results(APIClient(), '/records/')

    assert sorted(r['id'] for r in rows) == [good.id, other.id]  # in stock first, then cheapest
    card = next(r for r in rows if r['id'] == good.id)
    assert [c['condition'] for c in card['copies']] == ['NM', 'VG+', 'G']  # best condition first
    assert [c['stock'] for c in card['copies']] == [2, 0, 1]
    assert card['copies'][0]['sell_price'] == '450.00'
    assert next(r for r in rows if r['id'] == other.id)['copies'] == []


def test_filters_pick_the_copy_that_matched(group):
    first, near_mint, good = group
    client = APIClient()
    assert [r['id'] for r in _results(client, '/records/?condition=NM')] == [near_mint.id]
    assert [r['id'] for r in _results(client, '/records/?price_min=200')] == [near_mint.id]
    # Sold-out first record: the group still shows (as a copy in stock).
    assert [r['id'] for r in _results(client, '/records/?available=true')] == [good.id]


def test_search_collapses_and_all_copies_lists_each_one(group):
    client = APIClient()
    assert len(_results(client, '/search/?query=kind')) == 1
    assert len(_results(client, '/search/?query=kind&all_copies=true')) == 3
    assert len(_results(client, '/records/?all_copies=true')) == 3


def test_detail_lists_the_group_from_any_copy(group):
    first, near_mint, good = group
    for record in group:
        copies = APIClient().get(f'/records/{record.slug}/').json()['copies']
        assert [c['id'] for c in copies] == [near_mint.id, first.id, good.id]
    alone = _record('M', '100.00', 1, title='Abbey Road')
    assert APIClient().get(f'/records/{alone.slug}/').json()['copies'] == []


def test_new_copy_links_to_the_groups_first_record(admin_client, group):
    first, near_mint, _ = group
    resp = admin_client.post('/records/create/', {
        'title': 'Kind of Blue', 'condition': 'F', 'price': '90.00', 'stock': 1, 'variant_of': near_mint.id,
    }, format='json')
    assert resp.status_code == 201
    assert resp.json()['variant_of'] == first.id
    assert len(resp.json()['copies']) == 4


def test_copy_links_that_would_nest_are_rejected(admin_client, group):
    first, near_mint, _ = group
    # The group's first record can't hang from one of its own copies.
    assert admin_client.patch(f'/records/{first.id}/update/', {'variant_of': near_mint.id},
                              format='json').status_code == 400
    other = _record('M', '100.00', 1, title='Abbey Road')
    resp = admin_client.patch(f'/records/{first.id}/update/', {'variant_of': other.id}, format='json')
    assert resp.status_code == 400
    assert 'variant_of' in resp.json()
    # Unlinking a copy is fine.
    assert admin_client.patch(f'/records/{near_mint.id}/update/', {'variant_of': None},
                              format='json').json()['variant_of'] is None


def test_deleting_the_first_record_keeps_the_rest_grouped(admin_client, group):
    first, near_mint, good = group
    assert admin_client.delete(f'/records/{first.id}/delete/').status_code == 200
    near_mint.refresh_from_db()
    good.refresh_from_db()
    assert near_mint.variant_of_id is None
    assert good.variant_of_id == near_mint.id
    assert len(_results(APIClient(), '/records/')) == 1
