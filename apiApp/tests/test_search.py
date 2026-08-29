"""Main catalog search: tokenized, accent-insensitive, cross-field."""
import pytest
from decimal import Decimal
from django.urls import reverse

from apiApp.models import Artist, Category, Genere, Record


@pytest.fixture
def catalog(db):
    """Small fixture catalog covering the searchable fields."""
    pink = Artist.objects.create(name='Pink Floyd')
    zoe = Artist.objects.create(name='Zoé')
    rock = Genere.objects.create(name='Rock')
    lp = Category.objects.create(name='LP')
    cd = Category.objects.create(name='CD')

    dark_side = Record.objects.create(
        title='The Dark Side of the Moon', price=Decimal('500.00'), stock=2,
        artist=pink, genere=rock, category=lp,
    )
    mtw = Record.objects.create(
        title='Metrópolis', price=Decimal('300.00'), stock=1,
        artist=zoe, genere=rock, category=cd,
    )
    return {
        'records': [dark_side, mtw],
        'artist': pink,
        'genere': rock,
        'category': lp,
    }


def _search(client, term):
    resp = client.get(reverse('record-search'), {'query': term})
    assert resp.status_code == 200
    return [r['id'] for r in resp.data['results']]


def test_search_requires_query(api_client, db):
    assert api_client.get(reverse('record-search')).status_code == 400


def test_search_no_matches_is_empty_page(api_client, catalog):
    """A query with no matches is 200 + an empty paginated envelope, not 404."""
    resp = api_client.get(reverse('record-search'), {'query': 'zeppelin'})
    assert resp.status_code == 200
    assert {'count', 'next', 'previous', 'results'} <= set(resp.data)
    assert resp.data['count'] == 0
    assert resp.data['results'] == []


def test_search_returns_paginated_envelope(api_client, catalog):
    """Search responds in the same shape as /records/ (count/results/list...)."""
    resp = api_client.get(reverse('record-search'), {'query': 'rock'})
    assert resp.status_code == 200
    assert resp.data['count'] == 2
    assert 'next' in resp.data and 'previous' in resp.data
    ids = [r['id'] for r in resp.data['results']]
    assert catalog['records'][0].id in ids
    assert catalog['records'][1].id in ids


def test_single_word_matches_title(api_client, catalog):
    ids = _search(api_client, 'metropolis')  # accent-stripped
    assert catalog['records'][1].id in ids


def test_multiword_any_order_same_field(api_client, catalog):
    # Words reversed must still match the artist slug.
    ids = _search(api_client, 'floyd pink')
    assert catalog['records'][0].id in ids


def test_multiword_across_fields(api_client, catalog):
    # Artist word + title word in one query — impossible for the old
    # single-joined-token matcher.
    ids = _search(api_client, 'pink dark side moon')
    assert catalog['records'][0].id in ids


def test_all_tokens_must_match(api_client, catalog):
    # AND semantics: one unrelated word → no results.
    ids = _search(api_client, 'pink zeppelin')
    assert ids == []


def test_partial_token_matches(api_client, catalog):
    ids = _search(api_client, 'floy')
    assert catalog['records'][0].id in ids


def test_matches_genere_and_category(api_client, catalog):
    ids_rock = _search(api_client, 'rock')
    assert catalog['records'][0].id in ids_rock
    assert catalog['records'][1].id in ids_rock
    ids_cd = _search(api_client, 'cd')
    assert catalog['records'][1].id in ids_cd


def test_artist_autocomplete_multiword(api_client, catalog):
    resp = api_client.get(reverse('artist-search'), {'q': 'floyd pin'})
    assert resp.status_code == 200
    names = [a['name'] for a in resp.data]
    assert 'Pink Floyd' in names
