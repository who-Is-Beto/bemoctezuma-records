import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apiApp.models import Owner, PreSale, PreSaleOrder
from apiApp.services import presales as svc
from apiApp.tests.presale_factories import make_option, make_order, make_presale

D = Decimal


def image_file():
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, 'JPEG')
    return SimpleUploadedFile('c.jpg', buffer.getvalue(), content_type='image/jpeg')
User = get_user_model()


@pytest.fixture
def admin_client(api_client, db):
    admin = User.objects.create_user(username='boss', email='boss@x.com', password='x', role='ADMIN')
    api_client.force_authenticate(admin)
    api_client.user = admin
    return api_client


def role_user(*codenames):
    user = User.objects.create_user(username='staff', email='staff@x.com', password='x')
    group = Group.objects.create(name='Staff')
    group.permissions.set(Permission.objects.filter(codename__in=('access_admin_panel',) + codenames))
    user.groups.add(group)
    return user


@pytest.mark.django_db
@pytest.mark.parametrize('method,url', [
    ('get', '/presales/admin/'),
    ('post', '/presales/admin/'),
    ('post', '/presales/admin/orders/'),
    ('get', '/presales/admin/orders/code/PV-AAAA/'),
])
def test_admin_endpoints_forbid_customers(api_client, user, method, url):
    api_client.force_authenticate(user)
    assert getattr(api_client, method)(url, {}, format='json').status_code == 403


@pytest.mark.django_db
def test_role_with_tab_can_list_but_not_pay(api_client):
    api_client.force_authenticate(role_user('tab_presales'))
    order = make_order()
    assert api_client.get('/presales/admin/').status_code == 200
    res = api_client.post(f'/presales/admin/orders/{order.id}/payments/', {'amount': '100', 'method': 'cash'}, format='json')
    assert res.status_code == 403


@pytest.mark.django_db
def test_create_presale_with_options_multipart(admin_client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path  # keep test uploads out of the repo's media/
    owner = Owner.objects.create(name='Juan', email='juan@x.com')
    options = [
        {'name': 'Vinilo rojo', 'price': '800', 'min_deposit': '200', 'full_payment_discount': '50',
         'copies': 10, 'cost_price': '450', 'owner': owner.id},
        {'name': 'CD', 'price': '350', 'min_deposit': '100', 'copies': 5},
    ]
    res = admin_client.post('/presales/admin/', {
        'title': 'Kind of Blue', 'artist': 'Miles Davis', 'description': 'Reedición',
        'purchase_deadline': (timezone.localdate() + timedelta(days=20)).isoformat(),
        'estimated_arrival': 'Enero 2027',
        'cover': image_file(),
        'options': json.dumps(options),
    }, format='multipart')
    assert res.status_code == 201, res.json()
    presale = PreSale.objects.get()
    assert presale.status == 'draft'
    assert [o.name for o in presale.options.all()] == ['Vinilo rojo', 'CD']
    assert res.json()['options'][0]['cost_price'] == '450.00'


@pytest.mark.django_db
def test_create_rejects_invalid_option(admin_client):
    res = admin_client.post('/presales/admin/', {
        'title': 'X', 'artist': 'Y', 'purchase_deadline': '2030-01-01',
        'options': json.dumps([{'name': 'LP', 'price': '300', 'min_deposit': '300', 'copies': 1}]),
    }, format='multipart')
    assert res.status_code == 400
    assert PreSale.objects.count() == 0


@pytest.mark.django_db
def test_create_requires_an_option(admin_client):
    res = admin_client.post('/presales/admin/', {'title': 'X', 'artist': 'Y', 'purchase_deadline': '2030-01-01',
                                                 'options': '[]'}, format='multipart')
    assert res.status_code == 400


@pytest.mark.django_db
def test_admin_registers_instagram_order_with_cash(admin_client):
    option = make_option()
    res = admin_client.post('/presales/admin/orders/', {
        'option_id': option.id, 'buyer_name': 'Luis', 'buyer_phone': '5511112222', 'source': 'instagram',
        'delivery': 'pickup', 'amount': '300', 'method': 'cash', 'terms_accepted': True,
    }, format='json')
    assert res.status_code == 201, res.json()
    order = PreSaleOrder.objects.get()
    assert order.source == 'instagram' and order.status == 'apartado'
    assert res.json()['paid'] == '300.00' and res.json()['balance'] == '500.00'


@pytest.mark.django_db
def test_admin_order_requires_terms(admin_client):
    option = make_option()
    res = admin_client.post('/presales/admin/orders/', {
        'option_id': option.id, 'buyer_name': 'Luis', 'source': 'store', 'delivery': 'pickup',
        'amount': '300', 'method': 'cash', 'terms_accepted': False}, format='json')
    assert res.status_code == 400


@pytest.mark.django_db
def test_admin_order_links_verified_user_by_email(admin_client):
    customer = User.objects.create_user(username='ana', email='ana@example.com', password='x', email_verified=True)
    option = make_option()
    admin_client.post('/presales/admin/orders/', {
        'option_id': option.id, 'buyer_name': 'Ana', 'buyer_email': 'ANA@example.com', 'source': 'store',
        'delivery': 'pickup', 'amount': '300', 'method': 'card', 'terms_accepted': True}, format='json')
    assert PreSaleOrder.objects.get().user == customer


@pytest.mark.django_db
def test_lookup_by_code_accepts_lowercase_without_prefix(admin_client):
    order = make_order()
    res = admin_client.get(f'/presales/admin/orders/code/{order.code[3:].lower()}/')
    assert res.status_code == 200
    assert res.json()['code'] == order.code


@pytest.mark.django_db
def test_totals_add_up(admin_client):
    option = make_option()
    a = make_order(option)
    svc.record_payment(a, D('800'), 'cash')
    b = make_order(option)
    svc.record_payment(b, D('300'), 'cash')
    make_order(option)
    make_order(option, status='cancelado')
    body = admin_client.get(f'/presales/admin/{option.presale_id}/orders/').json()
    totals = body['totals']
    assert totals['reserved'] == 3 and totals['paid_full'] == 1
    assert totals['collected'] == '1100.00' and totals['owed'] == '1300.00'
    assert len(body['orders']) == 4


@pytest.mark.django_db
def test_orders_filter_owes(admin_client):
    option = make_option()
    paid = make_order(option)
    svc.record_payment(paid, D('800'), 'cash')
    owes = make_order(option)
    body = admin_client.get(f'/presales/admin/{option.presale_id}/orders/?filter=owes').json()
    assert [o['code'] for o in body['orders']] == [owes.code]


@pytest.mark.django_db
def test_option_price_locked_after_orders(admin_client):
    option = make_option()
    make_order(option)
    res = admin_client.patch(f'/presales/admin/options/{option.id}/', {'price': '900'}, format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'option_has_orders'
    res = admin_client.patch(f'/presales/admin/options/{option.id}/', {'copies': 12, 'name': 'Rojo'}, format='json')
    assert res.status_code == 200


@pytest.mark.django_db
def test_option_copies_not_below_taken(admin_client):
    option = make_option(copies=3)
    make_order(option)
    make_order(option)
    res = admin_client.patch(f'/presales/admin/options/{option.id}/', {'copies': 1}, format='json')
    assert res.status_code == 400


@pytest.mark.django_db
def test_delete_presale_only_without_orders(admin_client):
    option = make_option()
    make_order(option)
    assert admin_client.delete(f'/presales/admin/{option.presale_id}/').status_code == 400
    empty = make_presale(title='Otra')
    assert admin_client.delete(f'/presales/admin/{empty.id}/').status_code == 204


@pytest.mark.django_db
def test_extend_moves_deadline_for_open_orders(admin_client):
    option = make_option()
    open_order = make_order(option)
    closed = make_order(option, status='cancelado')
    until = timezone.localdate() + timedelta(days=300)
    res = admin_client.post(f'/presales/admin/{option.presale_id}/extend/', {'until': until.isoformat()}, format='json')
    assert res.status_code == 200
    open_order.refresh_from_db(); closed.refresh_from_db()
    assert open_order.deadline_override == until and closed.deadline_override is None


@pytest.mark.django_db
def test_payment_void_and_deliver_flow(admin_client):
    option = make_option(arrived_at=timezone.now())
    order = make_order(option)
    res = admin_client.post(f'/presales/admin/orders/{order.id}/payments/', {'amount': '800', 'method': 'transfer', 'note': 'SPEI 123'}, format='json')
    assert res.status_code == 201 and res.json()['status'] == 'listo'
    payment_id = res.json()['payments'][0]['id']
    res = admin_client.post(f'/presales/admin/payments/{payment_id}/void/', {'reason': ''}, format='json')
    assert res.status_code == 400
    res = admin_client.post(f'/presales/admin/payments/{payment_id}/void/', {'reason': 'No llegó'}, format='json')
    assert res.json()['status'] == 'apartado'
    res = admin_client.post(f'/presales/admin/orders/{order.id}/deliver/', format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'balance_due'


@pytest.mark.django_db
def test_arrived_all_marks_every_option(admin_client):
    presale = make_presale()
    a, b = make_option(presale), make_option(presale, name='CD')
    assert admin_client.post(f'/presales/admin/{presale.id}/arrived/', format='json').status_code == 200
    a.refresh_from_db(); b.refresh_from_db()
    assert a.arrived_at and b.arrived_at


@pytest.mark.django_db
def test_publish_sets_status(admin_client):
    presale = make_presale(status='draft')
    make_option(presale)
    res = admin_client.post(f'/presales/admin/{presale.id}/publish/', {'announce': False}, format='json')
    assert res.status_code == 200 and res.json()['status'] == 'published'


@pytest.mark.django_db
def test_requote_sets_shipping_cost(admin_client):
    from unittest import mock
    order = make_order(delivery='shipping', shipping_details={'zip': '64000'})
    quote = [{'courier': 'Estafeta', 'serviceType': 'T', 'title': 'E', 'total': '170.00', 'available': True}]
    with mock.patch('apiApp.services.shipping.get_shipping_quotes', return_value=quote):
        res = admin_client.post(f'/presales/admin/orders/{order.id}/shipping-quote/', format='json')
    assert res.status_code == 200 and res.json()['shipping_cost'] == '170.00'


@pytest.mark.django_db
def test_extend_never_shortens_a_later_override(admin_client):
    option = make_option()
    later = timezone.localdate() + timedelta(days=400)
    order = make_order(option, deadline_override=later)
    admin_client.post(f'/presales/admin/{option.presale_id}/extend/',
                      {'until': (timezone.localdate() + timedelta(days=200)).isoformat()}, format='json')
    order.refresh_from_db()
    assert order.deadline_override == later


@pytest.mark.django_db
def test_create_attaches_an_image_to_each_version(admin_client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    options = [
        {'name': 'Negro', 'price': '800', 'min_deposit': '200', 'copies': 10},
        {'name': 'Rojo', 'price': '900', 'min_deposit': '200', 'copies': 5},
    ]
    res = admin_client.post('/presales/admin/', {
        'title': 'Kind of Blue', 'artist': 'Miles Davis',
        'purchase_deadline': (timezone.localdate() + timedelta(days=20)).isoformat(),
        'options': json.dumps(options),
        'option_image_1': image_file(),
    }, format='multipart')
    assert res.status_code == 201, res.json()
    negro, rojo = PreSale.objects.get().options.order_by('position')
    assert not negro.image
    assert rojo.image.name.startswith('presales/')
    assert res.json()['options'][1]['image_url']


@pytest.mark.django_db
def test_version_image_can_change_after_orders(admin_client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    option = make_option()
    make_order(option)
    res = admin_client.patch(f'/presales/admin/options/{option.id}/', {'image': image_file()}, format='multipart')
    assert res.status_code == 200, res.json()
    option.refresh_from_db()
    assert option.image
