import pytest
from django.conf import settings
from django.core import mail

from apiApp.emails import (
    _resolve_recipients,
    _template_path,
    html_to_plain_text,
    render_email_html,
    send_password_recovery_email,
)
from apiApp.services import send_order_created_email


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
