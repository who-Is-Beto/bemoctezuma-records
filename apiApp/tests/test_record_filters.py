"""Catalog filters shared by /records/ and /search/ (genere, artist, condition,
price range, ordering) + the query count that the FK joins keep flat."""
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apiApp.models import Artist, Genere, Record


@pytest.fixture
def catalog(db):
    floyd = Artist.objects.create(name='Pink Floyd')
    zoe = Artist.objects.create(name='Zoé')
    rock = Genere.objects.create(name='Rock')
    pop = Genere.objects.create(name='Pop')
    mk = lambda **kw: Record.objects.create(stock=1, featured=True, **kw)  # noqa: E731
    return {
        'dark': mk(title='Dark Side', price=Decimal('500'), artist=floyd, genere=rock, condition='NM'),
        'wall': mk(title='The Wall', price=Decimal('800'), discount_porcentage=50, artist=floyd, genere=rock, condition='VG'),
        'metro': mk(title='Metrópolis', price=Decimal('300'), artist=zoe, genere=pop, condition='M'),
    }


def _ids(client, params, name='records-list'):
    resp = client.get(reverse(name), params)
    assert resp.status_code == 200
    return [r['id'] for r in resp.data['results']]


def test_artist_and_genere_filters(api_client, catalog):
    assert set(_ids(api_client, {'artist': 'pink-floyd'})) == {catalog['dark'].id, catalog['wall'].id}
    assert _ids(api_client, {'genere': 'pop'}) == [catalog['metro'].id]


def test_artist_page_includes_non_featured(api_client, catalog):
    catalog['wall'].featured = False
    catalog['wall'].save()
    assert catalog['wall'].id not in _ids(api_client, {})
    assert catalog['wall'].id in _ids(api_client, {'artist': 'pink-floyd'})


def test_condition_list_ignores_unknown_codes(api_client, catalog):
    assert set(_ids(api_client, {'condition': 'M,VG,bogus'})) == {catalog['metro'].id, catalog['wall'].id}


def test_price_range_uses_discounted_price(api_client, catalog):
    # The Wall: 800 at -50% = 400 -> inside 350..450; Dark Side 500 is out.
    assert _ids(api_client, {'price_min': '350', 'price_max': '450'}) == [catalog['wall'].id]
    # Garbage is ignored, not a 400 (shared URLs must not break).
    assert len(_ids(api_client, {'price_min': 'abc'})) == 3


def test_ordering_by_price(api_client, catalog):
    assert _ids(api_client, {'ordering': 'price_asc'}) == [catalog['metro'].id, catalog['wall'].id, catalog['dark'].id]
    assert _ids(api_client, {'ordering': 'price_desc'})[0] == catalog['dark'].id


def test_search_applies_same_filters_and_page_size(api_client, catalog):
    assert _ids(api_client, {'query': 'floyd', 'condition': 'NM'}, 'record-search') == [catalog['dark'].id]
    # Suggestions ask for page_size=5: the LIMIT is in SQL, not client slicing.
    assert len(_ids(api_client, {'query': 'floyd', 'page_size': 1}, 'record-search')) == 1


def _query_count(client):
    with CaptureQueriesContext(connection) as ctx:
        client.get(reverse('records-list'))
    return len(ctx.captured_queries)


def test_list_query_count_does_not_grow_per_row(api_client, catalog):
    _query_count(api_client)  # warm the SiteConfig cache (middleware)
    before = _query_count(api_client)
    artist = Artist.objects.create(name='Otro')
    genere = Genere.objects.create(name='Jazz')
    for i in range(5):
        Record.objects.create(title=f'Extra {i}', price=Decimal('100'), stock=1, artist=artist, genere=genere)
    # artist/genere/category are joined: more rows, same number of queries.
    assert _query_count(api_client) == before
