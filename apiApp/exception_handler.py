"""Custom DRF exception handler that normalizes DRF-internal error responses.

Throttling, authentication, and permission errors are wrapped into the same
{"error": {"code": ..., "message": ...}} envelope that our views use via
error_response(). Serializer validation errors (field-level) are left as-is
so frontends that already parse {"field": ["msg"]} keep working.

Error contract for the frontend:
  Throttle/auth errors → {"error": {"code": "...", "message": "..."}}
  Serializer errors    → {"field_name": ["msg"]}  (unchanged)
  error_response()     → {"error": {"code": "...", "message": "..."}}
"""

from rest_framework.exceptions import Throttled
from rest_framework.views import exception_handler


def normalized_exception_handler(exc, context):
    response = exception_handler(exc, context)

    # --- Unhandled exceptions (500s) ---
    if response is None:
        from rest_framework.response import Response
        return Response(
            {"error": {"code": "server_error", "message": "An unexpected error occurred."}},
            status=500,
        )

    # --- Throttled ---
    if isinstance(exc, Throttled):
        wait = int(exc.wait) if exc.wait else 60
        response.data = {
            "error": {
                "code": "throttled",
                "message": f"Demasiadas solicitudes. Intenta de nuevo en {wait} segundos.",
                "retry_after": wait,
            }
        }
        return response

    # --- {"detail": "..."} from JWT auth / permissions ---
    if (
        isinstance(response.data, dict)
        and "detail" in response.data
        and len(response.data) == 1
    ):
        detail = response.data["detail"]
        if isinstance(detail, str):
            code = _status_to_code(response.status_code)
            response.data = {
                "error": {
                    "code": code,
                    "message": detail,
                }
            }
        return response

    # Serializer field errors pass through unchanged:
    # {"field_name": ["msg"]} or {"non_field_errors": ["msg"]}
    return response


def _status_to_code(status_code):
    mapping = {
        400: "bad_request",
        401: "authentication_required",
        403: "permission_denied",
        404: "not_found",
        405: "method_not_allowed",
        429: "throttled",
    }
    return mapping.get(status_code, "error")
