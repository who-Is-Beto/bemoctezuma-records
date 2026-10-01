"""Order and sale serializers."""
from rest_framework import serializers

from ..models import Order, OrderItem, Owner, Record, Sale, SaleItem
from .catalog import OwnerSerializer, RecordListSerializer


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


class SaleItemSerializer(serializers.ModelSerializer):
    """One sold record. Title/artist/cover are the sale-time snapshot, so the
    line reprints the same after the record changes or is deleted."""
    owner = OwnerSerializer(read_only=True)

    class Meta:
        model = SaleItem
        fields = [
            'id', 'record', 'title', 'artist', 'cover_image_url', 'owner',
            'quantity', 'price', 'commission_amount', 'email_sent',
        ]


class SaleSerializer(serializers.ModelSerializer):
    """One ticket: everything the printable receipt needs."""
    items = SaleItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = [
            'id', 'created_at', 'payment_method', 'commission_rate', 'commission_amount',
            'subtotal', 'final_sale_price', 'items',
        ]

    def get_subtotal(self, obj):
        return str(obj.final_sale_price + obj.commission_amount)


class SaleLineSerializer(serializers.Serializer):
    record = serializers.PrimaryKeyRelatedField(queryset=Record.objects.all())
    quantity = serializers.IntegerField(min_value=1)
    # Unit price; defaults to the record's current (discounted) price.
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    # Whose copy is sold: required when several owners have the record in stock.
    owner = serializers.PrimaryKeyRelatedField(queryset=Owner.objects.all(), required=False, allow_null=True)

    def validate(self, line):
        """Resolve ``owner``: the chosen one, the only one with stock, or None (store stock)."""
        with_stock = [row.owner for row in line['record'].owner_stock.select_related('owner') if row.quantity > 0]
        owner = line.get('owner')
        if owner is None:
            if len(with_stock) > 1:
                raise serializers.ValidationError(
                    {'owner': f'"{line["record"].title}" tiene varios dueños: elige de quién es el que vendes.'}
                )
            line['owner'] = with_stock[0] if with_stock else None
        elif owner not in with_stock:
            raise serializers.ValidationError(
                {'owner': f'{owner.name} no tiene "{line["record"].title}" en stock.'}
            )
        return line


class SaleCreateSerializer(serializers.Serializer):
    """Body of POST /sales/create/: one ticket with one or more records."""
    items = SaleLineSerializer(many=True, allow_empty=False)
    payment_method = serializers.ChoiceField(choices=Sale.PAYMENT_METHODS)
    # Card commission %; defaults to 4.06. Ignored (0) for cash and transfer.
    commission_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False,
    )


class SalesFilterSerializer(serializers.Serializer):
    """Optional query params of GET /sales/."""
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    owner = serializers.IntegerField(required=False)
