"""Ventas → Métricas: point-of-sale tickets + paid online orders (GET /sales/metrics/)."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apiApp.models import Artist, Order, OrderItem, Owner, Record, RecordOwner, Sale, SaleItem
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
    record = Record.objects.create(
        title=title, price=Decimal('100.00'), stock=10,
        artist=Artist.objects.get_or_create(name=artist)[0],
    )
    if owner:
        RecordOwner.objects.create(record=record, owner=owner, quantity=10)
    return record


def _owner_of(record):
    row = record.owner_stock.first()
    return row.owner if row else None


def _sale(when, lines, **fields):
    """lines: (record, quantity, price, commission share)."""
    sale = Sale.objects.create(**fields)
    Sale.objects.filter(pk=sale.pk).update(created_at=when)
    for record, quantity, price, commission in lines:
        SaleItem.objects.create(
            sale=sale, record=record, owner=_owner_of(record), quantity=quantity, price=Decimal(price),
            commission_amount=Decimal(commission), title=record.title,
        )


def _order(when, status, amount, shipping, lines, session):
    order = Order.objects.create(
        stripe_checkout_session_id=session, amount=Decimal(amount), currency='mxn',
        user_email='cliente@example.com', shipped_to='home', shipping_cost=Decimal(shipping), status=status,
    )
    Order.objects.filter(pk=order.pk).update(created_at=when)
    for record, quantity, price in lines:
        OrderItem.objects.create(order=order, record=record, owner=_owner_of(record), quantity=quantity, price=Decimal(price))


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

    # No line here has a cost recorded: profit = net, and every unit is flagged.
    assert data['summary'] == {
        'count': 4, 'units': 8, 'gross': '660.00', 'commission': '32.42', 'net': '627.58',
        'average_ticket': '165.00', 'shipping': '150.00',
        'cost': '0.00', 'profit': '627.58', 'units_without_cost': 8,
    }
    assert data['channels'] == [
        {'channel': 'pos', 'count': 3, 'units': 5, 'gross': '360.00', 'commission': '10.15',
         'net': '349.85', 'average_ticket': '120.00', 'cost': '0.00', 'profit': '349.85', 'units_without_cost': 5},
        {'channel': 'online', 'count': 1, 'units': 3, 'gross': '300.00', 'commission': '22.27',
         'net': '277.73', 'average_ticket': '300.00', 'cost': '0.00', 'profit': '277.73', 'units_without_cost': 3},
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


# ── Profit: net minus the cost saved on each line at sale time ───────────


def test_profit_uses_the_cost_saved_when_it_sold(admin_client):
    from unittest import mock

    from apiApp.models import Cart, CartItem
    from apiApp.services import fulfill_checkout

    ana = Owner.objects.create(name='Ana', email='ana@example.com')
    kind = _record('Kind of Blue', ana, 'Miles Davis')
    Record.objects.filter(pk=kind.pk).update(cost_price=Decimal('60.00'))
    free = Record.objects.create(title='Sin costo', price=Decimal('80.00'), stock=5)  # cost_price defaults to 0

    # Point of sale, card 4.06 %: 2 × 100 + 1 × 80 = 280, commission 11.37.
    resp = admin_client.post('/sales/create/', {
        'items': [{'record': kind.id, 'quantity': 2, 'price': '100.00'},
                  {'record': free.id, 'quantity': 1, 'price': '80.00'}],
        'payment_method': 'card',
    }, format='json')
    assert resp.status_code == 201
    # Online: 1 × Kind of Blue at 100 through the real fulfillment path.
    cart = Cart.objects.create()
    CartItem.objects.create(cart=cart, record=kind, quantity=1)
    session = {'id': 'cs_profit', 'amount_total': 10000, 'currency': 'mxn',
               'customer_email': 'buyer@example.com', 'metadata': {'shipped_to': 'store'}}
    with mock.patch('stripe.checkout.Session.list_line_items', side_effect=RuntimeError('offline tests')):
        fulfill_checkout(session, cart.cart_code)
    assert OrderItem.objects.get().cost_price == Decimal('60.00')

    # The cost changes and a record is deleted afterwards: past profit must not move.
    Record.objects.filter(pk=kind.pk).update(cost_price=Decimal('95.00'))
    free.delete()

    summary = admin_client.get('/sales/metrics/').json()['summary']
    # cost = 3 × 60; fee on the 100 online order = (100 × 3.6% + 3) × 1.16 = 7.66.
    assert (summary['gross'], summary['commission'], summary['cost']) == ('380.00', '19.03', '180.00')
    assert summary['profit'] == '180.97'  # 380 - 19.03 - 180
    assert summary['units_without_cost'] == 1  # the 80 record had no cost recorded

    channels = {c['channel']: c for c in admin_client.get('/sales/metrics/').json()['channels']}
    assert (channels['pos']['cost'], channels['pos']['profit']) == ('120.00', '148.63')
    assert (channels['online']['cost'], channels['online']['profit']) == ('60.00', '32.34')
    owner_row = next(o for o in admin_client.get('/sales/metrics/').json()['owners'] if o['owner'])
    assert (owner_row['units'], owner_row['cost'], owner_row['units_without_cost']) == (3, '180.00', 0)


def test_a_sale_below_cost_shows_negative_profit(admin_client):
    record = Record.objects.create(title='Remate', price=Decimal('50.00'), cost_price=Decimal('120.00'), stock=1)
    admin_client.post('/sales/create/', {'items': [{'record': record.id, 'quantity': 1}], 'payment_method': 'cash'},
                      format='json')
    summary = admin_client.get('/sales/metrics/').json()['summary']
    assert (summary['net'], summary['cost'], summary['profit']) == ('50.00', '120.00', '-70.00')


# ── Exportar CSV (GET /sales/export/) ────────────────────────────────────


def _csv(client, url):
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp['Content-Type'].startswith('text/csv')
    text = resp.content.decode('utf-8')
    assert text.startswith('﻿')  # Excel reads the accents as UTF-8
    import csv
    return list(csv.reader(text[1:].splitlines()))


def test_export_has_every_paid_line_of_the_period(admin_client, activity):
    header, *rows = _csv(admin_client, '/sales/export/?date_from=2026-09-10&date_to=2026-09-10')
    assert header[:5] == ['Fecha', 'Hora', 'Canal', 'Lugar de venta', 'Folio']
    # 4 point-of-sale lines + the paid order's line; canceled/pending/other days left out.
    assert len(rows) == 5
    col = {name: i for i, name in enumerate(header)}
    online = next(r for r in rows if r[col['Canal']] == 'En línea')
    assert (online[col['Disco']], online[col['Dueño']], online[col['Cantidad']]) == ('Abbey Road', 'Beto', '3')
    assert (online[col['Subtotal']], online[col['Cliente']]) == ('300.00', 'cliente@example.com')
    # Fee share of the $450 order: 22.27 × 300 / 450.
    assert (online[col['Comisión']], online[col['Neto']]) == ('14.85', '285.15')
    card = next(r for r in rows if r[col['Forma de pago']] == 'Tarjeta' and r[col['Disco']] == 'Kind of Blue')
    assert (card[col['Fecha']], card[col['Subtotal']], card[col['Comisión']], card[col['Ganancia']]) == (
        '2026-09-10', '200.00', '8.12', '191.88')


def test_export_by_owner_has_only_their_lines(admin_client, activity):
    ana, _ = activity
    header, *rows = _csv(admin_client, f'/sales/export/?owner={ana.id}')
    assert {r[header.index('Dueño')] for r in rows} == {'Ana'}
    assert len(rows) == 4  # 3 point-of-sale lines + the old delivered order


def test_export_needs_the_sales_tab(db, api_client):
    customer = get_user_model().objects.create_user(username='c', email='c@example.com', password='Pass12345!')
    api_client.force_authenticate(customer)
    assert api_client.get('/sales/export/').status_code == 403
