"""Maintenance window: SiteConfig model, config service, endpoint and middleware."""
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken

from apiApp.services.config import get_maintenance_state, set_maintenance_state


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def admin_user(db):
    User = get_user_model()
    return User.objects.create_user(
        username='boss',
        email='boss@example.com',
        password='AdminPass123!',
        role='ADMIN',
    )


@pytest.fixture
def maintenance_window(db):
    """Open the window for one test and always shut it again after."""
    set_maintenance_state(True, 'Ventana de prueba')
    yield
    set_maintenance_state(False, '')
    cache.clear()


# ── Service / model ─────────────────────────────────────────────────────


def test_default_state_is_off(db):
    assert get_maintenance_state() == (False, settings.MAINTENANCE_DEFAULT_MESSAGE)


def test_set_state_persists_and_reads_live(db):
    set_maintenance_state(True, 'Estamos en mantenimiento')
    assert get_maintenance_state() == (True, 'Estamos en mantenimiento')
    set_maintenance_state(False, '')
    # An empty stored message falls back to the configured default.
    assert get_maintenance_state() == (False, settings.MAINTENANCE_DEFAULT_MESSAGE)


def test_default_message_is_modifiable(db):
    set_maintenance_state(True, 'Cierre por inventario — vuelve el lunes')
    assert get_maintenance_state()[1] == 'Cierre por inventario — vuelve el lunes'
    set_maintenance_state(True, '')
    assert get_maintenance_state()[1] == settings.MAINTENANCE_DEFAULT_MESSAGE


def test_set_state_coerces_and_truncates(db):
    set_maintenance_state('yes', 'x' * 500)
    mode, message = get_maintenance_state()
    assert mode is True
    assert len(message) == 255


def test_save_forces_singleton_pk(db):
    from apiApp.models import SiteConfig
    first = SiteConfig.objects.create(maintenance_mode=True)
    second = SiteConfig.objects.get(pk=1)
    assert first.pk == 1
    assert second.pk == 1
    assert SiteConfig.objects.count() == 1


# ── Endpoint ────────────────────────────────────────────────────────────


def test_get_status_is_public(api_client, db):
    resp = api_client.get(reverse('maintenance-config'))
    assert resp.status_code == 200
    assert resp.data == {
        'maintenance_mode': False,
        'maintenance_message': settings.MAINTENANCE_DEFAULT_MESSAGE,
    }


def test_patch_requires_admin(api_client, db):
    anon = api_client.patch(reverse('maintenance-config'), {'maintenance_mode': True}, format='json')
    assert anon.status_code == 403
    assert anon.data['error']['code'] == 'forbidden'


def test_patch_customer_is_forbidden(api_client, user, db):
    api_client.force_authenticate(user)
    resp = api_client.patch(reverse('maintenance-config'), {'maintenance_mode': True}, format='json')
    assert resp.status_code == 403


def test_admin_can_toggle_window(api_client, admin_user, db):
    api_client.force_authenticate(admin_user)
    resp = api_client.patch(
        reverse('maintenance-config'),
        {'maintenance_mode': True, 'maintenance_message': 'Volvemos pronto'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.data == {'maintenance_mode': True, 'maintenance_message': 'Volvemos pronto'}
    assert get_maintenance_state()[0] is True

    resp = api_client.patch(
        reverse('maintenance-config'),
        {'maintenance_mode': False, 'maintenance_message': ''},
        format='json',
    )
    assert resp.status_code == 200
    # Empty custom message → the PATCH reports the configured default again.
    assert resp.data['maintenance_message'] == settings.MAINTENANCE_DEFAULT_MESSAGE
    assert get_maintenance_state() == (False, settings.MAINTENANCE_DEFAULT_MESSAGE)


def test_patch_validates_payload(api_client, admin_user, db):
    api_client.force_authenticate(admin_user)
    url = reverse('maintenance-config')

    bad_bool = api_client.patch(url, {'maintenance_mode': 'yes'}, format='json')
    assert bad_bool.status_code == 400
    assert bad_bool.data['error']['code'] == 'invalid_maintenance_mode'

    missing = api_client.patch(url, {}, format='json')
    assert missing.status_code == 400

    bad_message = api_client.patch(
        url, {'maintenance_mode': False, 'maintenance_message': 42}, format='json'
    )
    assert bad_message.status_code == 400
    assert bad_message.data['error']['code'] == 'invalid_maintenance_message'


# ── Middleware ──────────────────────────────────────────────────────────


def test_middleware_blocks_anonymous_during_maintenance(api_client, maintenance_window):
    resp = api_client.get(reverse('records-list'))
    assert resp.status_code == 503
    body = resp.json()
    assert body['error']['code'] == 'maintenance_mode'
    assert body['error']['message'] == 'Ventana de prueba'


def test_middleware_allows_admin_token_during_maintenance(api_client, admin_user, maintenance_window):
    token = str(RefreshToken.for_user(admin_user).access_token)
    resp = api_client.get(reverse('records-list'), HTTP_AUTHORIZATION=f'Bearer {token}')
    assert resp.status_code == 200


def test_middleware_blocks_customer_token_during_maintenance(api_client, user, maintenance_window):
    token = str(RefreshToken.for_user(user).access_token)
    resp = api_client.get(reverse('records-list'), HTTP_AUTHORIZATION=f'Bearer {token}')
    assert resp.status_code == 503


def test_middleware_lets_admins_toggle_window_back_off(api_client, admin_user, maintenance_window):
    token = str(RefreshToken.for_user(admin_user).access_token)
    resp = api_client.patch(
        reverse('maintenance-config'),
        {'maintenance_mode': False, 'maintenance_message': ''},
        format='json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )
    assert resp.status_code == 200
    assert get_maintenance_state() == (False, settings.MAINTENANCE_DEFAULT_MESSAGE)
    # Window closed: the public catalog is reachable again for everyone.
    assert api_client.get(reverse('records-list')).status_code == 200


def test_middleware_serves_default_message_when_none_set(api_client, db):
    set_maintenance_state(True, '')
    try:
        assert get_maintenance_state() == (True, settings.MAINTENANCE_DEFAULT_MESSAGE)
        resp = api_client.get(reverse('records-list'))
        assert resp.status_code == 503
        assert resp.json()['error']['message'] == settings.MAINTENANCE_DEFAULT_MESSAGE
    finally:
        set_maintenance_state(False, '')
        cache.clear()


def test_middleware_allows_login_during_maintenance(api_client, maintenance_window):
    # Wrong credentials still yield DRF's 401 — the point is it is NOT the
    # maintenance 503 that would make the admin unable to log in.
    resp = api_client.post(
        reverse('auth-login-user'),
        {'username': 'nobody', 'password': 'wrong'},
        format='json',
    )
    assert resp.status_code != 503


def test_middleware_allows_config_status_during_maintenance(api_client, maintenance_window):
    resp = api_client.get(reverse('maintenance-config'))
    assert resp.status_code == 200
    assert resp.data['maintenance_mode'] is True


def test_middleware_inactive_lets_everything_through(api_client, db):
    assert api_client.get(reverse('records-list')).status_code == 200