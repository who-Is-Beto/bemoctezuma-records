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


class PreSaleOptionAdminSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    copies_left = serializers.SerializerMethodField()
    taken = serializers.SerializerMethodField()
    has_orders = serializers.SerializerMethodField()

    class Meta:
        model = PreSaleOption
        fields = ['id', 'name', 'image', 'image_url', 'category', 'weight_grams', 'price', 'min_deposit',
                  'full_payment_discount', 'cost_price', 'owner', 'copies', 'is_active', 'position',
                  'arrived_at', 'copies_left', 'taken', 'has_orders']
        read_only_fields = ['arrived_at']
        extra_kwargs = {'image': {'write_only': True, 'required': False}}

    def get_image_url(self, obj):
        return media_url(obj.image, self.context.get('request'))

    def get_copies_left(self, obj):
        return svc.copies_left(obj)

    def get_taken(self, obj):
        return obj.copies - svc.copies_left(obj)

    def get_has_orders(self, obj):
        return obj.orders.exists()

    def validate(self, attrs):
        instance = self.instance or PreSaleOption()
        for key, value in attrs.items():
            setattr(instance, key, value)
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs


class PreSaleAdminSerializer(serializers.ModelSerializer):
    cover_url = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    stats = serializers.SerializerMethodField()

    class Meta:
        model = PreSale
        fields = ['id', 'slug', 'title', 'artist', 'description', 'cover', 'cover_url', 'purchase_deadline',
                  'estimated_arrival', 'status', 'announced_at', 'created_at', 'options', 'stats']
        read_only_fields = ['slug', 'status', 'announced_at', 'created_at']
        extra_kwargs = {'cover': {'write_only': True, 'required': False}}

    def get_cover_url(self, obj):
        return media_url(obj.cover, self.context.get('request'))

    def get_options(self, obj):
        return PreSaleOptionAdminSerializer(obj.options.all(), many=True, context=self.context).data

    def get_stats(self, obj):
        return presale_totals(obj)


def presale_totals(presale):
    from ..models import PreSaleOrder
    orders = PreSaleOrder.objects.filter(option__presale=presale).exclude(status='pending_payment')
    reserved = paid_full = expired = 0
    collected = owed = svc.ZERO
    for order in orders.prefetch_related('payments').select_related('option'):
        paid = svc.paid_amount(order)
        if order.status in ('cancelado', 'vencido'):
            expired += order.status == 'vencido'
            collected += paid if order.status == 'vencido' else svc.ZERO
            continue
        reserved += 1
        collected += paid
        owed += svc.balance(order)
        paid_full += svc.balance(order) == 0 and not svc.shipping_pending(order)
    copies = sum(o.copies for o in presale.options.all())
    return {
        'copies': copies,
        'reserved': reserved,
        'paid_full': paid_full,
        'expired': expired,
        'collected': money(collected),
        'owed': money(owed),
    }


def presale_admin_order_payload(order, request=None):
    data = presale_order_payload(order, request)
    data.update({
        'id': order.id,
        'presale_id': order.option.presale_id,
        'buyer_name': order.buyer_name,
        'buyer_email': order.buyer_email or (order.user.email if order.user else ''),
        'buyer_phone': order.buyer_phone,
        'has_account': order.user_id is not None,
        'source': order.source,
        'source_label': order.get_source_display(),
        'shipping_details': order.shipping_details,
        'shipping_courier': order.shipping_courier,
        'deadline_override': order.deadline_override.isoformat() if order.deadline_override else None,
        'is_expirable': svc.is_expirable(order),
        'oversold': order.oversold,
        'delivered_by': order.delivered_by.get_username() if order.delivered_by else None,
        'payments': [
            {
                'id': p.id,
                'amount': money(p.amount),
                'method': p.method,
                'method_label': p.get_method_display(),
                'note': p.note,
                'created_at': p.created_at.isoformat(),
                'registered_by': p.registered_by.get_username() if p.registered_by else None,
                'voided': p.voided_at is not None,
                'void_reason': p.void_reason,
                'voided_by': p.voided_by.get_username() if p.voided_by else None,
            }
            for p in order.payments.all()
        ],
        'events': [
            {'kind': e.kind, 'note': e.note, 'actor': e.actor.get_username() if e.actor else None,
             'created_at': e.created_at.isoformat()}
            for e in order.events.all()
        ],
    })
    return data
