from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

from apiApp.models import Order, OrderItem, Record


@pytest.fixture(autouse=True)
def _email_test_env(settings):
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    mail.outbox.clear()


@pytest.fixture(autouse=True)
def _shipping_config(settings):
    """The Envíos Perros token normally comes from the untracked local `.env`,
    which is absent on CI. The shipping tests mock every HTTP call, so a dummy
    token is safe — without it every shipping endpoint 502s with
    `shipping_not_configured`."""
    settings.ENVIOS_PERROS_TOKEN = 'test-token'


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """Django's locmem cache persists across tests within the same pytest
    process, so rate-limit counters (password reset / email verify / etc.)
    leaked between tests and produced random 429s. Flush before AND after
    every test so each one starts with a clean throttle window."""
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _local_file_storage(settings):
    """Force FileSystemStorage in tests so uploads land on disk, not R2."""
    settings.STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    settings.MEDIA_URL = '/media/'


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(
        username='testuser',
        email='test@example.com',
        password='OldPass123!',
    )


@pytest.fixture
def order(db):
    record = Record.objects.create(
        title='Vinilo & Cía <b>Test</b>',
        price=Decimal('100.00'),
        stock=1,
    )
    order = Order.objects.create(
        stripe_checkout_session_id='cs_test_unique_1',
        amount=Decimal('100.00'),
        currency='mxn',
        user_email='test@example.com',
        shipped_to='home',
        shipping_details={
            "fullName": "<script>alert(1)</script>",
            "street": "Av 1",
            "number": "2",
            "neighborhood": "N",
            "city": "CDMX",
            "state": "CDMX",
            "zip": "01000",
            "phone": "555",
            "reference": "x",
        },
        shipping_link='',
        status='paid',
    )
    order_item = OrderItem.objects.create(
        order=order,
        record=record,
        quantity=2,
        price=Decimal('100.00'),
    )
    return {'order': order, 'record': record, 'order_item': order_item}


@pytest.fixture
def api_client():
    return APIClient()
