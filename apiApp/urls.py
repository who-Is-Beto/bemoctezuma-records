from django.urls import path
from . import views

urlpatterns = [
    path('records/', views.record_list, name='records-list'),
    path('records/<slug:slug>/', views.record_detail, name='product-detail'),
    path('categories/', views.get_category_list, name='category-list'),
    path('categories/<slug:slug>/', views.get_category_detail, name='category-detail'),
    path('cart-items/', views.get_all_cart_items, name='get-all-cart-items'),
    path('carts/', views.get_all_carts, name='get-all-carts'),
    path('cart/add/', views.add_to_cart, name='add-to-cart'),
    path('cart/update/', views.update_cart_quantity, name='update-cart-quantity'),
    path('cart/remove/', views.remove_cart_item, name='remove-cart-item'),
    path('cart/remove-all/', views.remove_all_cart_items, name='remove-all-cart-items'),
    path('cart/delete/', views.delete_cart, name='delete-cart'),
    path('cart/<str:cart_code>/', views.get_cart, name='get-cart'),
    path('wishlists/add/', views.add_to_wishlist, name='add-to-wishlist'),
    path('wishlists/', views.get_all_wishlists, name='get-all-wishlists'),
    path('wishlists/<uuid:wishlist_code>/', views.get_wishlist, name='get-wishlist'),
    path('wishlists/remove/', views.remove_from_wishlist, name='remove-from-wishlist'),
    path('wishlists/count/', views.get_wishlist_count, name='get-wishlist-count'),
    path('reviews/add/',views.add_review, name='add-review'),
    path('reviews/update/', views.update_review, name='update-review'),
    path('reviews/delete/', views.delete_review, name='delete-review'),
    path('reviews/', views.get_all_reviews, name='get-all-reviews'),
    path('search/', views.record_search, name='record-search'),
    path('create-checkout-session/', views.create_stripe_checkout_session, name='create-checkout-session'),
    path('stripe-webhook/', views.stripe_webhook, name='stripe-webhook'),
]
