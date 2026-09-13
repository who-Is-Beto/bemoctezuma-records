"""Site configuration views — the maintenance window."""

from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..services.config import get_maintenance_state, set_maintenance_state
from .common import error_response


@api_view(["GET", "PATCH"])
def maintenance_config(request):
    """GET: public maintenance status. PATCH: update the window (admin only).

    The GET side is deliberately public (no token): the storefront reads it at
    boot to decide between the site and the maintenance page, and it must keep
    working while the window is open (the middleware exempts /config/).
    """
    if request.method == "GET":
        maintenance_mode, maintenance_message = get_maintenance_state()
        return Response(
            {
                "maintenance_mode": maintenance_mode,
                "maintenance_message": maintenance_message,
            }
        )

    # PATCH — admins only. Manual check so GET can stay public on the same URL.
    if not request.user.is_authenticated or request.user.role != "ADMIN":
        return error_response(
            "No tienes permiso para realizar esta acción.",
            status_code=403,
            code="forbidden",
        )

    mode = request.data.get("maintenance_mode")
    message = request.data.get("maintenance_message", "")

    if not isinstance(mode, bool):
        return error_response(
            "maintenance_mode debe ser un booleano.",
            status_code=400,
            code="invalid_maintenance_mode",
        )
    if not isinstance(message, str):
        return error_response(
            "maintenance_message debe ser texto.",
            status_code=400,
            code="invalid_maintenance_message",
        )

    set_maintenance_state(mode, message)
    # Report the effective values: an empty custom message falls back to the
    # configured default so the client always mirrors what users will see.
    maintenance_mode, maintenance_message = get_maintenance_state()
    return Response(
        {
            "maintenance_mode": maintenance_mode,
            "maintenance_message": maintenance_message,
        }
    )