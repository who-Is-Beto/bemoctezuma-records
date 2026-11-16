"""Cart serializers."""
from rest_framework import serializers

from ..models import Cart, CartItem
from .catalog import RecordListSerializer


class CartItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)
    subtotal = serializers.SerializerMethodField()
    # How many people have this record in favorites right now (social proof in the cart).
    favorites_count = serializers.SerializerMethodField()
    class Meta:
        model = CartItem
        fields = ['id', 'record', 'quantity', 'subtotal', 'favorites_count']

    def get_favorites_count(self, cart_item):
        return cart_item.record.wishlist_records.count()

    def get_subtotal(self, cart_item):
        return cart_item.quantity * cart_item.record.effective_price


class CartSerializer(serializers.ModelSerializer):
    cart_items = CartItemSerializer(many=True, read_only=True)
    total_price = serializers.SerializerMethodField()
    class Meta:
        model = Cart
        fields = ['id', 'user', 'cart_code', 'created_at', 'updated_at', 'cart_items', 'total_price']

    def get_total_price(self, cart):
        total = sum(item.quantity * item.record.effective_price for item in cart.cart_items.all())
        return total


class CartStatSerializer(serializers.ModelSerializer):
    total_quantity = serializers.SerializerMethodField()
    class Meta:
        model = Cart
        fields = ['id', 'cart_code', 'total_quantity']

    def get_total_quantity(self, cart):
        total_quantity = sum(item.quantity for item in cart.cart_items.all())
        return total_quantity
