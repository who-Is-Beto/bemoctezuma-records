"""Where a Punto de venta sale happened (store, bazar, other): saved on the
ticket, shown in the Ventas history, broken down in Métricas and the CSV."""
import csv
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apiApp.models import Artist, Record, Sale


@pytest.fixture
def client(db):
    admin = get_user_model().objects.create_user(
        username='channeladmin', email='channeladmin@example.com', password='Pass12345!', role='ADMIN'
    )
    client = APIClient()
    client.force_authenticate(admin)
    return client


def _sell(client, **extra):
    record = Record.objects.create(
        title='Kind of Blue', price=Decimal('100.00'), stock=5,
        artist=Artist.objects.get_or_create(name='Miles Davis')[0],
    )
    body = {'items': [{'record': record.id, 'quantity': 1}], 'payment_method': 'cash', **extra}
    return client.post('/sales/create/', body, format='json')


def test_sale_saves_its_channel_and_note(client):
    resp = _sell(client, channel='other', channel_note='  Instagram  ')
    assert resp.status_code == 201
    assert (resp.json()['sale']['channel'], resp.json()['sale']['channel_note']) == ('other', 'Instagram')
    sale = Sale.objects.get()
    assert (sale.channel, sale.channel_note) == ('other', 'Instagram')


def test_channel_defaults_to_the_store_and_is_validated(client):
    assert _sell(client).json()['sale']['channel'] == 'store'
    resp = _sell(client, channel='tianguis')
    assert resp.status_code == 400
    assert _sell(client, channel='other', channel_note='x' * 101).status_code == 400


def test_history_shows_the_channel(client):
    _sell(client, channel='bazar', channel_note='Bazar del Chopo')
    sale = client.get('/sales/').json()['results'][0]
    assert (sale['channel'], sale['channel_note']) == ('bazar', 'Bazar del Chopo')


def test_metrics_break_point_of_sale_down_by_channel(client):
    _sell(client, channel='store')
    _sell(client, channel='store')
    _sell(client, channel='bazar')
    Sale.objects.create(final_sale_price=Decimal('40.00'))  # before channels: no items, still counted
    places = {row['channel']: row for row in client.get('/sales/metrics/').json()['sale_channels']}
    assert (places['store']['count'], places['store']['gross']) == (2, '200.00')
    assert (places['bazar']['count'], places['bazar']['gross']) == (1, '100.00')
    assert places['unknown']['count'] == 1


def test_export_has_the_place_of_sale(client):
    _sell(client, channel='bazar', channel_note='Bazar del Chopo')
    text = client.get('/sales/export/').content.decode('utf-8')[1:]
    header, row = list(csv.reader(text.splitlines()))
    assert row[header.index('Lugar de venta')] == 'Bazar · Bazar del Chopo'


# ── Dashboard: timeline and previous period ──────────────────────────────


def test_timeline_has_every_day_of_the_range_with_zeros(client):
    _sell(client)  # today, cash, $100
    today = timezone.localdate()
    start = today - timedelta(days=2)
    data = client.get(f'/sales/metrics/?date_from={start}&date_to={today}').json()
    assert data['timeline']['grain'] == 'day'
    points = data['timeline']['points']
    assert [p['period'] for p in points] == [str(start + timedelta(days=i)) for i in range(3)]
    assert (points[0]['pos'], points[0]['count']) == ('0.00', 0)
    assert (points[-1]['pos'], points[-1]['online'], points[-1]['count']) == ('100.00', '0.00', 1)


def test_long_ranges_are_charted_by_month(client):
    _sell(client)
    today = timezone.localdate()
    data = client.get(f'/sales/metrics/?date_from={today - timedelta(days=200)}&date_to={today}').json()
    assert data['timeline']['grain'] == 'month'
    assert data['timeline']['points'][-1]['period'] == today.strftime('%Y-%m')


def test_metrics_compare_with_the_previous_period(client):
    _sell(client)
    today = timezone.localdate()
    data = client.get(f'/sales/metrics/?date_from={today}&date_to={today}').json()
    assert data['previous']['count'] == 0
    # Tomorrow's "previous period" is today: it holds today's sale.
    tomorrow = today + timedelta(days=1)
    data = client.get(f'/sales/metrics/?date_from={tomorrow}&date_to={tomorrow}').json()
    assert (data['summary']['count'], data['previous']['count'], data['previous']['gross']) == (0, 1, '100.00')
    assert client.get('/sales/metrics/').json()['previous'] is None


def test_featured_record_is_a_random_available_one_with_cover(db, api_client):
    assert api_client.get('/records/featured/').status_code == 404
    Record.objects.create(title='Sin portada', price=Decimal('1'), stock=3)
    Record.objects.create(title='Agotado', price=Decimal('1'), stock=0, cover_image_url='https://x/a.jpg')
    assert api_client.get('/records/featured/').status_code == 404
    Record.objects.create(title='Kind of Blue', price=Decimal('1'), stock=1, cover_image_url='https://x/k.jpg')
    resp = api_client.get('/records/featured/')
    assert (resp.status_code, resp.json()['title']) == (200, 'Kind of Blue')
