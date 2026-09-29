"""Services package — keeps ``from apiApp.services import ...`` working.

Business logic grouped by domain: emailing (order emails), shipping
(Envíos Perros), checkout (Stripe order fulfillment), search (tokenized
queries), discogs (release data shaping) and sales (owner emails for the
sales registered in Punto de venta). This is a pure reorganization —
behavior is unchanged.
"""

from .emailing import (
    _order_email_context,
    send_order_created_email,
    send_order_notification_email,
    send_order_shipped_email,
)
from .shipping import (
    CD_UNIT_WEIGHT_GRAMS,
    DEFAULT_UNIT_WEIGHT_GRAMS,
    MIN_PACKAGE_WEIGHT_KG,
    PACKAGE_TARE_GRAMS,
    PREFERRED_COURIER,
    SEVEN_INCH_UNIT_WEIGHT_GRAMS,
    ShippingQuoteError,
    _normalize_quote,
    build_package_from_cart,
    get_shipping_quotes,
    get_zip_locations,
    normalize_zip_code,
    record_unit_weight_grams,
    select_cheapest_quote,
)
from .checkout import (
    _resolve_cart_code_from_session,
    fulfill_checkout,
)
from .search import (
    _normalized_search_term,
    _query_tokens,
    _record_token_q,
    _slug_contains,
    search_artists,
    search_records,
)
from .discogs import (
    DiscogsServiceError,
    discogs_release_detail,
    discogs_search,
)
from .config import (
    get_maintenance_state,
    set_maintenance_state,
)
from .sales import commission_rate_for, notify_sale_owners, sales_metrics, split_commission

__all__ = [
    # emailing
    '_order_email_context',
    'send_order_created_email',
    'send_order_notification_email',
    'send_order_shipped_email',
    # shipping
    'CD_UNIT_WEIGHT_GRAMS',
    'DEFAULT_UNIT_WEIGHT_GRAMS',
    'MIN_PACKAGE_WEIGHT_KG',
    'PACKAGE_TARE_GRAMS',
    'PREFERRED_COURIER',
    'SEVEN_INCH_UNIT_WEIGHT_GRAMS',
    'ShippingQuoteError',
    '_normalize_quote',
    'build_package_from_cart',
    'get_shipping_quotes',
    'get_zip_locations',
    'normalize_zip_code',
    'record_unit_weight_grams',
    'select_cheapest_quote',
    # checkout
    '_resolve_cart_code_from_session',
    'fulfill_checkout',
    # search
    '_normalized_search_term',
    '_query_tokens',
    '_record_token_q',
    '_slug_contains',
    'search_artists',
    'search_records',
    # discogs
    'DiscogsServiceError',
    'discogs_release_detail',
    'discogs_search',
    # config
    'get_maintenance_state',
    'set_maintenance_state',
    # sales
    'commission_rate_for',
    'notify_sale_owners',
    'sales_metrics',
    'split_commission',
]