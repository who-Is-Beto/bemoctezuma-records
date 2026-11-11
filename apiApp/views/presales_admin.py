"""Administración → Preventas: presales, options, buyers, payments, delivery."""
import json
import logging
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_admin, error_response
from .presales import presale_error
from ..models import PreSale, PreSaleOption, PreSaleOrder, PreSaleOrderEvent, PreSalePayment
from ..serilizers import (
    PreSaleAdminSerializer,
    PreSaleOptionAdminSerializer,
    presale_admin_order_payload,
    presale_totals,
)
from ..services import presales as svc
from ..services.shipping import ShippingQuoteError

logger = logging.getLogger(__name__)

TAB = 'apiApp.tab_presales'
ADD_PRESALE = 'apiApp.add_presale'
CHANGE_PRESALE = 'apiApp.change_presale'
PAYMENTS = 'apiApp.add_presalepayment'
ORDERS = 'apiApp.change_presaleorder'

# Money fields an option keeps once someone bought it (existing orders snapshot
# their own price, but the presale page would contradict them).
LOCKED_OPTION_FIELDS = ('price', 'min_deposit', 'full_payment_discount')


def _orders_qs():
    return PreSaleOrder.objects.select_related(
        'option__presale', 'user', 'delivered_by'
    ).prefetch_related(
        Prefetch('payments', queryset=PreSalePayment.objects.select_related('registered_by', 'voided_by')),
        Prefetch('events', queryset=PreSaleOrderEvent.objects.select_related('actor')),
    )


def _order_response(request, order, status_code=200):
    order = _orders_qs().get(pk=order.pk)
    return Response(presale_admin_order_payload(order, request), status=status_code)


def _get_order(order_id):
    return _orders_qs().filter(pk=order_id).first()


def _not_found():
    return error_response("No encontramos esa preventa.", status_code=404, code="not_found")


def _parse_options(raw):
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or '[]')
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


# ── presales ───────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def admin_presales(request):
    if request.method == 'GET':
        denied = _require_admin(request, TAB)
        if denied:
            return denied
        presales = PreSale.objects.prefetch_related('options')
        return Response(PreSaleAdminSerializer(presales, many=True, context={'request': request}).data)

    denied = _require_admin(request, ADD_PRESALE)
    if denied:
        return denied
    options = _parse_options(request.data.get('options'))
    if not options:
        return error_response("Agrega al menos una opción.", status_code=400, code="options_required")
    data = {k: v for k, v in request.data.items() if k != 'options'}
    serializer = PreSaleAdminSerializer(data=data, context={'request': request})
    if not serializer.is_valid():
        return error_response("Revisa los datos de la preventa.", status_code=400, code="invalid_presale",
                              details=serializer.errors)
    option_serializers = []
    for position, option in enumerate(options):
        if not isinstance(option, dict):
            return error_response("Opción inválida.", status_code=400, code="invalid_option")
        option_serializer = PreSaleOptionAdminSerializer(data={'position': position, **option},
                                                         context={'request': request})
        if not option_serializer.is_valid():
            return error_response(f"Revisa la opción {position + 1}.", status_code=400, code="invalid_option",
                                  details=option_serializer.errors)
        option_serializers.append(option_serializer)
    with transaction.atomic():
        presale = serializer.save()
        for option_serializer in option_serializers:
            option_serializer.save(presale=presale)
    return Response(PreSaleAdminSerializer(presale, context={'request': request}).data, status=201)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def admin_presale_detail(request, presale_id):
    denied = _require_admin(request, TAB if request.method == 'GET' else CHANGE_PRESALE)
    if denied:
        return denied
    presale = PreSale.objects.filter(pk=presale_id).first()
    if presale is None:
        return _not_found()
    if request.method == 'GET':
        return Response(PreSaleAdminSerializer(presale, context={'request': request}).data)
    if request.method == 'DELETE':
        if PreSaleOrder.objects.filter(option__presale=presale).exists():
            return error_response("Esta preventa ya tiene compradores; ciérrala en lugar de borrarla.",
                                  status_code=400, code="presale_has_orders")
        presale.delete()
        return Response(status=204)
    data = {k: v for k, v in request.data.items() if k != 'options'}
    status_value = data.pop('status', None)
    serializer = PreSaleAdminSerializer(presale, data=data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return error_response("Revisa los datos de la preventa.", status_code=400, code="invalid_presale",
                              details=serializer.errors)
    presale = serializer.save()
    if status_value in ('draft', 'closed', 'published') and status_value != presale.status:
        # Publishing (and its announcement) goes through the publish endpoint.
        if status_value == 'published' and presale.announced_at is None:
            return error_response("Publica la preventa desde el botón Publicar.", status_code=400,
                                  code="use_publish")
        presale.status = status_value
        presale.save(update_fields=['status', 'updated_at'])
    return Response(PreSaleAdminSerializer(presale, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_presale_add_option(request, presale_id):
    denied = _require_admin(request, CHANGE_PRESALE)
    if denied:
        return denied
    presale = PreSale.objects.filter(pk=presale_id).first()
    if presale is None:
        return _not_found()
    data = {'position': presale.options.count(), **request.data.dict()} if hasattr(request.data, 'dict') \
        else {'position': presale.options.count(), **request.data}
    serializer = PreSaleOptionAdminSerializer(data=data, context={'request': request})
    if not serializer.is_valid():
        return error_response("Revisa la opción.", status_code=400, code="invalid_option", details=serializer.errors)
    serializer.save(presale=presale)
    return Response(serializer.data, status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def admin_presale_option(request, option_id):
    denied = _require_admin(request, CHANGE_PRESALE)
    if denied:
        return denied
    option = PreSaleOption.objects.filter(pk=option_id).first()
    if option is None:
        return _not_found()
    has_orders = option.orders.exists()
    if request.method == 'DELETE':
        if has_orders:
            return error_response("Esta opción ya tiene compradores; ocúltala en lugar de borrarla.",
                                  status_code=400, code="option_has_orders")
        option.delete()
        return Response(status=204)
    if has_orders and any(field in request.data for field in LOCKED_OPTION_FIELDS):
        return error_response("Esta opción ya tiene compradores: su precio, apartado y descuento no cambian.",
                              status_code=400, code="option_has_orders")
    if 'copies' in request.data:
        taken = option.copies - svc.copies_left(option)
        try:
            copies = int(request.data['copies'])
        except (TypeError, ValueError):
            copies = -1
        if copies < taken:
            return error_response(f"Ya hay {taken} copias apartadas; no puedes poner menos.",
                                  status_code=400, code="copies_below_taken")
    serializer = PreSaleOptionAdminSerializer(option, data=request.data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return error_response("Revisa la opción.", status_code=400, code="invalid_option", details=serializer.errors)
    serializer.save()
    return Response(serializer.data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def admin_presale_publish(request, presale_id):
    denied = _require_admin(request, CHANGE_PRESALE)
    if denied:
        return denied
    presale = PreSale.objects.filter(pk=presale_id).first()
    if presale is None:
        return _not_found()
    from ..services.emailing import announce_presale, presale_recipient_count
    if request.method == 'GET':
        return Response({'recipients': presale_recipient_count(), 'announced_at': presale.announced_at})
    if not presale.options.filter(is_active=True).exists():
        return error_response("Agrega al menos una opción activa.", status_code=400, code="options_required")
    presale.status = 'published'
    presale.save(update_fields=['status', 'updated_at'])
    recipients = announce_presale(presale) if request.data.get('announce') is True else 0
    return Response({'status': presale.status, 'recipients': recipients})


def _arrive(request, options):
    from ..services.emailing import send_presale_email
    for option in options:
        orders = svc.mark_arrived(option, by=request.user)
        for order in orders:
            order.refresh_from_db()
            if order.delivery == 'shipping' and order.shipping_cost is None:
                try:
                    svc.requote_shipping(order, by=request.user)
                except (svc.PresaleError, ShippingQuoteError) as exc:
                    logger.warning("Could not re-quote shipping for %s: %s", order.code, exc)
            send_presale_email(order, 'arrived')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_presale_option_arrived(request, option_id):
    denied = _require_admin(request, CHANGE_PRESALE)
    if denied:
        return denied
    option = PreSaleOption.objects.select_related('presale').filter(pk=option_id).first()
    if option is None:
        return _not_found()
    _arrive(request, [option])
    return Response(PreSaleAdminSerializer(option.presale, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_presale_arrived(request, presale_id):
    denied = _require_admin(request, CHANGE_PRESALE)
    if denied:
        return denied
    presale = PreSale.objects.filter(pk=presale_id).first()
    if presale is None:
        return _not_found()
    _arrive(request, list(presale.options.filter(arrived_at__isnull=True)))
    return Response(PreSaleAdminSerializer(presale, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_presale_extend(request, presale_id):
    denied = _require_admin(request, ORDERS)
    if denied:
        return denied
    try:
        until = date.fromisoformat(str(request.data.get('until')))
    except ValueError:
        return error_response("Elige una fecha válida.", status_code=400, code="invalid_date")
    orders = PreSaleOrder.objects.filter(option__presale_id=presale_id, status__in=('apartado', 'pagado', 'listo'))
    for order in orders:
        order.deadline_override = until
        order.save(update_fields=['deadline_override', 'updated_at'])
        PreSaleOrderEvent.objects.create(order=order, kind='deadline', note=f'Plazo extendido a {until}',
                                         actor=request.user)
    return Response({'updated': len(orders)})


# ── orders ─────────────────────────────────────────────────────────────────

ORDER_FILTERS = {
    'owes': lambda o: o.status in ('apartado', 'pagado') and (svc.balance(o) > 0 or svc.shipping_pending(o)),
    'ready': lambda o: o.status == 'listo' or (o.status == 'pagado' and o.delivery == 'shipping'
                                               and o.option.arrived_at and svc.balance(o) == 0
                                               and not svc.shipping_pending(o)),
    'expiring': lambda o: o.status in ('apartado', 'pagado') and o.option.arrived_at and svc.balance(o) > 0
    and 0 <= svc.days_left(o) <= 7,
    'expired': lambda o: o.status == 'vencido' or svc.is_expirable(o),
}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_presale_orders(request, presale_id):
    denied = _require_admin(request, TAB)
    if denied:
        return denied
    presale = PreSale.objects.filter(pk=presale_id).first()
    if presale is None:
        return _not_found()
    orders = list(_orders_qs().filter(option__presale=presale).exclude(status='pending_payment'))
    matches = ORDER_FILTERS.get(request.query_params.get('filter'))
    if matches:
        orders = [o for o in orders if matches(o)]
    return Response({
        'totals': presale_totals(presale),
        'orders': [presale_admin_order_payload(o, request) for o in orders],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_presale_create_order(request):
    denied = _require_admin(request, PAYMENTS)
    if denied:
        return denied
    data = request.data
    option_id = str(data.get('option_id', ''))
    option = PreSaleOption.objects.select_related('presale').filter(pk=option_id).first() if option_id.isdigit() else None
    if option is None:
        return error_response("Elige una opción.", status_code=400, code="invalid_option")
    name = str(data.get('buyer_name') or '').strip()
    if not name:
        return error_response("Escribe el nombre del cliente.", status_code=400, code="buyer_name_required")
    source = data.get('source') if data.get('source') in ('instagram', 'store', 'other') else 'store'
    method = data.get('method')
    if method not in ('cash', 'card', 'transfer'):
        return error_response("Elige cómo pagó.", status_code=400, code="invalid_method")
    delivery = data.get('delivery') if data.get('delivery') in ('pickup', 'shipping') else 'pickup'
    details = data.get('shipping_details') if delivery == 'shipping' and isinstance(data.get('shipping_details'), dict) else None
    email = str(data.get('buyer_email') or '').strip().lower()
    quote = None
    try:
        if delivery == 'shipping' and details and details.get('zip'):
            try:
                quote = svc.quote_shipping(option, details.get('zip'))['cost']
            except ShippingQuoteError:
                quote = None
        order = svc.create_order(
            option,
            buyer={'name': name, 'email': email, 'phone': str(data.get('buyer_phone') or '').strip()},
            source=source, delivery=delivery, shipping_details=details, amount=data.get('amount'),
            method=method, shipping_quote=quote, created_by=request.user,
            accept_terms=data.get('terms_accepted') is True, note=str(data.get('note') or ''),
        )
    except svc.PresaleError as exc:
        return presale_error(exc)
    if email:
        user = get_user_model().objects.filter(email__iexact=email, email_verified=True).first()
        if user:
            order.user = user
            order.save(update_fields=['user'])
    from ..services.emailing import send_presale_email
    send_presale_email(order, 'reserved')
    return _order_response(request, order, status_code=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_presale_order_by_code(request, code):
    denied = _require_admin(request, TAB)
    if denied:
        return denied
    code = code.strip().upper()
    if not code.startswith('PV-'):
        code = f'PV-{code}'
    order = _orders_qs().filter(code=code).exclude(status='pending_payment').first()
    if order is None:
        return _not_found()
    return Response(presale_admin_order_payload(order, request))


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def admin_presale_order(request, order_id):
    denied = _require_admin(request, TAB if request.method == 'GET' else ORDERS)
    if denied:
        return denied
    order = _get_order(order_id)
    if order is None:
        return _not_found()
    if request.method == 'GET':
        return Response(presale_admin_order_payload(order, request))
    data = request.data
    fields = []
    if 'deadline_override' in data:
        raw = data.get('deadline_override')
        try:
            order.deadline_override = date.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            return error_response("Elige una fecha válida.", status_code=400, code="invalid_date")
        fields.append('deadline_override')
    if 'delivery' in data:
        if data['delivery'] not in ('pickup', 'shipping'):
            return error_response("Entrega inválida.", status_code=400, code="invalid_delivery")
        if svc.shipping_paid(order) > 0:
            return error_response("El envío ya se cobró; no se puede cambiar la entrega.",
                                  status_code=400, code="shipping_already_paid")
        order.delivery = data['delivery']
        if order.delivery == 'pickup':
            order.shipping_cost = None
            order.shipping_estimate = None
        fields += ['delivery', 'shipping_cost', 'shipping_estimate']
    if 'shipping_details' in data and isinstance(data['shipping_details'], dict):
        order.shipping_details = {k: str(v)[:200] for k, v in data['shipping_details'].items()}
        fields.append('shipping_details')
    for key in ('buyer_name', 'buyer_email', 'buyer_phone'):
        if key in data:
            setattr(order, key, str(data[key] or '').strip()[:120])
            fields.append(key)
    if fields:
        order.save(update_fields=fields + ['updated_at'])
        PreSaleOrderEvent.objects.create(order=order, kind='status', note='Datos editados: ' + ', '.join(fields),
                                         actor=request.user)
        svc.refresh_status(order, request.user)
    return _order_response(request, order)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_presale_add_payment(request, order_id):
    denied = _require_admin(request, PAYMENTS)
    if denied:
        return denied
    order = _get_order(order_id)
    if order is None:
        return _not_found()
    method = request.data.get('method')
    if method not in ('cash', 'card', 'transfer'):
        return error_response("Elige cómo pagó.", status_code=400, code="invalid_method")
    if order.status in ('cancelado', 'vencido', 'entregado', 'enviado', 'pending_payment'):
        return presale_error(svc.PresaleError('invalid_status'))
    try:
        due_shipping = svc.shipping_due(order)
        amount = svc._money(request.data.get('amount'))
        # Store payments cover the record first; whatever exceeds it pays shipping.
        item_left = max(svc.balance(order) - due_shipping, svc.ZERO)
        shipping_part = min(max(amount - item_left, svc.ZERO), due_shipping)
        svc.record_payment(order, amount, method, note=str(request.data.get('note') or ''),
                           registered_by=request.user, shipping_amount=shipping_part)
    except svc.PresaleError as exc:
        return presale_error(exc)
    from ..services.emailing import send_presale_email
    order.refresh_from_db()
    send_presale_email(order, 'payment')
    return _order_response(request, order, status_code=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_presale_void_payment(request, payment_id):
    denied = _require_admin(request, PAYMENTS)
    if denied:
        return denied
    payment = PreSalePayment.objects.select_related('order').filter(pk=payment_id).first()
    if payment is None:
        return _not_found()
    try:
        svc.void_payment(payment, by=request.user, reason=request.data.get('reason'))
    except svc.PresaleError as exc:
        return presale_error(exc)
    return _order_response(request, payment.order)


def _order_action(action):
    @api_view(['POST'])
    @permission_classes([IsAuthenticated])
    def view(request, order_id):
        denied = _require_admin(request, ORDERS)
        if denied:
            return denied
        order = _get_order(order_id)
        if order is None:
            return _not_found()
        try:
            action(request, order)
        except svc.PresaleError as exc:
            return presale_error(exc)
        except ShippingQuoteError as exc:
            return error_response(exc.message, status_code=502, code=exc.code)
        return _order_response(request, order)
    return view


def _deliver(request, order):
    svc.deliver(order, by=request.user)
    from ..services.emailing import send_presale_email
    send_presale_email(order, 'delivered')


def _ship(request, order):
    svc.mark_shipped(order, tracking=request.data.get('tracking'), by=request.user)
    from ..services.emailing import send_presale_email
    send_presale_email(order, 'shipped')


admin_presale_deliver = _order_action(_deliver)
admin_presale_ship = _order_action(_ship)
admin_presale_cancel = _order_action(lambda r, o: svc.cancel(o, by=r.user, reason=r.data.get('reason')))
admin_presale_expire = _order_action(lambda r, o: svc.expire(o, by=r.user))
admin_presale_reopen = _order_action(lambda r, o: svc.reopen(o, by=r.user))
admin_presale_requote = _order_action(lambda r, o: svc.requote_shipping(o, by=r.user))
