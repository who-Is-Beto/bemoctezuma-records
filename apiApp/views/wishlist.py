"""Wishlist ("Favoritos"): each user saves records to their own list.

Every endpoint acts only on request.user's wishlist: there is no way to read
or change someone else's. The list is created on the first save.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import error_response
from ..models import Record, Wishlist, WishlistItem
from ..serilizers import WishlistItemSerializer


def _wishlist_of(user, create=False):
    # ponytail: "the oldest list" instead of a unique constraint, so legacy
    # rows from the old endpoints need no migration; a double-tap race at
    # most leaves a second, unused list.
    wishlist = Wishlist.objects.filter(user=user).order_by('id').first()
    if wishlist is None and create:
        wishlist = Wishlist.objects.create(user=user)
    return wishlist


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def wishlist_detail(request):
    """The user's saved records, newest first: {count, items: [{id, record, added_at}]}."""
    wishlist = _wishlist_of(request.user)
    items = (
        wishlist.wishlist_items
        .select_related('record__artist', 'record__category')
        .prefetch_related('record__generes')
        .order_by('-added_at', '-id')
        if wishlist else WishlistItem.objects.none()
    )
    data = WishlistItemSerializer(items, many=True).data
    return Response({'count': len(data), 'items': data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def wishlist_ids(request):
    """Just the saved record ids, so every card can show its heart filled or not."""
    wishlist = _wishlist_of(request.user)
    ids = list(wishlist.wishlist_items.values_list('record_id', flat=True)) if wishlist else []
    return Response({'record_ids': ids})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def wishlist_add(request):
    """Save {record_id}. 201 when added, 200 when it was already saved."""
    try:
        record_id = int(request.data.get('record_id'))
    except (TypeError, ValueError):
        return error_response("record_id is required", status_code=400, code="record_id_required")
    record = Record.objects.filter(pk=record_id).first()
    if record is None:
        return error_response("Ese disco ya no existe.", status_code=404, code="record_not_found")

    _, created = WishlistItem.objects.get_or_create(wishlist=_wishlist_of(request.user, create=True), record=record)
    return Response({'record_id': record.id, 'saved': True}, status=201 if created else 200)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def wishlist_remove(request, record_id):
    """Unsave a record. Idempotent: removing one that isn't saved is not an error."""
    wishlist = _wishlist_of(request.user)
    if wishlist:
        wishlist.wishlist_items.filter(record_id=record_id).delete()
    return Response(status=204)
