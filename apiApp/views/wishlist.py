"""Wishlist views: add, remove, list and count wishlist items."""
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import error_response
from ..models import Record, User, Wishlist, WishlistItem
from ..serilizers import WishlistSerializer


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_to_wishlist(request):
    email = request.data.get('email')
    wishlist_code = request.data.get('wishlist_code')
    record_id = request.data.get('record_id')
    user = User.objects.get(email=email) if email else None
    
    if not record_id:
        return error_response("record_id is required", status_code=400, code="record_id_required")

    if wishlist_code:
        wishlist, _ = Wishlist.objects.get_or_create(wishlist_code=wishlist_code, defaults={'user': user})
    else:
        wishlist = Wishlist.objects.create(user=user)
    record = Record.objects.get(id=str(record_id))
    _, created = WishlistItem.objects.get_or_create(wishlist=wishlist, record=record)
    if not created:
        return Response({"message": "Record already in wishlist"}, status=200)
    serializer = WishlistSerializer(wishlist)
    return Response({"message": "Record added to wishlist", "wishlist": serializer.data}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_wishlists(_):
    wishlists = Wishlist.objects.all()
    serializer = WishlistSerializer(wishlists, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_wishlist(request, wishlist_code):
    email = request.query_params.get('email')
    user = User.objects.get(email=email) if email else None
    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    serializer = WishlistSerializer(wishlist)
    return Response(serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_from_wishlist(request):
    wishlist_code = request.data.get('wishlist_code')
    record_id = request.data.get('record_id')

    if not wishlist_code or not record_id:
        return error_response(
            "wishlist_code and record_id are required",
            status_code=400,
            code="wishlist_params_required",
        )

    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    try:
        wishlist_item = WishlistItem.objects.get(wishlist=wishlist, record_id=record_id)
    except WishlistItem.DoesNotExist:
        return error_response("Wishlist item not found", status_code=404, code="wishlist_item_not_found")

    wishlist_item.delete()
    wishlist.refresh_from_db()
    serializer = WishlistSerializer(wishlist)
    return Response({"message": "Record removed from wishlist", "wishlist": serializer.data}, status=200)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_wishlist_count(request):
    wishlist_code = request.query_params.get('wishlist_code') or request.data.get('wishlist_code')
    if not wishlist_code:
        return error_response("wishlist_code is required", status_code=400, code="wishlist_code_required")

    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    wishlist_count = wishlist.wishlist_items.count()
    return Response({"wishlist_count": wishlist_count}, status=200)