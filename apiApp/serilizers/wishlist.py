"""Wishlist serializers."""
from rest_framework import serializers

from ..models import WishlistItem
from .catalog import RecordListSerializer


class WishlistItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ['id', 'record', 'added_at']

