"""Temp debug: class-attr binding vs override. DO NOT COMMIT."""

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.settings import api_settings


@pytest.mark.django_db
def test_debug_email(api_client, user, settings):
    cache.clear()
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "email_verify": "5/hour",
        },
    }
    print("\n[email] api_settings.DEFAULT_THROTTLE_RATES:", api_settings.DEFAULT_THROTTLE_RATES)
    print("[email] classattr THROTTLE_RATES         :", ScopedRateThrottle.THROTTLE_RATES)
    print("[email] same dict obj?                  :", api_settings.DEFAULT_THROTTLE_RATES is ScopedRateThrottle.THROTTLE_RATES)
    url = reverse('resend-verification-email')
    statuses = [api_client.post(url, {'email': user.email}, format='json').status_code for _ in range(6)]
    print("[email] statuses:", statuses)


@pytest.mark.django_db
def test_debug_password(api_client, settings):
    cache.clear()
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "password_reset_request": "5/hour",
        },
    }
    print("\n[pw] api_settings.DEFAULT_THROTTLE_RATES:", api_settings.DEFAULT_THROTTLE_RATES)
    print("[pw] classattr THROTTLE_RATES         :", ScopedRateThrottle.THROTTLE_RATES)
    print("[pw] same dict obj?                  :", api_settings.DEFAULT_THROTTLE_RATES is ScopedRateThrottle.THROTTLE_RATES)
    url = reverse('password-reset-request')
    statuses = [api_client.post(url, {'email': 'ghost@nowhere.invalid'}, format='json').status_code for _ in range(6)]
    print("[pw] statuses:", statuses)