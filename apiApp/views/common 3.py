"""Shared view helpers (no DRF views, only building blocks).

Kept in their own module so the per-domain view submodules can import them
without creating import cycles. Everything here lived at the top of the old
single-file ``apiApp/views.py``.
"""
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.db.models import Value
from django.db.models.functions import Replace
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.text import slugify
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from ..models import Cart


def _build_token_response(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


def _build_verification_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.FRONTEND_URL.rstrip('/')}/verificar-correo?uid={uid}&token={token}"


def error_response(message, status_code=400, code="error", details=None):
    payload = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return Response(payload, status=status_code)


def _optimized_cart(cart):
    """Return a Cart queryset pre-fetched for CartSerializer serialization.

    Avoids N+1 queries: each CartItem -> Record -> Artist/Category/Genere is
    fetched in bulk.  Accepts a Cart instance or Cart queryset.
    """
    if isinstance(cart, Cart):
        return Cart.objects.filter(pk=cart.pk).prefetch_related(
            'cart_items__record__artist',
            'cart_items__record__category',
            'cart_items__record__genere',
        ).get(pk=cart.pk)
    return cart.prefetch_related(
        'cart_items__record__artist',
        'cart_items__record__category',
        'cart_items__record__genere',
    )


def _require_email_verified(request):
    """Block authenticated-but-unverified users from purchasing flows.

    Only fires when REQUIRE_EMAIL_VERIFICATION is enabled. Returns an error
    Response when the user must verify their email first, otherwise None.
    """
    if (
        settings.REQUIRE_EMAIL_VERIFICATION
        and request.user.is_authenticated
        and not request.user.email_verified
    ):
        return error_response(
            "Verifica tu correo para continuar. Revisa tu bandeja de entrada o pide un nuevo enlace desde tu perfil.",
            status_code=403,
            code="email_not_verified",
        )
    return None


def _require_admin(request):
    """Return an error Response if the user is not an admin, else None."""
    if request.user.role != "ADMIN":
        return error_response(
            "No tienes permiso para realizar esta acción.",
            status_code=403,
            code="forbidden",
        )
    return None


def _normalized_search_term(term):
    """Normalize a search term the same way slugs are generated.

    'Zoé' -> 'zoe', 'T. Rex' -> 'trex' — lets 'zoe' match records by 'Zoé'
    and 'trex' match 'T. Rex' by comparing against hyphen-stripped slugs.
    """
    return slugify(term or '').replace('-', '')


def _slug_contains(model, term):
    """Queryset of `model` whose slug, with hyphens stripped, contains term."""
    return model.objects.annotate(
        _norm_slug=Replace('slug', Value('-'), Value('')),
    ).filter(_norm_slug__icontains=term)


def _query_tokens(query):
    """Split a raw query into normalized, comparable tokens.

    'Pink  Floyd!' -> ['pink', 'floyd']; 'Zoé' -> ['zoe'].
    """
    tokens = (_normalized_search_term(word) for word in query.split())
    return [tok for tok in tokens if tok]
