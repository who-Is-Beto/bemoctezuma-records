"""Site-wide configuration: the maintenance window.

The state is cached with a short TTL so the per-request middleware check does
not hit the database every time, while a flip still propagates in seconds.
``set_maintenance_state`` invalidates the key so a saved change is visible
immediately (LocMemCache is per-process, but the TTL self-heals anything that
a scale-out leaves stale).
"""

from django.conf import settings
from django.core.cache import cache

from ..models import SiteConfig

_CACHE_KEY = "site_config:maintenance"
_CACHE_TTL = 3  # seconds — short enough that a window flip feels instant


def get_maintenance_state():
    """Return ``(maintenance_mode, maintenance_message)`` for the site.

    The message is the *effective* one: a custom message stored in
    SiteConfig wins, otherwise MAINTENANCE_DEFAULT_MESSAGE (modifiable via
    the admin PATCH) is returned.
    """
    state = cache.get(_CACHE_KEY)
    if state is None:
        state = _read_state_from_db()
        cache.set(_CACHE_KEY, state, _CACHE_TTL)
    return state


def _read_state_from_db():
    """Read the persisted state, degrading to ``OFF`` if the DB is unreachable.

    The middleware runs before every request, so a DB hiccup must never turn
    into a 500 for the whole store — the maintenance window simply stays
    closed (and pytest-django's DB guard stays happy for non-DB tests).
    """
    try:
        config = SiteConfig.objects.get_or_create(pk=1)[0]
        message = config.maintenance_message or settings.MAINTENANCE_DEFAULT_MESSAGE
        return (config.maintenance_mode, message)
    except Exception:
        return (False, settings.MAINTENANCE_DEFAULT_MESSAGE)


def set_maintenance_state(mode, message):
    """Persist the maintenance window and invalidate the cache."""
    config = _get_config()
    config.maintenance_mode = bool(mode)
    config.maintenance_message = (message or "")[:255]
    config.save(update_fields=["maintenance_mode", "maintenance_message"])
    cache.delete(_CACHE_KEY)


def _get_config():
    return SiteConfig.objects.get_or_create(pk=1)[0]