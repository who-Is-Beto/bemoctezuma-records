"""Ventas → Métricas: point-of-sale tickets + paid online orders (GET /sales/metrics/)."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apiApp.models import Artist, Order, OrderItem, Owner, Record, Sale, SaleItem
from apiApp.services.sales import estimated_stripe_fee


CDMX = ZoneInfo('America/Mexico_City')
SEP_10 = datetime(2026, 9, 10, 12, 0, tzinfo=CDMX)
SEP_1 = datetime(2026, 9, 1, 12, 0, tzinfo=CDMX)


@pytest.fixture
def admin_client(db):
    admin = get_user_model().objects.create_user(
        username='metricsadmin', email='metricsadmin@example.com', password='Pass12345!', role='ADMIN'
    )
    client = APIClient()
    client.force_authenticate(admin)
    return client


def _record(title, owner, artist):
    return Record.objects.create(
        title=title, price=Decimal('100.00'), stock=10, owner=owner,
        artist=Artist.objects.get_or_create(name=artist)[0],
    )


def _sale(when, lines, **fields):
    """lines: (record, quantity, price, commission share)."""
    sale = Sale.objects.create(**fields)
    Sale.objects.filter(pk=sale.pk).update(created_at=when)
    for record, quantity, price, commission in lines:
        SaleItem.objects.create(
            sale=sale, record=record, owner=record.owner, quantity=quantity, price=Decimal(price),
            commission_amount=Decimal(commission), title=record.title,
        )


def _order(when, status, amount, shipping, lines, session):
    order = Order.objects.create(
        stripe_checkout_session_id=session, amount=Decimal(amount), currency='mxn',
        user_email='cliente@example.com', shipped_to='home', shipping_cost=Decimal(shipping), status=status,
    )
    Order.objects.filter(pk=order.pk).update(created_at=when)
    for record, quantity, price in lines:
        OrderItem.objects.create(order=order, record=record, quantity=quantity, price=Decimal(price))


@pytest.fixture
def activity(db):
    ana = Owner.objects.create(name='Ana', email='ana@example.com')
    beto = Owner.objects.create(name='Beto', email='beto@example.com')
    kind = _record('Kind of Blue', ana, 'Miles Davis')
    abbey = _record('Abbey Road', beto, 'The Beatles')

    _sale(SEP_10, [(kind, 2, '100.00', '8.12'), (abbey, 1, '50.00', '2.03')], payment_method='card',
          commission_rate=Decimal('4.06'), commission_amount=Decimal('10.15'), final_sale_price=Decimal('239.85'))
    _sale(SEP_10, [(kind, 1, '70.00', '0.00')], payment_method='cash', final_sale_price=Decimal('70.00'))
    _sale(SEP_10, [(abbey, 1, '40.00', '0.00')], final_sale_price=Decimal('40.00'))  # before payment methods
    _sale(SEP_1, [(kind, 1, '999.00', '0.00')], payment_method='cash', final_sale_price=Decimal('999.00'))

    # $300 of records + $150 shipping; fee estimate (450 × 3.6% + 3) × 1.16 = 22.27.
    _order(SEP_10, 'paid', '450.00', '150.00', [(abbey, 3, '100.00')], 'cs_paid')
    _order(SEP_10, 'canceled', '500.00', '0', [(kind, 1, '500.00')], 'cs_canceled')
    _order(SEP_10, 'pending', '500.00', '0', [(kind, 1, '500.00')], 'cs_pending')
    _order(SEP_1, 'delivered', '800.00', '0', [(kind, 1, '800.00')], 'cs_old')
    return ana, beto


def test_estimated_stripe_fee_uses_settings(settings):
    assert estimated_stripe_fee(Decimal('450.00')) == Decimal('22.27')
    assert estimated_stripe_fee(Decimal('0.00')) == Decimal('0.00')
    settings.STRIPE_FEE_PERCENT, settings.STRIPE_FEE_FIXED, settings.STRIPE_FEE_VAT = Decimal('3'), Decimal('0'), Decimal('0')
    assert estimated_stripe_fee(Decimal('100.00')) == Decimal('3.00')


def test_metrics_combine_point_of_sale_and_paid_online_orders(admin_client, activity):
    ana, beto = activity
    data = admin_client.get('/sales/metrics/', {'date_from': '2026-09-10', 'date_to': '2026-09-10'}).json()

    assert data['summary'] == {
        'count': 4, 'units': 8, 'gross': '660.00', 'commission': '32.42', 'net': '627.58',
        'average_ticket': '165.00', 'shipping': '150.00',
    }
    assert data['channels'] == [
        {'channel': 'pos', 'count': 3, 'units': 5, 'gross': '360.00', 'commission': '10.15',
         'net': '349.85', 'average_ticket': '120.00'},
        {'channel': 'online', 'count': 1, 'units': 3, 'gross': '300.00', 'commission': '22.27',
         'net': '277.73', 'average_ticket': '300.00'},
    ]
    assert [(m['method'], m['gross'], m['commission']) for m in data['payment_methods']] == [
        ('stripe', '300.00', '22.27'), ('card', '250.00', '10.15'), ('cash', '70.00', '0.00'), ('unknown', '40.00', '0.00'),
    ]
    assert [(r['title'], r['units'], r['gross']) for r in data['top_records']] == [
        ('Abbey Road', 5, '390.00'), ('Kind of Blue', 3, '270.00'),
    ]
    # Beto's online line carries its share of the Stripe fee: 22.27 × 300/450 = 14.85; shipping's share stays with the store.
    assert [(o['owner']['id'], o['units'], o['gross'], o['commission'], o['net']) for o in data['owners']] == [
        (beto.id, 5, '390.00', '16.88', '373.12'),
        (ana.id, 3, '270.00', '8.12', '261.88'),
    ]


def test_metrics_without_dates_count_everything_and_empty_range_is_zero(admin_client, activity):
    everything = admin_client.get('/sales/metrics/').json()
    assert everything['summary']['count'] == 6
    empty = admin_client.get('/sales/metrics/', {'date_from': '2030-01-01'}).json()
    assert empty['summary']['gross'] == '0.00' and empty['summary']['average_ticket'] == '0.00'
    assert empty['top_records'] == [] and empty['owners'] == []


def test_metrics_need_the_sales_tab(db, api_client):
    customer = get_user_model().objects.create_user(username='c', email='c@example.com', password='Pass12345!')
    api_client.force_authenticate(customer)
    assert api_client.get('/sales/metrics/').status_code == 403
    admin = get_user_model().objects.create_user(username='a', email='a@example.com', password='Pass12345!', role='ADMIN')
    api_client.force_authenticate(admin)
    assert api_client.get('/sales/metrics/', {'date_to': 'nope'}).status_code == 400
