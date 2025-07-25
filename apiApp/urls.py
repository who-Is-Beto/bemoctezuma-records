from django.urls import path
from .views import record_list, record_detail, get_category_list, get_category_detail

urlpatterns = [
    path('records/', record_list, name='records-list'),
    path('records/<slug:slug>/', record_detail, name='product-detail'),
    path('categories/', get_category_list, name='category-list'),
    path('categories/<slug:slug>/', get_category_detail, name='category-detail'), 
]