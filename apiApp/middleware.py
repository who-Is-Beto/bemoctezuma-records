"""Request middleware — currently only the site-wide maintenance window."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from rest_framework_simplejwt.tokens import AccessToken

from .services.config import get_maintenance_state

User = get_user_model()

# Request paths that stay reachable while maintenance is active:
#  - pre-auth flows must keep working so an admin can log in and close the
#    window (login/refresh/register/verify/password reset);
#  - the /config/ namespace is the public maintenance-status endpoint plus the
#    admin-only PATCH that flips the window;
#  - the Stripe webhook is exempt so refunds/fulfillment are never dropped.
_BYPASS_PREFIXES = (
    "/auth/login/",
    "/auth/refresh/",
    "/auth/register/",
    "/auth/verify-email/",
    "/auth/verify-email/resend/",
    "/auth/password-reset/",
    "/auth/password-reset/confirm/",
    "/config/",
    "/stripe-webhook/",
)


class MaintenanceModeMiddleware:
    """503 every request while the maintenance window is open, except:

    - pre-auth and config endpoints (see ``_BYPASS_PREFIXES`` above)
    - any request carrying a valid ADMIN JWT (admins keep full access and
      are the ones who can flip the window back off)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        maintenance_on, message = get_maintenance_state()
        if maintenance_on and not self._is_exempt(request):
            return JsonResponse(
                {
                    "error": {
                        "code": "maintenance_mode",
                        "message": message or settings.MAINTENANCE_DEFAULT_MESSAGE,
                    }
                },
                status=503,
            )
        return self.get_response(request)

    def _is_exempt(self, request):
        if request.path.startswith(_BYPASS_PREFIXES):
            return True
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.lower().startswith("bearer "):
            return False
        try:
            token = AccessToken(header.split(" ", 1)[1])
        except Exception:
            return False
        user_id = token.get("user_id")
        if user_id is None:
            return False
        # Verify the token owner still holds admin rights (one indexed lookup;
        # only reached while the window is actually open).
        return User.objects.filter(pk=user_id, role="ADMIN").exists()