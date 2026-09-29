"""Custom roles: Administración access → tabs → per-tab actions, admin-only CRUD."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apiApp.services.config import get_maintenance_state, set_maintenance_state

from apiApp.admin_panel import normalize_role_codenames


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _make(username, **extra):
    return get_user_model().objects.create_user(
        username=username, email=f'{username}@example.com', password='Pass12345!', **extra
    )


def _role(name, *codenames):
    role = Group.objects.create(name=name)
    role.permissions.set(Permission.objects.filter(content_type__app_label='apiApp', codename__in=codenames))
    return role


def _member(username, role):
    user = _make(username)
    user.groups.add(role)
    return user


@pytest.fixture
def admin(db):
    return _make('rolesadmin', role='ADMIN')


def test_normalize_drops_orphans():
    assert normalize_role_codenames({'tab_manage_orders', 'change_order'}) == set()  # no access
    assert normalize_role_codenames({'access_admin_panel', 'change_order'}) == {'access_admin_panel'}
    assert normalize_role_codenames({'access_admin_panel', 'tab_manage_orders', 'change_order'}) == {
        'access_admin_panel', 'tab_manage_orders', 'change_order'}


def test_admin_role_crud_via_catalog(admin):
    client = _client(admin)
    catalog = client.get('/roles/catalog/').json()
    assert catalog['access']['codename'] == 'access_admin_panel'
    orders = next(t for t in catalog['tabs'] if t['id'] == 'manage-orders')
    assert [a['codename'] for a in orders['actions']] == ['change_order']

    resp = client.post('/roles/', {'name': 'Envíos', 'permissions': [
        'access_admin_panel', orders['codename'], 'change_order',
        'delete_record',  # its tab isn't selected → dropped
    ]}, format='json')
    assert resp.status_code == 201
    assert sorted(resp.json()['permissions']) == ['access_admin_panel', 'change_order', 'tab_manage_orders']

    # Raw Django permissions outside the catalogue are rejected.
    assert client.post('/roles/', {'name': 'x', 'permissions': ['add_cart']}, format='json').status_code == 400

    role_id = resp.json()['id']
    assert client.patch(f'/roles/{role_id}/', {'permissions': []}, format='json').json()['permissions'] == []
    assert client.delete(f'/roles/{role_id}/').status_code == 200


def test_non_admin_cannot_manage_roles_or_users(db):
    user = _member('staffer', _role('Todo', 'access_admin_panel', 'tab_manage_users', 'delete_user'))
    client = _client(user)
    assert client.get('/roles/').status_code == 403
    assert client.get('/roles/catalog/').status_code == 403
    assert client.patch(f'/auth/users/{user.id}/', {'role': 'ADMIN'}, format='json').status_code == 403
    # Can list/delete customers, never admins.
    assert client.get('/auth/users/').status_code == 200
    assert client.delete(f'/auth/users/{_make("admin2", role="ADMIN").id}/delete/').status_code == 403
    assert client.delete(f'/auth/users/{_make("cust").id}/delete/').status_code == 200


def test_levels_gate_admin_endpoints(db):
    viewer = _member('viewer', _role('Ver pedidos', 'access_admin_panel', 'tab_manage_orders'))
    assert _client(viewer).get('/orders/all/').status_code == 200
    assert _client(viewer).patch('/orders/999/update/', {}, format='json').status_code == 403

    editor = _member('editor', _role('Envíos', 'access_admin_panel', 'tab_manage_orders', 'change_order'))
    assert _client(editor).patch('/orders/999/update/', {}, format='json').status_code == 404  # past the gate

    # Tab permission without Administración access → nothing.
    no_access = _member('noaccess', _role('Sin acceso', 'tab_manage_orders', 'change_order'))
    assert _client(no_access).get('/orders/all/').status_code == 403

    # Plain customer, and legacy is_staff flag, grant nothing.
    assert _client(_make('cust')).get('/orders/all/').status_code == 403
    assert _client(_make('staff', is_staff=True)).get('/orders/all/').status_code == 403


def test_admin_assigns_multiple_roles_and_me_merges_permissions(admin):
    shipping = _role('Envíos', 'access_admin_panel', 'tab_manage_orders')
    stock = _role('Inventario', 'access_admin_panel', 'tab_manage_records', 'delete_record')
    target = _make('target')
    resp = _client(admin).patch(
        f'/auth/users/{target.id}/', {'groups': [shipping.id, stock.id]}, format='json'
    )
    assert resp.status_code == 200
    assert sorted(resp.json()['groups']) == sorted([shipping.id, stock.id])
    assert sorted(resp.json()['group_names']) == ['Envíos', 'Inventario']

    me = _client(get_user_model().objects.get(pk=target.pk)).get('/auth/me/').json()
    assert me['permissions'] == [
        'apiApp.access_admin_panel', 'apiApp.delete_record',
        'apiApp.tab_manage_orders', 'apiApp.tab_manage_records',
    ]


def test_maintenance_permission(db):
    manager = _member('manager', _role('Mantenimiento', 'access_admin_panel', 'view_siteconfig', 'change_siteconfig'))
    viewer = _member('mviewer', _role('Ver mantenimiento', 'access_admin_panel', 'view_siteconfig'))
    body = {'maintenance_mode': True, 'maintenance_message': 'Inventario'}

    assert _client(viewer).patch('/config/maintenance/', body, format='json').status_code == 403
    assert _client(_make('cust')).patch('/config/maintenance/', body, format='json').status_code == 403
    assert _client(manager).patch('/config/maintenance/', body, format='json').status_code == 200
    try:
        assert get_maintenance_state()[0] is True

        # While the window is open only the manager (not the viewer) gets past the 503 gate.
        def status_for(user):
            token = str(RefreshToken.for_user(user).access_token)
            return APIClient().get('/auth/me/', HTTP_AUTHORIZATION=f'Bearer {token}').status_code

        assert status_for(manager) == 200
        assert status_for(viewer) == 503
    finally:
        set_maintenance_state(False, '')
