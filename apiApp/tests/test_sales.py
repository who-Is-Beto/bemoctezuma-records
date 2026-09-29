"""Owners, record ownership, sales from Punto de venta and the Ventas history.

Emails go to the locmem backend (conftest) on the happy paths; failures mock
``send_email`` where the sales service imports it.
"""
from datetime import datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.db.models import ProtectedError
from django.utils import timezone
from rest_framework.test import APIClient

from apiApp.emails import send_email
from apiApp.models import Artist, Owner, Record, Sale, SaleItem
from apiApp.services import split_commission


CDMX = ZoneInfo('America/Mexico_City')


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _user(username, *codenames, **extra):
    user = get_user_model().objects.create_user(
        username=username, email=f'{username}@example.com', password='Pass12345!', **extra
    )
    if codenames:
        role = Group.objects.create(name=username)
        role.permissions.set(Permission.objects.filter(content_type__app_label='apiApp', codename__in=codenames))
        user.groups.add(role)
    return user


@pytest.fixture
def admin(db):
    return _user('salesadmin', role='ADMIN')


@pytest.fixture
def customer(db):
    return _user('salescustomer')


def _owner(name='Ana', email='ana@example.com'):
    return Owner.objects.create(name=name, email=email)


def _record(title='Kind of Blue', stock=3, owner=None, price='200.00', artist='Miles Davis'):
    return Record.objects.create(
        title=title, price=Decimal(price), stock=stock, owner=owner,
        artist=Artist.objects.get_or_create(name=artist)[0],
    )


def _sell(client, *lines, payment_method='cash', **extra):
    body = {'items': list(lines), 'payment_method': payment_method, **extra}
    return client.post('/sales/create/', body, format='json')


def _sale_at(when, *lines, **fields):
    """A sale dated ``when``; lines are (record, quantity, price[, commission])."""
    sale = Sale.objects.create(**fields)
    Sale.objects.filter(pk=sale.pk).update(created_at=when)  # auto_now_add ignores kwargs
    for record, quantity, price, *commission in lines:
        SaleItem.objects.create(
            sale=sale, record=record, owner=record.owner, quantity=quantity, price=Decimal(price),
            commission_amount=Decimal(commission[0] if commission else '0.00'), title=record.title,
        )
    return sale


# ── Owners ───────────────────────────────────────────────────────────────


def test_admin_creates_owner_and_lists_it(admin):
    client = _client(admin)
    resp = client.post('/owners/create/', {'name': 'Ana López', 'email': ' Ana@Example.com '}, format='json')
    assert resp.status_code == 201
    assert resp.json()['email'] == 'ana@example.com'
    assert [o['name'] for o in client.get('/owners/').json()] == ['Ana López']


def test_owner_needs_name_and_a_valid_unique_email(admin):
    client = _client(admin)
    _owner()
    assert 'email' in client.post('/owners/create/', {'name': 'Ana', 'email': 'no-es-correo'}, format='json').json()
    assert 'name' in client.post('/owners/create/', {'email': 'otra@example.com'}, format='json').json()
    dup = client.post('/owners/create/', {'name': 'Otra Ana', 'email': 'ANA@example.com'}, format='json')
    assert dup.status_code == 400
    assert dup.json()['email'] == ['Ya existe un dueño con ese correo.']
    assert Owner.objects.count() == 1


def test_owner_endpoints_need_permission(customer):
    client = _client(customer)
    assert client.get('/owners/').status_code == 403
    assert client.post('/owners/create/', {'name': 'X', 'email': 'x@example.com'}, format='json').status_code == 403


def test_roles_gate_owners_and_sales(db):
    inventario = _client(_user('inventario', 'access_admin_panel', 'tab_add_record', 'add_record'))
    ventas = _client(_user('ventas', 'access_admin_panel', 'tab_sales'))
    new_owner = {'name': 'Ana', 'email': 'ana@example.com'}

    assert inventario.post('/owners/create/', new_owner, format='json').status_code == 201
    assert inventario.get('/sales/').status_code == 403
    assert ventas.get('/sales/').status_code == 200
    assert ventas.get('/owners/').status_code == 200  # the Ventas owner filter
    assert ventas.post('/owners/create/', new_owner, format='json').status_code == 403


# ── Record ↔ owner ───────────────────────────────────────────────────────


def test_record_owner_is_set_on_create_and_changed_on_update(admin):
    client = _client(admin)
    ana, beto = _owner(), _owner('Beto', 'beto@example.com')
    resp = client.post('/records/create/', {'title': 'Abbey Road', 'price': '350.00', 'stock': 1, 'owner': ana.id}, format='json')
    assert resp.status_code == 201
    assert resp.json()['owner'] == ana.id
    record = Record.objects.get(pk=resp.json()['id'])
    assert list(ana.records.all()) == [record]

    assert client.patch(f'/records/{record.id}/update/', {'owner': beto.id}, format='json').status_code == 200
    record.refresh_from_db()
    assert record.owner == beto
    client.patch(f'/records/{record.id}/update/', {'owner': None}, format='json')
    record.refresh_from_db()
    assert record.owner is None


def test_records_without_owner_keep_working(api_client, admin):
    record = _record(owner=None)
    assert api_client.get('/records/').status_code == 200
    assert _client(admin).get(f'/records/{record.id}/update/').json()['owner'] is None


# ── Registering sales ────────────────────────────────────────────────────


def test_sale_is_saved_decrements_stock_and_emails_the_owner(admin):
    ana = _owner()
    record = _record(stock=3, owner=ana)
    resp = _sell(_client(admin), {'record': record.id, 'quantity': 2, 'price': '150.00'})
    assert resp.status_code == 201
    assert resp.json()['warnings'] == []
    assert resp.json()['sale']['items'][0]['email_sent'] is True

    item = SaleItem.objects.select_related('sale').get()
    assert (item.record, item.owner, item.quantity, item.price, item.email_sent) == (record, ana, 2, Decimal('150.00'), True)
    record.refresh_from_db()
    assert (record.stock, record.final_sale_price) == (1, Decimal('150.00'))

    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ['ana@example.com']
    assert msg.subject == f'Vendimos tu disco 🎶 — Venta #{item.sale_id}'
    for text in ('Kind of Blue', 'Miles Davis', '$150.00 MXN', '$300.00 MXN',
                 timezone.localtime(item.sale.created_at).strftime('%d/%m/%Y')):
        assert text in msg.body


def test_price_defaults_to_the_discounted_record_price(admin):
    record = _record(price='200.00', owner=_owner())
    record.discount_porcentage = 10
    record.save()
    assert _sell(_client(admin), {'record': record.id, 'quantity': 1}).status_code == 201
    assert SaleItem.objects.get().price == Decimal('180.00')


def test_multi_owner_ticket_emails_each_owner_only_their_records(admin):
    ana, beto = _owner(), _owner('Beto', 'beto@example.com')
    kind = _record('Kind of Blue', owner=ana)
    train = _record('Blue Train', owner=ana, artist='John Coltrane')
    abbey = _record('Abbey Road', owner=beto, artist='The Beatles')
    resp = _sell(_client(admin), *({'record': r.id, 'quantity': 1} for r in (kind, train, abbey)))
    assert resp.status_code == 201
    assert Sale.objects.count() == 1 and SaleItem.objects.count() == 3

    assert len(mail.outbox) == 2
    body = {m.to[0]: m.body for m in mail.outbox}
    assert 'Kind of Blue' in body['ana@example.com'] and 'Blue Train' in body['ana@example.com']
    assert 'Abbey Road' not in body['ana@example.com']
    assert 'Abbey Road' in body['beto@example.com']
    assert 'Kind of Blue' not in body['beto@example.com'] and 'Blue Train' not in body['beto@example.com']


def test_sale_without_owner_is_saved_with_a_warning(admin, caplog):
    _record(owner=None)
    resp = _sell(_client(admin), {'record': Record.objects.get().id, 'quantity': 1})
    assert resp.status_code == 201
    assert resp.json()['warnings'] == ['"Kind of Blue" no tiene dueño asignado; no se envió correo.']
    item = SaleItem.objects.get()
    assert item.owner is None and item.email_sent is False
    assert mail.outbox == []
    assert 'has no owner' in caplog.text


def test_email_failure_keeps_the_sale_and_warns(admin, caplog):
    record = _record(stock=2, owner=_owner())
    with mock.patch('apiApp.services.sales.send_email', side_effect=ConnectionError('Resend down')):
        resp = _sell(_client(admin), {'record': record.id, 'quantity': 1})
    assert resp.status_code == 201
    assert resp.json()['warnings'] == ['No se pudo enviar el correo a Ana (ana@example.com).']
    assert SaleItem.objects.get().email_sent is False
    record.refresh_from_db()
    assert record.stock == 1
    assert 'Resend down' in caplog.text


def test_one_failed_owner_email_does_not_affect_the_others(admin):
    kind = _record('Kind of Blue', owner=_owner())
    abbey = _record('Abbey Road', owner=_owner('Beto', 'beto@example.com'), artist='The Beatles')

    def flaky(**kwargs):
        if kwargs['to'] == ['beto@example.com']:
            raise ConnectionError('boom')
        return send_email(**kwargs)

    with mock.patch('apiApp.services.sales.send_email', side_effect=flaky):
        resp = _sell(_client(admin), {'record': kind.id, 'quantity': 1}, {'record': abbey.id, 'quantity': 1})
    assert resp.status_code == 201
    assert [m.to for m in mail.outbox] == [['ana@example.com']]
    sent = {i.owner.email: i.email_sent for i in SaleItem.objects.select_related('owner')}
    assert sent == {'ana@example.com': True, 'beto@example.com': False}


def test_oversell_rolls_back_the_whole_ticket(admin):
    ana = _owner()
    kind = _record('Kind of Blue', stock=5, owner=ana)
    abbey = _record('Abbey Road', stock=1, owner=ana, artist='The Beatles')
    resp = _sell(_client(admin), {'record': kind.id, 'quantity': 2}, {'record': abbey.id, 'quantity': 2})
    assert resp.status_code == 400
    assert resp.json()['error']['code'] == 'insufficient_stock'
    assert 'Abbey Road' in resp.json()['error']['message']
    assert not Sale.objects.exists() and not SaleItem.objects.exists()
    kind.refresh_from_db()
    assert kind.stock == 5
    assert mail.outbox == []


def test_sale_input_is_validated(admin):
    client = _client(admin)
    record = _record()
    assert _sell(client).status_code == 400  # empty ticket
    assert _sell(client, {'record': record.id, 'quantity': 0}).status_code == 400
    assert _sell(client, {'record': 999999, 'quantity': 1}).status_code == 400
    assert _sell(client, {'record': record.id, 'quantity': 1, 'price': '-5'}).status_code == 400
    assert client.post('/sales/create/', {'items': [{'record': record.id, 'quantity': 1}]}, format='json').status_code == 400
    assert _sell(client, {'record': record.id, 'quantity': 1}, payment_method='crypto').status_code == 400
    assert _sell(client, {'record': record.id, 'quantity': 1}, payment_method='card', commission_rate='100.01').status_code == 400
    assert _sell(client, {'record': record.id, 'quantity': 1}, payment_method='card', commission_rate='-1').status_code == 400
    assert client.post('/sales/create/', [], format='json').status_code == 400
    assert not Sale.objects.exists()


def test_selling_needs_permission(customer):
    record = _record()
    assert _sell(_client(customer), {'record': record.id, 'quantity': 1}).status_code == 403
    record.refresh_from_db()
    assert record.stock == 3


# ── Ventas history ───────────────────────────────────────────────────────


def test_history_filters_by_store_date_and_owner_with_totals(admin):
    ana, beto = _owner(), _owner('Beto', 'beto@example.com')
    kind, abbey = _record(owner=ana), _record('Abbey Road', owner=beto, artist='The Beatles')
    # 23:30 in CDMX on Sep 10 is already Sep 11 in UTC: it must count as Sep 10.
    ticket = _sale_at(datetime(2026, 9, 10, 23, 30, tzinfo=CDMX), (kind, 2, '100.00', '8.12'), (abbey, 1, '50.00', '2.03'),
                      payment_method='card', commission_rate=Decimal('4.06'), commission_amount=Decimal('10.15'),
                      final_sale_price=Decimal('239.85'))
    _sale_at(datetime(2026, 9, 11, 0, 30, tzinfo=CDMX), (kind, 1, '70.00'))
    _sale_at(datetime(2026, 9, 1, 12, 0, tzinfo=CDMX), (abbey, 1, '999.00'))
    client = _client(admin)

    day = client.get('/sales/', {'date_from': '2026-09-10', 'date_to': '2026-09-10'}).json()
    assert [s['id'] for s in day['results']] == [ticket.id]
    sale = day['results'][0]
    assert (sale['payment_method'], sale['commission_rate'], sale['commission_amount'], sale['subtotal'],
            sale['final_sale_price']) == ('card', '4.06', '10.15', '250.00', '239.85')
    assert sorted(i['title'] for i in sale['items']) == ['Abbey Road', 'Kind of Blue']
    assert day['totals'] == {'subtotal': '250.00', 'commission': '10.15', 'net': '239.85'}

    # Owner filter: the whole ticket comes back (receipt), totals count only Ana's lines.
    ana_only = client.get('/sales/', {'date_from': '2026-09-10', 'date_to': '2026-09-11', 'owner': ana.id}).json()
    assert [len(s['items']) for s in ana_only['results']] == [1, 2]  # newest first
    assert ana_only['totals'] == {'subtotal': '270.00', 'commission': '8.12', 'net': '261.88'}

    everything = client.get('/sales/').json()
    assert len(everything['results']) == 3 and everything['totals']['subtotal'] == '1319.00'


def test_history_survives_record_deletion_and_protects_owners(admin):
    ana = _owner()
    record = _record(owner=ana)
    assert _sell(_client(admin), {'record': record.id, 'quantity': 1, 'price': '100.00'}).status_code == 201
    record.delete()

    item = _client(admin).get('/sales/').json()['results'][0]['items'][0]
    # The receipt still has the sale-time snapshot.
    assert (item['record'], item['title'], item['artist'], item['owner']['id']) == (None, 'Kind of Blue', 'Miles Davis', ana.id)
    with pytest.raises(ProtectedError):
        ana.delete()


def test_history_needs_permission_and_valid_dates(admin, customer):
    assert _client(customer).get('/sales/').status_code == 403
    resp = _client(admin).get('/sales/', {'date_from': '2026-02-30'})
    assert resp.status_code == 400 and 'date_from' in resp.json()


# ── Payment method & card commission ─────────────────────────────────────


def test_split_commission_rounds_to_cents_and_shares_add_up():
    commission, shares = split_commission([Decimal('400.00'), Decimal('150.00')], Decimal('4.06'))
    assert (commission, shares) == (Decimal('22.33'), [Decimal('16.24'), Decimal('6.09')])
    # Ten 0.12 lines: 0.0487 → 0.05; every exact share is 0.005, so plain rounding
    # would give 0.10. Largest remainders hand out exactly 5 cents, none negative.
    commission, shares = split_commission([Decimal('0.12')] * 10, Decimal('4.06'))
    assert commission == Decimal('0.05') and sum(shares) == commission and min(shares) >= 0
    assert split_commission([Decimal('0.00')], Decimal('4.06')) == (Decimal('0.00'), [Decimal('0.00')])


def test_card_sale_charges_default_commission_on_the_whole_ticket(admin):
    ana = _owner()
    kind, abbey = _record(owner=ana, stock=5), _record('Abbey Road', owner=ana, artist='The Beatles')
    resp = _sell(_client(admin), {'record': kind.id, 'quantity': 2}, {'record': abbey.id, 'quantity': 1, 'price': '150.00'},
                 payment_method='card')
    assert resp.status_code == 201
    sale = Sale.objects.get()
    assert (sale.payment_method, sale.commission_rate, sale.commission_amount, sale.final_sale_price) == (
        'card', Decimal('4.06'), Decimal('22.33'), Decimal('527.67'))
    assert [i.commission_amount for i in sale.items.order_by('id')] == [Decimal('16.24'), Decimal('6.09')]
    body = resp.json()['sale']
    assert (body['subtotal'], body['final_sale_price']) == ('550.00', '527.67')

    # The owner sees the breakdown and their net.
    text = mail.outbox[0].body
    for fragment in ('Tarjeta', 'Comisión por tarjeta (4.06%)', '$550.00 MXN', '$22.33 MXN', '$527.67 MXN'):
        assert fragment in text


def test_card_rate_is_editable_and_cash_or_transfer_never_pay_commission(admin):
    client = _client(admin)
    record = _record(stock=5, owner=_owner())
    _sell(client, {'record': record.id, 'quantity': 1, 'price': '100.00'}, payment_method='card', commission_rate='3.50')
    _sell(client, {'record': record.id, 'quantity': 1, 'price': '100.00'}, payment_method='cash', commission_rate='4.06')
    _sell(client, {'record': record.id, 'quantity': 1, 'price': '100.00'}, payment_method='transfer')
    got = list(Sale.objects.order_by('id').values_list('payment_method', 'commission_rate', 'commission_amount', 'final_sale_price'))
    assert got == [
        ('card', Decimal('3.50'), Decimal('3.50'), Decimal('96.50')),
        ('cash', Decimal('0.00'), Decimal('0.00'), Decimal('100.00')),
        ('transfer', Decimal('0.00'), Decimal('0.00'), Decimal('100.00')),
    ]
    assert 'Comisión' not in mail.outbox[1].body


def test_migration_backfills_sales_registered_before_payment_methods(admin):
    from importlib import import_module
    from django.apps import apps
    backfill = import_module('apiApp.migrations.0050_sale_payment_commission').backfill_existing_sales

    record = _record(owner=_owner())
    record.cover_image_url = 'https://img.example.com/kind.jpg'
    record.save()
    legacy = Sale.objects.create()
    SaleItem.objects.create(sale=legacy, record=record, owner=record.owner, quantity=2, price=Decimal('150.00'))
    SaleItem.objects.create(sale=legacy, record=None, quantity=1, price=Decimal('20.00'))  # record deleted since
    backfill(apps, None)

    legacy.refresh_from_db()
    assert (legacy.payment_method, legacy.commission_amount, legacy.final_sale_price) == ('', Decimal('0.00'), Decimal('320.00'))
    snapshots = list(legacy.items.order_by('id').values_list('title', 'artist', 'cover_image_url'))
    assert snapshots == [('Kind of Blue', 'Miles Davis', 'https://img.example.com/kind.jpg'), ('', '', None)]
