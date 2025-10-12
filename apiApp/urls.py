from django.urls import path
from .views import (
    record_list,
    record_detail,
    get_category_list,
    get_category_detail,
    add_to_cart,
    update_cart_quantity,
    remove_cart_item,
    get_cart,
    get_all_carts,
    get_all_cart_items,
    remove_all_cart_items
)

urlpatterns = [
    path('records/', record_list, name='records-list'),
    path('records/<slug:slug>/', record_detail, name='product-detail'),
    path('categories/', get_category_list, name='category-list'),
    path('categories/<slug:slug>/', get_category_detail, name='category-detail'),
    path('cart-items/', get_all_cart_items, name='get-all-cart-items'),
    path('carts/', get_all_carts, name='get-all-carts'),
    path('cart/add/', add_to_cart, name='add-to-cart'),
    path('cart/update/', update_cart_quantity, name='update-cart-quantity'),
    path('cart/remove/', remove_cart_item, name='remove-cart-item'),
    path('cart/remove-all/', remove_all_cart_items, name='remove-all-cart-items'),
    path('cart/<str:cart_code>/', get_cart, name='get-cart'),
]
