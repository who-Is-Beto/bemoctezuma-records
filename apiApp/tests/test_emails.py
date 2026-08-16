import pytest
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apiApp.emails import (
    _resolve_recipients,
    _template_path,
    html_to_plain_text,
    render_email_html,
    send_password_recovery_email,
    send_verification_email,
    send_welcome_email,
)
from apiApp.services import send_order_created_email, send_order_notification_email


def _uid(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


def _token(user):
    return default_token_generator.make_token(user)


@pytest.mark.parametrize(
    'name',
    ['order_created', 'order_created.html', 'emails/order_created.html'],
)
def test_template_path_normalizes(name):
    assert _template_path(name) == 'emails/order_created.html'


def test_template_path_strips_whitespace():
    assert _template_path(' order_created ') == 'emails/order_created.html'
    assert _template_path(' emails/order_created.html ') == 'emails/order_created.html'


def _order_created_context():
    orders_link = f"{settings.FRONTEND_URL.rstrip('/')}/mis-ordenes"
    return {
        'order_id': 1,
        'amount_str': '$100.00 MXN',
        'shipped_label': 'Enviado a domicilio',
        'tracking': 'Preparando para envío',
        'orders_link': orders_link,
        'items': [
            {
                'title': 'Á é í ó ú & Test',
                'quantity': 2,
                'price_str': '$100.00 MXN',
                'image_url': None,
            }
        ],
        'shipping': None,
        'frontend_url': settings.FRONTEND_URL,
    }


def test_html_to_plain_text_strips_tags_keeps_urls_and_accents():
    orders_link = f"{settings.FRONTEND_URL.rstrip('/')}/mis-ordenes"
    plain = html_to_plain_text(render_email_html('order_created', _order_created_context()))
    assert '<' not in plain
    assert '>' not in plain
    assert f'({orders_link})' in plain
    assert 'Á é í ó ú' in plain


@pytest.mark.django_db
def test_html_to_plain_text_no_double_escape(order):
    send_order_created_email(order['order'])
    body = mail.outbox[-1].body
    assert '&' in body
    assert '&amp;' not in body


def test_resolve_recipients_plain_string():
    assert _resolve_recipients('a@b.com') == ['a@b.com']


def test_resolve_recipients_user_like():
    class Obj:
        email = 'x@y.com'

    assert _resolve_recipients(Obj()) == ['x@y.com']


def test_resolve_recipients_mixed_list():
    class Obj:
        email = 'x@y.com'

    assert _resolve_recipients(['a@b.com', Obj()]) == ['a@b.com', 'x@y.com']


def test_resolve_recipients_deduplicates_preserving_order():
    class Obj:
        email = 'a@b.com'

    assert _resolve_recipients(['a@b.com', 'a@b.com', Obj(), 'c@d.com']) == ['a@b.com', 'c@d.com']


def test_resolve_recipients_empty_string_raises():
    with pytest.raises(ValueError):
        _resolve_recipients(' ')


def test_resolve_recipients_invalid_object_raises():
    with pytest.raises(ValueError):
        _resolve_recipients(123)


def test_resolve_recipients_falsy_email_raises():
    class Obj:
        email = ''

    with pytest.raises(ValueError):
        _resolve_recipients(Obj())


@pytest.mark.django_db
def test_order_email_escapes_html(order):
    send_order_created_email(order['order'])
    msg = mail.outbox[-1]
    html = msg.alternatives[0][0]
    assert '&lt;script&gt;' in html
    assert '<script>' not in html
    assert '&lt;script&gt;' not in msg.body


@pytest.mark.django_db
def test_order_email_query_count(django_assert_num_queries, order):
    with django_assert_num_queries(1):
        send_order_created_email(order['order'])


@pytest.mark.django_db
def test_order_email_outbox_shape(order):
    send_order_created_email(order['order'])
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.subject == f'Tu orden #{order["order"].id} fue creada'
    assert msg.to == ['test@example.com']
    assert len(msg.alternatives) == 1
    assert msg.alternatives[0][1] == 'text/html'


@pytest.mark.django_db
def test_seller_notification_sent_to_configured_recipients(order):
    send_order_notification_email(order['order'])
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.subject == f'🛒 Nueva orden #{order["order"].id} — test@example.com'
    assert msg.to == list(settings.SELLER_NOTIFY_EMAILS)
    assert msg.reply_to == ['test@example.com']
    html = msg.alternatives[0][0]
    # Customer info is present for preparing the order.
    assert 'test@example.com' in html
    assert 'Enviado a domicilio' in html
    assert 'Av 1' in html


@pytest.mark.django_db
def test_seller_notification_skips_when_no_recipients(settings, order):
    settings.SELLER_NOTIFY_EMAILS = []
    send_order_notification_email(order['order'])
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_seller_notification_custom_recipients(settings, order):
    settings.SELLER_NOTIFY_EMAILS = ['shop@example.com']
    send_order_notification_email(order['order'])
    msg = mail.outbox[0]
    assert msg.to == ['shop@example.com']


@pytest.mark.django_db
def test_password_recovery_email(user):
    send_password_recovery_email(
        user,
        'https://example.com/reset?uid=1&token=abc',
        expiry_hours=24,
    )
    msg = mail.outbox[-1]
    assert msg.subject == 'Restablece tu contraseña — Moctezuma Records'
    assert msg.to == ['test@example.com']
    assert 'https://example.com/reset?uid=1&token=abc' in msg.body
    assert 'expira en 24 horas' in msg.body


@pytest.mark.django_db
def test_welcome_email_uses_username_when_no_first_name(user):
    send_welcome_email(user)
    msg = mail.outbox[-1]
    assert msg.subject == '¡Bienvenido a Moctezuma Records!'
    assert msg.to == ['test@example.com']
    assert '¡Hola testuser!' in msg.body
    assert settings.FRONTEND_URL in msg.body


@pytest.mark.django_db
def test_welcome_email_prefers_first_name(user):
    user.first_name = 'Ana'
    user.save()
    send_welcome_email(user)
    msg = mail.outbox[-1]
    assert '¡Hola Ana!' in msg.body
    assert '¡Hola testuser!' not in msg.body


@pytest.mark.django_db
def test_welcome_email_html_escapes_user_name(user):
    user.first_name = '<b>Pepito</b>'
    user.save()
    send_welcome_email(user)
    msg = mail.outbox[-1]
    html = msg.alternatives[0][0]
    assert '&lt;b&gt;Pepito&lt;/b&gt;' in html
    assert '<b>Pepito</b>' not in html


@pytest.mark.django_db
def test_register_user_sends_welcome_email(api_client):
    resp = api_client.post(
        reverse('auth-register-user'),
        {
            'username': 'newuser',
            'email': 'newuser@example.com',
            'password': 'StrongPass123!',
            'first_name': 'Nueva',
        },
        format='json',
    )
    assert resp.status_code == 201
    assert 'tokens' in resp.json()
    assert resp.json()['email_verified'] is False
    assert len(mail.outbox) == 2
    subjects = {m.subject for m in mail.outbox}
    assert subjects == {
        '¡Bienvenido a Moctezuma Records!',
        'Confirma tu correo — Moctezuma Records',
    }
    msg = mail.outbox[0]
    assert msg.subject == '¡Bienvenido a Moctezuma Records!'
    assert msg.to == ['newuser@example.com']
    assert '¡Hola Nueva!' in msg.body


@pytest.mark.django_db
def test_register_user_escapes_welcome_name(api_client):
    resp = api_client.post(
        reverse('auth-register-user'),
        {
            'username': 'tricky<user>',
            'email': 'tricky@example.com',
            'password': 'StrongPass123!',
        },
        format='json',
    )
    assert resp.status_code == 201
    msg = mail.outbox[-1]
    html = msg.alternatives[0][0]
    assert '&lt;user&gt;' in html
    assert '<user>' not in html


@pytest.mark.django_db
def test_verification_email(user):
    send_verification_email(user, 'https://example.com/verify?uid=1&token=abc', expiry_hours=24)
    msg = mail.outbox[-1]
    assert msg.subject == 'Confirma tu correo — Moctezuma Records'
    assert msg.to == ['test@example.com']
    assert 'https://example.com/verify?uid=1&token=abc' in msg.body
    assert 'expira en 24 horas' in msg.body


@pytest.mark.django_db
def test_verify_email_happy_path(api_client, user):
    resp = api_client.post(
        reverse('verify-email'),
        {'uid': _uid(user), 'token': _token(user)},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json() == {'message': 'Email verified successfully', 'email_verified': True}
    user.refresh_from_db()
    assert user.email_verified is True


@pytest.mark.django_db
def test_verify_email_forged_token(api_client, user):
    token = _token(user)
    forged = token[:-1] + ('0' if token[-1] != '0' else '1')
    resp = api_client.post(
        reverse('verify-email'),
        {'uid': _uid(user), 'token': forged},
        format='json',
    )
    assert resp.status_code == 400
    assert 'Invalid or expired verification link' in resp.content.decode()
    user.refresh_from_db()
    assert user.email_verified is False


@pytest.mark.django_db
def test_verify_email_garbage_uid(api_client, user):
    resp = api_client.post(
        reverse('verify-email'),
        {'uid': '!!!', 'token': _token(user)},
        format='json',
    )
    assert resp.status_code == 400
    assert 'Invalid or expired verification link' in resp.content.decode()


@pytest.mark.django_db
def test_verify_email_idempotent(api_client, user):
    payload = {'uid': _uid(user), 'token': _token(user)}
    first = api_client.post(reverse('verify-email'), payload, format='json')
    second = api_client.post(reverse('verify-email'), payload, format='json')
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()['message'] == 'Email already verified'


@pytest.mark.django_db
def test_resend_verification_email(api_client, user):
    resp = api_client.post(
        reverse('resend-verification-email'),
        {'email': user.email},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json() == {'message': 'If that email is registered, a verification link has been sent'}
    assert len(mail.outbox) == 1
    assert 'Confirma tu correo' in mail.outbox[0].subject


@pytest.mark.django_db
def test_resend_skips_when_already_verified(api_client, user):
    user.email_verified = True
    user.save()
    resp = api_client.post(
        reverse('resend-verification-email'),
        {'email': user.email},
        format='json',
    )
    assert resp.status_code == 200
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_resend_unknown_email_generic(api_client):
    resp = api_client.post(
        reverse('resend-verification-email'),
        {'email': 'ghost@nowhere.invalid'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json() == {'message': 'If that email is registered, a verification link has been sent'}
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_resend_verification_email_case_insensitive(api_client, user):
    """Resend must find the user even when the email is typed with different
    casing than the one stored at registration."""
    resp = api_client.post(
        reverse('resend-verification-email'),
        {'email': user.email.upper()},
        format='json',
    )
    assert resp.status_code == 200
    assert len(mail.outbox) == 1
    assert 'Confirma tu correo' in mail.outbox[0].subject
    assert mail.outbox[0].to == ['test@example.com']


@pytest.mark.django_db
def test_resend_throttled(api_client, user):
    cache.clear()
    url = reverse('resend-verification-email')
    statuses = [
        api_client.post(url, {'email': user.email}, format='json').status_code
        for _ in range(6)
    ]
    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429


@pytest.mark.django_db
def test_login_returns_email_verified_flag(settings, api_client, user):
    # The flag-in-response behavior is what's under test here; the login gate
    # itself is covered by the email_not_verified tests in this file and in
    # test_verification_gate.py.
    settings.REQUIRE_EMAIL_VERIFICATION = False
    resp = api_client.post(
        reverse('auth-login-user'),
        {'username': user.username, 'password': 'OldPass123!'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['email_verified'] is False


@pytest.mark.django_db
def test_login_blocked_when_verification_required(api_client, user, settings):
    settings.REQUIRE_EMAIL_VERIFICATION = True
    resp = api_client.post(
        reverse('auth-login-user'),
        {'username': user.username, 'password': 'OldPass123!'},
        format='json',
    )
    assert resp.status_code == 403
    assert resp.json()['error']['code'] == 'email_not_verified'


@pytest.mark.django_db
def test_login_allowed_after_verification_required(api_client, user, settings):
    user.email_verified = True
    user.save()
    settings.REQUIRE_EMAIL_VERIFICATION = True
    resp = api_client.post(
        reverse('auth-login-user'),
        {'username': user.username, 'password': 'OldPass123!'},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['email_verified'] is True
