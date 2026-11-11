"""Presale payloads. Public ones never include cost_price / owner."""
from decimal import Decimal

from rest_framework import serializers

from ..models import PreSale, PreSaleOption
from ..services import presales as svc


def media_url(field, request):
    """Absolute URL for an ImageField (S3 already returns absolute URLs)."""
    if not field:
        return None
    try:
        url = field.url
    except ValueError:
        return None
    if url.startswith(('http://', 'https://')):
        return url
    return request.build_absolute_uri(url) if request else url


def money(value):
    return None if value is None else str(Decimal(value).quantize(svc.CENT))


class PreSaleOptionPublicSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    format = serializers.SerializerMethodField()
    copies_left = serializers.SerializerMethodField()
    arrived = serializers.SerializerMethodField()

    class Meta:
        model = PreSaleOption
        fields = ['id', 'name', 'image', 'format', 'price', 'min_deposit', 'full_payment_discount',
                  'copies', 'copies_left', 'arrived', 'is_active']

    def get_image(self, obj):
        return media_url(obj.image, self.context.get('request'))

    def get_format(self, obj):
        return obj.category.name if obj.category_id else ''

    def get_copies_left(self, obj):
        return max(svc.copies_left(obj), 0)

    def get_arrived(self, obj):
        return obj.arrived_at is not None


class PreSalePublicSerializer(serializers.ModelSerializer):
    cover = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    is_open = serializers.SerializerMethodField()
    price_from = serializers.SerializerMethodField()
    max_discount = serializers.SerializerMethodField()

    class Meta:
        model = PreSale
        fields = ['id', 'slug', 'title', 'artist', 'description', 'cover', 'purchase_deadline',
                  'estimated_arrival', 'status', 'is_open', 'price_from', 'max_discount', 'options']

    def _options(self, obj):
        return [o for o in obj.options.all() if o.is_active]

    def get_cover(self, obj):
        return media_url(obj.cover, self.context.get('request'))

    def get_options(self, obj):
        return PreSaleOptionPublicSerializer(self._options(obj), many=True, context=self.context).data

    def get_is_open(self, obj):
        return presale_is_open(obj)

    def get_price_from(self, obj):
        prices = [o.price for o in self._options(obj)]
        return money(min(prices)) if prices else None

    def get_max_discount(self, obj):
        discounts = [o.full_payment_discount for o in self._options(obj)]
        return money(max(discounts)) if discounts else money(0)


def presale_is_open(presale):
    from django.utils import timezone
    return (
        presale.status == 'published'
        and timezone.localdate() <= presale.purchase_deadline
        and any(o.is_active and svc.copies_left(o) > 0 for o in presale.options.all())
    )


def presale_order_payload(order, request=None):
    """What a customer sees about their own presale order."""
    option = order.option
    presale = option.presale
    paid = svc.paid_amount(order)
    return {
        'code': order.code,
        'status': order.status,
        'status_label': order.get_status_display(),
        'presale': {
            'slug': presale.slug,
            'title': presale.title,
            'artist': presale.artist,
            'cover': media_url(option.image or presale.cover, request),
            'estimated_arrival': presale.estimated_arrival,
        },
        'option': {'id': option.id, 'name': option.name},
        'delivery': order.delivery,
        'delivery_label': order.get_delivery_display(),
        'item_price': money(order.item_price),
        'discount_applied': money(order.discount_applied),
        'shipping_estimate': money(order.shipping_estimate),
        'shipping_cost': money(order.shipping_cost),
        'shipping_pending': svc.shipping_pending(order),
        'total': money(svc.total_due(order)),
        'paid': money(paid),
        'balance': money(svc.balance(order)),
        'deadline': svc.deadline(order).isoformat(),
        'days_left': svc.days_left(order),
        'arrived': option.arrived_at is not None,
        'payments': [
            {
                'id': p.id,
                'amount': money(p.amount),
                'method': p.method,
                'method_label': p.get_method_display(),
                'created_at': p.created_at.isoformat(),
                'voided': p.voided_at is not None,
            }
            for p in order.payments.all()
        ],
        'shipping_link': order.shipping_link,
        'reserved_at': order.reserved_at.isoformat(),
        'delivered_at': order.delivered_at.isoformat() if order.delivered_at else None,
    }
