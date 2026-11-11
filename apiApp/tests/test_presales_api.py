from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apiApp.models import Category
from apiApp.services import presales as svc
from apiApp.tests.presale_factories import make_option, make_presale

QUOTE = [{'courier': 'Estafeta', 'serviceType': 'Terrestre', 'title': 'Estafeta Terrestre',
          'total': '150.00', 'available': True}]


@pytest.mark.django_db
def test_list_hides_drafts(api_client):
    make_option(make_presale(title='Publicada'))
    make_option(make_presale(title='Borrador', status='draft'))
    res = api_client.get('/presales/')
    assert res.status_code == 200
    assert [p['title'] for p in res.json()] == ['Publicada']


@pytest.mark.django_db
def test_list_never_exposes_cost_or_owner(api_client):
    make_option(cost_price=Decimal('400'))
    body = str(api_client.get('/presales/').json())
    assert 'cost_price' not in body and 'owner' not in body


@pytest.mark.django_db
def test_list_item_shape(api_client):
    presale = make_presale()
    make_option(presale, name='Rojo', price=Decimal('800'))
    make_option(presale, name='CD', price=Decimal('350'), min_deposit=Decimal('100'),
                full_payment_discount=Decimal('0'), position=1)
    item = api_client.get('/presales/').json()[0]
    assert item['price_from'] == '350.00'
    assert item['max_discount'] == '50.00'
    assert item['is_open'] is True
    assert [o['name'] for o in item['options']] == ['Rojo', 'CD']
    assert item['options'][0]['copies_left'] == 10


@pytest.mark.django_db
def test_copies_left_counts_holds(api_client, user):
    option = make_option(copies=2)
    svc.create_order(option, buyer=user, source='online', delivery='pickup', shipping_details=None,
                     amount=Decimal('200'), accept_terms=True)
    item = api_client.get(f'/presales/{option.presale.slug}/').json()
    assert item['options'][0]['copies_left'] == 1


@pytest.mark.django_db
def test_detail_404_for_draft_when_anonymous(api_client):
    presale = make_presale(status='draft')
    assert api_client.get(f'/presales/{presale.slug}/').status_code == 404


@pytest.mark.django_db
def test_detail_draft_visible_to_admin(api_client):
    presale = make_presale(status='draft')
    admin = get_user_model().objects.create_user(username='boss', email='b@x.com', password='x', role='ADMIN')
    api_client.force_authenticate(admin)
    assert api_client.get(f'/presales/{presale.slug}/').status_code == 200


@pytest.mark.django_db
def test_cover_is_absolute_url(api_client):
    presale = make_presale(cover=SimpleUploadedFile('c.jpg', b'x', content_type='image/jpeg'))
    make_option(presale)
    assert api_client.get('/presales/').json()[0]['cover'].startswith('http://testserver/')


@pytest.mark.django_db
def test_shipping_estimate_uses_option_weight(api_client):
    cd = Category.objects.create(name='CD')
    option = make_option(category=cd)
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=QUOTE) as quotes:
        res = api_client.post('/presales/shipping-estimate/', {'option_id': option.id, 'zip': '64000'}, format='json')
    assert res.status_code == 200
    assert res.json()['shipping_estimate'] == '150.00'
    zip_code, package = quotes.call_args.args
    assert zip_code == '64000'
    assert package['weight'] == 1  # 300 g tare + 85 g CD → minimum package weight (1 kg)


@pytest.mark.django_db
def test_shipping_estimate_rejects_bad_zip(api_client):
    option = make_option()
    res = api_client.post('/presales/shipping-estimate/', {'option_id': option.id, 'zip': 'abc'}, format='json')
    assert res.status_code == 400
