"""Views package — keeps ``from apiApp import views`` and ``views.xxx`` working.

The old monolithic views.py was split by domain. URL patterns in urls.py
reference ``views.<function>``; every view function is re-exported here so
that module keeps working unchanged. This is a pure reorganization —
behavior is unchanged.
"""

from .common import (
    _build_token_response,
    _build_verification_link,
    _optimized_cart,
    _require_admin,
    _require_email_verified,
    error_response,
)
from .auth import (
    confirm_password_reset,
    get_me,
    get_user_details,
    login_user,
    register_user,
    request_password_reset,
    resend_verification_email,
    verify_email,
)
from .admin import (
    admin_delete_record,
    admin_delete_user,
    admin_list_orders,
    admin_list_users,
    admin_update_order,
    admin_update_record,
    admin_update_user,
)
from .catalog import (
    artist_create,
    artist_list,
    artist_search,
    genere_list,
    get_category_detail,
    get_category_list,
    record_create,
    record_detail,
    record_list,
)
from .cart import (
    add_to_cart,
    delete_cart,
    get_all_cart_items,
    get_all_carts,
    get_cart,
    remove_all_cart_items,
    remove_cart_item,
    update_cart_quantity,
)
from .wishlist import (
    add_to_wishlist,
    get_all_wishlists,
    get_wishlist,
    get_wishlist_count,
    remove_from_wishlist,
)
from .reviews import (
    add_review,
    delete_review,
    get_all_reviews,
    get_record_reviews,
    update_review,
)
from .search import record_search
from .shipping import (
    shipping_locations,
    shipping_quote,
)
from .checkout import (
    checkout_success,
    complete_checkout_session,
    create_stripe_checkout_session,
    stripe_webhook,
)
from .orders import get_user_orders
from .bazares import (
    admin_delete_bazar,
    admin_list_bazares,
    admin_update_bazar,
    bazar_create,
    bazar_list,
)
from .discogs import (
    discogs_release_detail,
    discogs_search,
)
from .config import maintenance_config

__all__ = [
    # common helpers (kept for any module importing them from apiApp.views)
    '_build_token_response',
    '_build_verification_link',
    '_optimized_cart',
    '_require_admin',
    '_require_email_verified',
    'error_response',
    # auth
    'confirm_password_reset',
    'get_me',
    'get_user_details',
    'login_user',
    'register_user',
    'request_password_reset',
    'resend_verification_email',
    'verify_email',
    # admin
    'admin_delete_record',
    'admin_delete_user',
    'admin_list_orders',
    'admin_list_users',
    'admin_update_order',
    'admin_update_record',
    'admin_update_user',
    # catalog
    'artist_create',
    'artist_list',
    'artist_search',
    'genere_list',
    'get_category_detail',
    'get_category_list',
    'record_create',
    'record_detail',
    'record_list',
    # cart
    'add_to_cart',
    'delete_cart',
    'get_all_cart_items',
    'get_all_carts',
    'get_cart',
    'remove_all_cart_items',
    'remove_cart_item',
    'update_cart_quantity',
    # wishlist
    'add_to_wishlist',
    'get_all_wishlists',
    'get_wishlist',
    'get_wishlist_count',
    'remove_from_wishlist',
    # reviews
    'add_review',
    'delete_review',
    'get_all_reviews',
    'get_record_reviews',
    'update_review',
    # search
    'record_search',
    # shipping
    'shipping_locations',
    'shipping_quote',
    # checkout
    'checkout_success',
    'complete_checkout_session',
    'create_stripe_checkout_session',
    'stripe_webhook',
    # orders
    'get_user_orders',
    # bazares
    'admin_delete_bazar',
    'admin_list_bazares',
    'admin_update_bazar',
    'bazar_create',
    'bazar_list',
    # discogs
    'discogs_release_detail',
    'discogs_search',
    # config
    'maintenance_config',
]