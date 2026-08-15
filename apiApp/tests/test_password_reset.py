import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def _uid(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


def _token(user):
    return default_token_generator.make_token(user)


def _forged_token(user):
    token = _token(user)
    return token[:-1] + ('0' if token[-1] != '0' else '1')


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()



@pytest.mark.django_db
def test_request_unknown_email_returns_generic(api_client):
    resp = api_client.post(
        reverse('password-reset-request'),
        {'email': 'ghost@nowhere.invalid'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json() == {'message': 'If that email is registered, a reset link has been sent'}
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_request_known_email_sends_link(api_client, user):
    resp = api_client.post(
        reverse('password-reset-request'),
        {'email': user.email},
        format='json',
    )
    assert resp.status_code == 200
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert 'uid=' in body
    assert 'token=' in body
    assert '/restablecer-contrasena' in body


@pytest.mark.django_db
def test_request_no_enumeration(api_client, user):
    url = reverse('password-reset-request')
    r_unknown = api_client.post(url, {'email': 'ghost@nowhere.invalid'}, format='json')
    r_known = api_client.post(url, {'email': user.email}, format='json')
    assert r_unknown.status_code == r_known.status_code
    assert r_unknown.json() == r_known.json()


@pytest.mark.django_db
def test_confirm_happy_path(api_client, user):
    resp = api_client.post(
        reverse('password-reset-confirm'),
        {
            'uid': _uid(user),
            'token': _token(user),
            'new_password': 'NewPass456!',
            'confirm_password': 'NewPass456!',
        },
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json() == {'message': 'Password reset successfully'}
    assert authenticate(username=user.username, password='OldPass123!') is None
    assert authenticate(username=user.username, password='NewPass456!') is not None


@pytest.mark.django_db
def test_confirm_forged_token(api_client, user):
    resp = api_client.post(
        reverse('password-reset-confirm'),
        {
            'uid': _uid(user),
            'token': _forged_token(user),
            'new_password': 'NewPass456!',
            'confirm_password': 'NewPass456!',
        },
        format='json',
    )
    assert resp.status_code == 400
    assert 'Invalid or expired reset token' in resp.content.decode()


@pytest.mark.django_db
def test_confirm_garbage_uid(api_client, user):
    resp = api_client.post(
        reverse('password-reset-confirm'),
        {
            'uid': '!!!',
            'token': _token(user),
            'new_password': 'NewPass456!',
            'confirm_password': 'NewPass456!',
        },
        format='json',
    )
    assert resp.status_code == 400
    assert 'Invalid or expired reset token' in resp.content.decode()


@pytest.mark.django_db
def test_confirm_token_single_use(api_client, user):
    uid = _uid(user)
    token = _token(user)
    payload = {
        'uid': uid,
        'token': token,
        'new_password': 'NewPass456!',
        'confirm_password': 'NewPass456!',
    }
    first = api_client.post(reverse('password-reset-confirm'), payload, format='json')
    assert first.status_code == 200
    replay = api_client.post(reverse('password-reset-confirm'), payload, format='json')
    assert replay.status_code == 400
    assert 'Invalid or expired reset token' in replay.content.decode()


@pytest.mark.django_db
def test_confirm_mismatched_passwords(api_client, user):
    resp = api_client.post(
        reverse('password-reset-confirm'),
        {
            'uid': _uid(user),
            'token': _token(user),
            'new_password': 'NewPass456!',
            'confirm_password': 'Different456!',
        },
        format='json',
    )
    assert resp.status_code == 400
    assert 'Passwords do not match' in resp.content.decode()


@pytest.mark.django_db
def test_confirm_weak_password(api_client, user):
    resp = api_client.post(
        reverse('password-reset-confirm'),
        {
            'uid': _uid(user),
            'token': _token(user),
            'new_password': 'short',
            'confirm_password': 'short',
        },
        format='json',
    )
    assert resp.status_code == 400
    assert b'password' in resp.content.lower()


@pytest.mark.django_db
def test_confirm_inactive_user(api_client, user):
    user.is_active = False
    user.save()
    resp = api_client.post(
        reverse('password-reset-confirm'),
        {
            'uid': _uid(user),
            'token': _token(user),
            'new_password': 'NewPass456!',
            'confirm_password': 'NewPass456!',
        },
        format='json',
    )
    assert resp.status_code == 400
    assert 'Invalid or expired reset token' in resp.content.decode()


@pytest.mark.django_db
def test_confirm_success_returns_no_tokens(api_client, user):
    resp = api_client.post(
        reverse('password-reset-confirm'),
        {
            'uid': _uid(user),
            'token': _token(user),
            'new_password': 'NewPass456!',
            'confirm_password': 'NewPass456!',
        },
        format='json',
    )
    assert resp.status_code == 200
    assert 'token' not in resp.json()
    assert 'access' not in resp.json()
    assert 'refresh' not in resp.json()


@pytest.mark.django_db
def test_request_throttled(api_client):
    cache.clear()
    url = reverse('password-reset-request')
    statuses = [
        api_client.post(url, {'email': 'ghost@nowhere.invalid'}, format='json').status_code
        for _ in range(6)
    ]
    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429
