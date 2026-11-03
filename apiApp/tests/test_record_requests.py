"""Encargar un disco: the form emails the store, Reply-To the visitor."""
from unittest import mock

import pytest
from django.core import mail

URL = '/record-requests/'
VALID = {
    'name': 'Ana', 'email': 'ana@example.com', 'artist': 'Los Ángeles Azules',
    'title': 'Inolvidable', 'format': 'LP', 'details': '1ra edición',
    'message': 'Lo busco para regalo.',
}


@pytest.mark.django_db
def test_request_is_emailed_to_the_store(api_client, settings):
    settings.SELLER_NOTIFY_EMAILS = ['tienda@example.com']
    response = api_client.post(URL, VALID, format='json')
    assert response.status_code == 201
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ['tienda@example.com']
    assert sent.reply_to == ['ana@example.com']  # answering reaches the visitor
    assert 'Inolvidable' in sent.subject
    html = sent.alternatives[0][0]
    assert '1ra edición' in html and 'Lo busco para regalo.' in html


@pytest.mark.django_db
def test_only_name_email_artist_and_title_are_required(api_client):
    minimal = {k: VALID[k] for k in ('name', 'email', 'artist', 'title')}
    assert api_client.post(URL, minimal, format='json').status_code == 201

    response = api_client.post(URL, {'name': 'Ana', 'email': 'nope'}, format='json')
    assert response.status_code == 400
    assert set(response.json()['error']['details']) == {'email', 'artist', 'title'}
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_honeypot_pretends_success_without_sending(api_client):
    response = api_client.post(URL, {**VALID, 'website': 'http://spam'}, format='json')
    assert response.status_code == 201
    assert mail.outbox == []


@pytest.mark.django_db
def test_email_failure_returns_503(api_client):
    with mock.patch('apiApp.services.emailing.send_email', side_effect=RuntimeError('down')):
        response = api_client.post(URL, VALID, format='json')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'email_failed'


@pytest.mark.django_db
def test_requests_are_throttled(api_client, settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        'DEFAULT_THROTTLE_RATES': {**settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'], 'record_request': '2/hour'},
    }
    codes = [api_client.post(URL, VALID, format='json').status_code for _ in range(3)]
    assert codes == [201, 201, 429]


# ── Discogs lookup ───────────────────────────────────────────────────────

DISCOGS_URL = '/record-requests/discogs/'


def _discogs_response(results):
    response = mock.Mock()
    response.json.return_value = {'results': results}
    response.raise_for_status.return_value = None
    return response


MASTER = {'id': 1, 'title': 'Miles Davis - Kind Of Blue', 'year': '1959', 'uri': '/master/1-Kind-Of-Blue',
          'thumb': 'https://i.discogs.com/t.jpg', 'cover_image': 'https://i.discogs.com/c.jpg'}
PRESSING = {'id': 10, 'master_id': 1, 'title': 'Miles Davis - Kind Of Blue', 'uri': '/release/10'}
NO_MASTER = {'id': 20, 'master_id': 0, 'title': 'Los Rayos - Cumbia Rayada', 'uri': '/release/20',
             'thumb': 'https://st.discogs.com/spacer.gif'}
NO_MASTER_AGAIN = {**NO_MASTER, 'id': 21}


@pytest.mark.django_db
def test_discogs_lookup_lists_one_result_per_album(api_client):
    def fake_get(url, params, **kwargs):
        return _discogs_response([MASTER] if params['type'] == 'master' else [PRESSING, NO_MASTER, NO_MASTER_AGAIN])

    with mock.patch('apiApp.services.discogs.requests.get', side_effect=fake_get) as get:
        data = api_client.get(DISCOGS_URL, {'q': 'kind of blue'}).json()
        assert get.call_count == 2
        # Same query again (any casing/spacing): served from the cache.
        api_client.get(DISCOGS_URL, {'q': '  Kind  of BLUE '})
        assert get.call_count == 2

    results = data['results']
    assert [(r['kind'], r['artist'], r['title']) for r in results] == [
        ('master', 'Miles Davis', 'Kind Of Blue'),
        ('release', 'Los Rayos', 'Cumbia Rayada'),  # no master, listed once
    ]
    assert results[0]['url'] == 'https://www.discogs.com/master/1-Kind-Of-Blue'
    assert results[0]['year'] == '1959'
    assert results[1]['thumb'] is None  # spacer.gif means no picture


@pytest.mark.django_db
def test_discogs_lookup_ignores_short_queries_and_reports_outages(api_client):
    with mock.patch('apiApp.services.discogs.requests.get') as get:
        assert api_client.get(DISCOGS_URL, {'q': 'ab'}).json() == {'results': []}
        get.assert_not_called()

    import requests
    with mock.patch('apiApp.services.discogs.requests.get', side_effect=requests.ConnectionError):
        assert api_client.get(DISCOGS_URL, {'q': 'kind of blue'}).status_code == 502


@pytest.mark.django_db
def test_picked_discogs_record_is_in_the_email(api_client):
    payload = {**VALID, 'year': 1959, 'discogs_url': 'https://www.discogs.com/master/1',
               'cover_url': 'https://i.discogs.com/c.jpg'}
    assert api_client.post(URL, payload, format='json').status_code == 201
    html = mail.outbox[0].alternatives[0][0]
    assert 'https://www.discogs.com/master/1' in html and 'https://i.discogs.com/c.jpg' in html


@pytest.mark.django_db
def test_only_discogs_links_are_accepted(api_client):
    response = api_client.post(URL, {**VALID, 'discogs_url': 'https://evil.example/x',
                                     'cover_url': 'http://i.discogs.com/c.jpg'}, format='json')
    assert response.status_code == 400
    assert set(response.json()['error']['details']) == {'discogs_url', 'cover_url'}
    assert mail.outbox == []


# ── Formats of the picked album ──────────────────────────────────────────

from apiApp.services.discogs import normalize_formats  # noqa: E402


@pytest.mark.parametrize('name, descriptions, expected', [
    ('Vinyl', ['LP', 'Album'], {'LP'}),
    ('Vinyl', ['12"', 'LP', 'Album'], {'LP'}),          # an LP is already 12"
    ('Vinyl', ['7"', '45 RPM', 'Single'], {'7"'}),
    ('Vinyl', ['12"', '33 ⅓ RPM', 'Maxi-Single'], {'12"'}),
    ('2×Vinyl', ['LP', 'Album'], {'LP'}),
    ('Vinyl', ['Album'], {'Vinilo'}),                   # size unknown
    ('CD', ['Album'], {'CD'}),
    ('Cassette', ['Album'], {'Casete'}),
    ('File', ['MP3'], set()),                           # not a record
    ('DVD', ['NTSC'], set()),
])
def test_discogs_formats_are_normalized(name, descriptions, expected):
    assert normalize_formats(name, descriptions) == expected


@pytest.mark.django_db
def test_master_formats_come_from_all_its_pressings(api_client):
    versions = {'versions': [
        {'major_formats': ['Vinyl'], 'format': 'LP, Album'},
        {'major_formats': ['CD'], 'format': 'Album, Reissue'},
        {'major_formats': ['Vinyl'], 'format': '7", Single'},
        {'major_formats': ['File'], 'format': 'MP3'},
        {'major_formats': ['Cassette'], 'format': 'Album'},
    ]}
    with mock.patch('apiApp.services.discogs.requests.get', return_value=_discogs_json(versions)) as get:
        data = api_client.get('/record-requests/discogs/master/1/formats/').json()
        api_client.get('/record-requests/discogs/master/1/formats/')
        assert get.call_count == 1  # cached
    assert data == {'formats': ['LP', '7"', 'CD', 'Casete']}


@pytest.mark.django_db
def test_release_formats_and_unknown_kind(api_client):
    release = {'formats': [{'name': 'Vinyl', 'descriptions': ['10"', 'EP']}]}
    with mock.patch('apiApp.services.discogs.requests.get', return_value=_discogs_json(release)):
        assert api_client.get('/record-requests/discogs/release/5/formats/').json() == {'formats': ['10"']}
    assert api_client.get('/record-requests/discogs/label/5/formats/').status_code == 404


def _discogs_json(payload):
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response
