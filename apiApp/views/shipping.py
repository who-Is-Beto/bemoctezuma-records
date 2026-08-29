"""Shipping views: Envíos Perros quote and Sepomex locations."""
import re
from decimal import Decimal

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_email_verified, error_response
from ..models import Cart
from ..services import (
    PREFERRED_COURIER,
    ShippingQuoteError,
    build_package_from_cart,
    get_shipping_quotes,
    get_zip_locations,
    normalize_zip_code,
    select_cheapest_quote,
)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def shipping_quote(request):
    """Quote home-delivery cost for a cart via Envíos Perros.

    Body: {"cart_code": "...", "zip": "15400"}
    Returns the cheapest Estafeta option (store policy) plus the full quote
    list so the frontend can display alternatives later if needed.
    """
    blocked = _require_email_verified(request)
    if blocked:
        return blocked

    cart_code = request.data.get('cart_code')
    zip_code = normalize_zip_code(request.data.get('zip'))
    if not zip_code:
        return error_response(
            "zip debe ser un código postal válido de 5 dígitos.",
            status_code=400,
            code="invalid_zip_code",
        )

    cart = Cart.objects.filter(cart_code=cart_code).prefetch_related('cart_items__record__category').first()
    if not cart or cart.cart_items.count() == 0:
        return error_response("Cart is empty or not found", status_code=400, code="cart_empty")

    package = build_package_from_cart(cart)
    try:
        quotes = get_shipping_quotes(zip_code, package)
    except ShippingQuoteError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)

    selected = select_cheapest_quote(quotes)
    if selected is None:
        return error_response(
            f"No hay opciones de envío con {PREFERRED_COURIER} para el código postal {zip_code}.",
            status_code=404,
            code="shipping_unavailable",
        )

    subtotal = sum(
        (item.record.effective_price * item.quantity for item in cart.cart_items.all()),
        Decimal('0'),
    )
    return Response({
        'zip_code': zip_code,
        'package': package,
        'subtotal': subtotal,
        'currency': selected.get('currency', 'MXN'),
        'selected': {
            'title': selected.get('title'),
            'total': selected.get('total'),
            'currency': selected.get('currency', 'MXN'),
            'courier': selected.get('courier'),
            'serviceType': selected.get('serviceType'),
            'deliveryCommitment': selected.get('deliveryCommitment'),
        },
        'quotes': quotes,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def shipping_locations(request):
    """List the Sepomex colonias for a ZIP code (Envíos Perros /locations).

    One ZIP can cover several colonias and label generation requires the exact
    Sepomex name, so the checkout address form offers these as a dropdown.
    Query: ?zip=06700 (4-5 digits, per the upstream autocomplete contract).
    """
    blocked = _require_email_verified(request)
    if blocked:
        return blocked

    raw = (request.query_params.get('zip') or '').strip()
    if not re.fullmatch(r'\d{4,5}', raw):
        return error_response(
            "zip debe tener 4 o 5 dígitos.",
            status_code=400,
            code="invalid_zip_code",
        )
    zip_code = normalize_zip_code(raw) if len(raw) == 5 else raw

    try:
        locations = get_zip_locations(zip_code)
    except ShippingQuoteError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)

    return Response({'zip': zip_code, 'locations': locations})