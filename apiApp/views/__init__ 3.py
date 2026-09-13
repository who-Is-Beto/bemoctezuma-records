"""Re-exports every view function from its per-domain submodule.

Keeps the import surface identical to the previous single-file
``apiApp/views.py`` so ``from . import views`` (used by ``apiApp/urls.py`` and
by tests that patch ``apiApp.views.<name>``) keeps working unchanged.
"""
from .common import (
    _build_token_response,
    _build_verification_link,
    _normalized_search_term,
    _optimized_cart,
    _query_tokens,
    _require_admin,
    _require_email_verified,
    _slug_contains,
    error_response,
)
from .auth import (
    register_user,
    login_user,
    request_password_reset,
    confirm_password_reset,
    verify_email,
    resend_verification_email,
    get_me,
    get_user_details,
    admin_list_users,
    admin_update_user,
    admin_delete_user,
)
from .catalog import (
    admin_update_record,
    admin_delete_record,
    record_list,
    record_create,
    artist_list,
    artist_search,
    artist_create,
    genere_list,
    record_detail,
    get_category_list,
    get_category_detail,
)
from .orders import (
    admin_list_orders,
    admin_update_order,
    get_user_orders,
)
from .cart import (
    get_cart,
    get_all_carts,
    get_all_cart_items,
    add_to_cart,
    update_cart_quantity,
    remove_cart_item,
    remove_all_cart_items,
    delete_cart,
)
from .wishlist import (
    add_to_wishlist,
    get_all_wishlists,
    get_wishlist,
    remove_from_wishlist,
    get_wishlist_count,
)
from .reviews import (
    add_review,
    update_review,
    delete_review,
    get_record_reviews,
    get_all_reviews,
)
from .search import (
    _record_token_q,
    record_search,
)
from .shipping import (
    shipping_quote,
    shipping_locations,
)
from .checkout import (
    create_stripe_checkout_session,
    stripe_webhook,
    complete_checkout_session,
    checkout_success,
)
from .discogs import (
    discogs_search,
    discogs_release_detail,
)
from .bazares import (
    bazar_list,
    admin_list_bazares,
    bazar_create,
    admin_update_bazar,
    admin_delete_bazar,
)

__all__ = [
    '_build_token_response',
    '_build_verification_link',
    '_normalized_search_term',
    '_optimized_cart',
    '_query_tokens',
    '_require_admin',
    '_require_email_verified',
    '_slug_contains',
    'error_response',
    'register_user',
    'login_user',
    'request_password_reset',
    'confirm_password_reset',
    'verify_email',
    'resend_verification_email',
    'get_me',
    'get_user_details',
    'admin_list_users',
    'admin_update_user',
    'admin_delete_user',
    'admin_update_record',
    'admin_delete_record',
    'record_list',
    'record_create',
    'artist_list',
    'artist_search',
    'artist_create',
    'genere_list',
    'record_detail',
    'get_category_list',
    'get_category_detail',
    'admin_list_orders',
    'admin_update_order',
    'get_user_orders',
    'get_cart',
    'get_all_carts',
    'get_all_cart_items',
    'add_to_cart',
    'update_cart_quantity',
    'remove_cart_item',
    'remove_all_cart_items',
    'delete_cart',
    'add_to_wishlist',
    'get_all_wishlists',
    'get_wishlist',
    'remove_from_wishlist',
    'get_wishlist_count',
    'add_review',
    'update_review',
    'delete_review',
    'get_record_reviews',
    'get_all_reviews',
    '_record_token_q',
    'record_search',
    'shipping_quote',
    'shipping_locations',
    'create_stripe_checkout_session',
    'stripe_webhook',
    'complete_checkout_session',
    'checkout_success',
    'discogs_search',
    'discogs_release_detail',
    'bazar_list',
    'admin_list_bazares',
    'bazar_create',
    'admin_update_bazar',
    'admin_delete_bazar',
]
