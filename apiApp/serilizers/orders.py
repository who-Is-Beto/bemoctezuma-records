"""Order serializers."""
from rest_framework import serializers

from ..models import Order, OrderItem
from .catalog import RecordListSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'record', 'quantity', 'price']


class OrderSerializer(serializers.ModelSerializer):
    order_items = OrderItemSerializer(many=True, read_only=True)
    pickup_bazar = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id',
            'stripe_checkout_session_id',
            'amount',
            'currency',
            'user_email',
            'shipped_to',
            'shipping_details',
            'shipping_cost',
            'shipping_courier',
            'shipping_service',
            'shipping_link',
            'pickup_bazar',
            'status',
            'created_at',
            'updated_at',
            'order_items',
        ]

    def get_pickup_bazar(self, obj):
        bazar = obj.pickup_bazar
        if bazar is None:
            return None
        return {
            'id': bazar.id,
            'name': bazar.name,
            'date': bazar.date.isoformat(),
            'schedule': bazar.schedule,
            'address': bazar.address,
            'google_maps_url': bazar.google_maps_url,
        }
