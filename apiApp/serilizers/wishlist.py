"""Wishlist serializers."""
from rest_framework import serializers

from ..models import WishlistItem
from .catalog import RecordListSerializer


class WishlistItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)
    # Everyone who has this record in favorites right now, this user included.
    favorites_count = serializers.SerializerMethodField()

    class Meta:
        model = WishlistItem
        fields = ['id', 'record', 'added_at', 'favorites_count']

    def get_favorites_count(self, item):
        return item.record.wishlist_records.count()

