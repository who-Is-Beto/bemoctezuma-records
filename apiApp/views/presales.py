"""Preventas for customers: browse, quote shipping, reserve, settle, own orders,
and the announcement list. Money rules live in services/presales.py."""
import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.db.models import Prefetch
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_email_verified, error_response
from ..admin_panel import ACCESS
from ..models import PreSale, PreSaleOption, PreSaleOrder
from ..serilizers import PreSalePublicSerializer, presale_order_payload
from ..services import presales as svc
from ..services.shipping import ShippingQuoteError
from ..throttling import LiveScopedRateThrottle

logger = logging.getLogger(__name__)

ADDRESS_FIELDS = ['fullName', 'phone', 'street', 'number', 'neighborhood', 'city', 'state', 'zip']


def _presales_qs():
    return PreSale.objects.prefetch_related(
        Prefetch('options', queryset=PreSaleOption.objects.select_related('category'))
    )


def can_manage_presales(user):
    return bool(
        user and user.is_authenticated
        and (user.role == 'ADMIN' or (user.has_perm(f'apiApp.{ACCESS}') and user.has_perm('apiApp.tab_presales')))
    )


def presale_error(exc, status_code=400):
    return error_response(exc.message, status_code=status_code, code=exc.code)


@api_view(['GET'])
def presale_list(request):
    presales = _presales_qs().filter(status='published').order_by('purchase_deadline', 'id')
    return Response(PreSalePublicSerializer(presales, many=True, context={'request': request}).data)


@api_view(['GET'])
def presale_detail(request, slug):
    qs = _presales_qs()
    if not can_manage_presales(request.user):
        qs = qs.exclude(status='draft')
    presale = qs.filter(slug=slug).first()
    if presale is None:
        return error_response("Esta preventa no existe.", status_code=404, code="not_found")
    return Response(PreSalePublicSerializer(presale, context={'request': request}).data)


@api_view(['POST'])
@throttle_classes([LiveScopedRateThrottle])
def presale_shipping_estimate(request):
    option = PreSaleOption.objects.select_related('category').filter(pk=request.data.get('option_id')).first() \
        if str(request.data.get('option_id', '')).isdigit() else None
    if option is None:
        return error_response("Elige una opción de la preventa.", status_code=400, code="invalid_option")
    try:
        quote = svc.quote_shipping(option, request.data.get('zip'))
    except svc.PresaleError as exc:
        return presale_error(exc)
    except ShippingQuoteError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)
    return Response({
        'shipping_estimate': str(quote['cost']),
        'courier': quote['courier'],
        'service': quote['service'],
    })


presale_shipping_estimate.view_class.throttle_scope = 'presale_quote'


def _cents(amount):
    return int((Decimal(amount) * 100).quantize(Decimal('1')))


def _line(name, amount):
    return {'price_data': {'currency': 'mxn', 'product_data': {'name': name}, 'unit_amount': _cents(amount)},
            'quantity': 1}


def _checkout(order, *, purpose, item_amount, shipping_amount, item_label):
    presale = order.option.presale
    line_items = [_line(f"{presale.title} — {order.option.name} ({item_label})", item_amount)] if item_amount > 0 else []
    if shipping_amount > 0:
        line_items.append(_line("Envío", shipping_amount))
    frontend = settings.FRONTEND_URL.rstrip('/')
    return stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=line_items,
        mode='payment',
        locale='es',
        success_url=f"{frontend}/mis-ordenes?presale_session={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{frontend}/preventas/{presale.slug}" if purpose == 'reserve' else f"{frontend}/mis-ordenes",
        customer_email=order.user.email if order.user else None,
        client_reference_id=order.code,
        metadata={'kind': 'presale', 'order_id': str(order.id), 'purpose': purpose,
                  'shipping': str(Decimal(shipping_amount).quantize(svc.CENT))},
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def presale_reserve(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    data = request.data
    option_id = str(data.get('option_id', ''))
    option = PreSaleOption.objects.select_related('presale', 'category').filter(pk=option_id).first() \
        if option_id.isdigit() else None
    if option is None:
        return error_response("Elige una opción de la preventa.", status_code=400, code="invalid_option")
    delivery = data.get('delivery')
    if delivery not in ('pickup', 'shipping'):
        return error_response("Elige cómo quieres recibir tu disco.", status_code=400, code="invalid_delivery")
    if data.get('accept_terms') is not True:
        return presale_error(svc.PresaleError('terms_required'))

    details = None
    quote = None
    if delivery == 'shipping':
        details = data.get('shipping_details') or {}
        if not isinstance(details, dict):
            return error_response("La dirección no es válida.", status_code=400, code="invalid_shipping_details")
        missing = [k for k in ADDRESS_FIELDS if not str(details.get(k) or '').strip()]
        if missing:
            return error_response(f"Falta: {', '.join(missing)}", status_code=400, code="missing_shipping_fields")
        details = {k: str(details[k]).strip()[:200] for k in ADDRESS_FIELDS}
        try:
            quote = svc.quote_shipping(option, details['zip'])
        except svc.PresaleError as exc:
            return presale_error(exc)
        except ShippingQuoteError as exc:
            return error_response(exc.message, status_code=502, code=exc.code)
        details['zip'] = svc.shipping.normalize_zip_code(details['zip'])

    try:
        order = svc.create_order(
            option, buyer=request.user, source='online', delivery=delivery, shipping_details=details,
            amount=data.get('amount'), shipping_quote=quote['cost'] if quote else None, accept_terms=True,
        )
    except svc.PresaleError as exc:
        return presale_error(exc, status_code=409 if exc.code == 'sold_out' else 400)
    if quote:
        order.shipping_courier, order.shipping_service = quote['courier'], quote['service']
        order.save(update_fields=['shipping_courier', 'shipping_service'])

    shipping_now = order.shipping_cost or svc.ZERO
    # create_order already validated the amount; anything covering item_price is a full payment.
    amount = svc._money(data.get('amount'))
    full = amount >= order.item_price
    item_amount = order.item_price if full else amount
    try:
        session = _checkout(order, purpose='reserve', item_amount=item_amount, shipping_amount=shipping_now,
                            item_label='pago completo' if full else 'apartado')
    except Exception as exc:  # Stripe down: release the hold right away.
        logger.exception("Stripe session for presale %s failed", order.code)
        order.status = 'cancelado'
        order.save(update_fields=['status'])
        return error_response("No pudimos iniciar el pago. Intenta de nuevo.", status_code=502,
                              code="checkout_error", details=str(exc))
    return Response({'checkout_url': session.get('url'), 'code': order.code})


def _own_order(request, code):
    return PreSaleOrder.objects.select_related('option__presale').filter(
        code=code.upper(), user=request.user).exclude(status='pending_payment').first()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def presale_settle(request, code):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    order = _own_order(request, code)
    if order is None:
        return error_response("No encontramos esa preventa.", status_code=404, code="not_found")
    if order.status in ('cancelado', 'vencido', 'entregado', 'enviado'):
        return presale_error(svc.PresaleError('invalid_status'))
    due = svc.balance(order)
    if due <= 0:
        return presale_error(svc.PresaleError('nothing_due'))
    shipping_amount = svc.shipping_due(order)
    try:
        session = _checkout(order, purpose='settle', item_amount=due - shipping_amount,
                            shipping_amount=shipping_amount, item_label='liquidación')
    except Exception as exc:
        logger.exception("Stripe settle session for presale %s failed", order.code)
        return error_response("No pudimos iniciar el pago. Intenta de nuevo.", status_code=502,
                              code="checkout_error", details=str(exc))
    return Response({'checkout_url': session.get('url')})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def presale_checkout_complete(request):
    session_id = str(request.data.get('session_id') or '').strip()
    if not session_id:
        return error_response("session_id is required", status_code=400, code="missing_session_id")
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        return error_response("No pudimos confirmar el pago.", status_code=502, code="checkout_complete_error")
    metadata = session.get('metadata') or {}
    order = PreSaleOrder.objects.filter(pk=metadata.get('order_id'), user=request.user).first() \
        if metadata.get('kind') == 'presale' else None
    if order is None:
        return error_response("No encontramos esa preventa.", status_code=404, code="not_found")
    order = svc.fulfil_presale_session(session) or order
    order.refresh_from_db()
    return Response(presale_order_payload(order, request))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def presale_my_orders(request):
    orders = PreSaleOrder.objects.select_related('option__presale').prefetch_related('payments').filter(
        user=request.user).exclude(status='pending_payment')
    return Response([presale_order_payload(o, request) for o in orders])
