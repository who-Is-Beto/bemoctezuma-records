"""Custom throttles.

``ScopedRateThrottle.THROTTLE_RATES`` is a class attribute bound to
``api_settings.DEFAULT_THROTTLE_RATES`` at import time, so once the process
starts it never re-reads that setting.  That makes it impossible to change a
scope's rate at runtime via ``override_settings`` (e.g. in tests) — the stale,
frozen dict is always used.

``LiveScopedRateThrottle`` re-reads ``api_settings.DEFAULT_THROTTLE_RATES`` on
every ``get_rate()`` call, so rate changes take effect immediately.
"""
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle


class LiveScopedRateThrottle(ScopedRateThrottle):
    """ScopedRateThrottle that resolves its rates from the live settings.

    Fixes tests/scenarios that reassign ``REST_FRAMEWORK`` so a scope's rate
    is correctly applied (and enforced) on the very next request.
    """

    def get_rate(self):
        """Read the current scope's rate from the live DRF settings."""
        if not getattr(self, "scope", None):
            msg = ("You must set either `.scope` or `.rate` for '%s' throttle" % self.__class__.__name__)
            raise Exception(msg)

        throttle_rates = api_settings.DEFAULT_THROTTLE_RATES
        try:
            return throttle_rates[self.scope]
        except KeyError:
            top_percent = self.scope.split("/")[0]
            if top_percent in throttle_rates:
                return throttle_rates[top_percent]
            msg = ("No default throttle rate set for '%s' scope" % self.scope)
            raise Exception(msg)
