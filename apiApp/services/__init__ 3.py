"""Re-exports all service functions/constants from their domain submodules.

Keeps the public import surface identical to the previous single-file
``apiApp/services.py`` so ``from apiApp.services import ...`` (used across
views and tests) keeps working unchanged.
"""
from .emailing import (
    _order_email_context,
    send_order_created_email,
    send_order_shipped_email,
    send_order_notification_email,
)
from .shipping import (
    PACKAGE_TARE_GRAMS,
    DEFAULT_UNIT_WEIGHT_GRAMS,
    CD_UNIT_WEIGHT_GRAMS,
    SEVEN_INCH_UNIT_WEIGHT_GRAMS,
    BOX_DEPTH_CM,
    BOX_WIDTH_CM,
    BOX_HEIGHT_PER_UNIT_CM,
    BOX_MAX_HEIGHT_CM,
    MIN_PACKAGE_WEIGHT_KG,
    PREFERRED_COURIER,
    ShippingQuoteError,
    normalize_zip_code,
    record_unit_weight_grams,
    build_package_from_cart,
    _normalize_quote,
    get_zip_locations,
    get_shipping_quotes,
    select_cheapest_quote,
)
from .checkout import fulfill_checkout, _resolve_cart_code_from_session

__all__ = [
    '_order_email_context',
    'send_order_created_email',
    'send_order_shipped_email',
    'send_order_notification_email',
    'PACKAGE_TARE_GRAMS',
    'DEFAULT_UNIT_WEIGHT_GRAMS',
    'CD_UNIT_WEIGHT_GRAMS',
    'SEVEN_INCH_UNIT_WEIGHT_GRAMS',
    'BOX_DEPTH_CM',
    'BOX_WIDTH_CM',
    'BOX_HEIGHT_PER_UNIT_CM',
    'BOX_MAX_HEIGHT_CM',
    'MIN_PACKAGE_WEIGHT_KG',
    'PREFERRED_COURIER',
    'ShippingQuoteError',
    'normalize_zip_code',
    'record_unit_weight_grams',
    'build_package_from_cart',
    '_normalize_quote',
    'get_zip_locations',
    'get_shipping_quotes',
    'select_cheapest_quote',
    'fulfill_checkout',
    '_resolve_cart_code_from_session',
]
