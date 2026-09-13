"""Regression test for the prod incident where /auth/register/ 500'd.

Root cause: a hung SMTP connection to smtp.gmail.com blocked the gunicorn
worker past its 30s timeout, killing the request. The view's try/except
around the email sends only catches *raised* exceptions, not hangs. The
fix is EMAIL_TIMEOUT in settings.py; this test locks in the contract that
register must succeed even when the email backend raises.
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient


class _ExplodingEmailBackend:
    """Simulates an unreachable mail server that raises instead of hanging."""

    def __init__(self, *args, **kwargs):
        pass

    def send_messages(self, messages):
        raise OSError('SMTP unreachable (simulated)')


@pytest.mark.django_db
def test_register_survives_email_backend_failure(api_client, settings):
    settings.EMAIL_BACKEND = 'apiApp.tests.test_register_repro._ExplodingEmailBackend'
    resp = api_client.post(
        reverse('auth-register-user'),
        {
            'username': 'reg_resilient',
            'email': 'reg.resilient@example.com',
            'password': 'StrongPass123!',
            'first_name': 'Resiliente',
        },
        format='json',
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body['email_verified'] is False
    assert 'tokens' in body
