"""Preventas for customers: browse, quote shipping, reserve, settle, own orders,
and the announcement list. Money rules live in services/presales.py."""
import logging
import re
import time
from decimal import Decimal

import stripe
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.db.models import Prefetch
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_email_verified, error_response
from ..admin_panel import ACCESS
from ..models import PreSale, PreSaleOption, PreSaleOrder, PreSaleSubscriber
from ..serilizers import PreSalePublicSerializer, presale_order_payload
from ..services import presales as svc
from ..services.emailing import send_presale_subscribe_confirmation
from ..services.shipping import ShippingQuoteError
from ..throttling import LiveScopedRateThrottle

logger = logging.getLogger(__name__)

ADDRESS_FIELDS = ['fullName', 'phone', 'street', 'number', 'neighborhood', 'city', 'state', 'zip']
OPTIONAL_ADDRESS_FIELDS = ['reference']


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
    try:
        quote = svc.quote_shipping(svc.parse_items(request.data.get('items')), request.data.get('zip'))
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


def _record_lines(order, item_amount, item_label):
    """Stripe lines for the records: one per version on a reservation (``item_amount``
    spread by each line's share, cents rounded, the remainder on the last), one on a settle."""
    if item_amount <= 0:
        return []
    items = list(order.items.all())
    if item_label == 'liquidación' or len(items) == 1:
        label = f"{items[0].option.name} ×{items[0].quantity}" if len(items) == 1 else svc.lines_label(order)
        title = svc.order_title(order)
        return [_line(f"{title} — {label} ({item_label})", item_amount)]
    total = sum((item.line_total for item in items), Decimal('0'))
    lines, given = [], Decimal('0')
    for index, item in enumerate(items):
        share = item_amount - given if index == len(items) - 1 else \
            (Decimal(item_amount) * item.line_total / total).quantize(svc.CENT)
        given += share
        lines.append(_line(f"{item.option.presale.title} — {item.option.name} ×{item.quantity} ({item_label})", share))
    return lines


def _checkout(order, *, purpose, item_amount, shipping_amount, item_label):
    presales = svc.order_presales(order)
    line_items = _record_lines(order, item_amount, item_label)
    if shipping_amount > 0:
        line_items.append(_line("Envío", shipping_amount))
    frontend = settings.FRONTEND_URL.rstrip('/')
    return stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=line_items,
        mode='payment',
        locale='es',
        success_url=f"{frontend}/mis-ordenes?presale_session={{CHECKOUT_SESSION_ID}}",
        cancel_url=(f"{frontend}/preventas/{presales[-1].slug}" if purpose == 'reserve' else f"{frontend}/mis-ordenes"),
        customer_email=order.user.email if order.user else None,
        client_reference_id=order.code,
        expires_at=int(time.time()) + svc.CHECKOUT_MINUTES * 60,
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
    try:
        lines = svc.parse_items(data.get('items'))
    except svc.PresaleError as exc:
        return presale_error(exc)
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
        details = {k: str(details.get(k) or '').strip()[:200] for k in ADDRESS_FIELDS + OPTIONAL_ADDRESS_FIELDS}
        try:
            quote = svc.quote_shipping(lines, details['zip'])
        except svc.PresaleError as exc:
            return presale_error(exc)
        except ShippingQuoteError as exc:
            return error_response(exc.message, status_code=502, code=exc.code)
        details['zip'] = svc.shipping.normalize_zip_code(details['zip'])

    try:
        order = svc.create_order(
            lines, buyer=request.user, source='online', delivery=delivery, shipping_details=details,
            amount=data.get('amount'), shipping_quote=quote['cost'] if quote else None, accept_terms=True,
        )
    except svc.PresaleError as exc:
        return presale_error(exc, status_code=409 if exc.code in ('sold_out', 'limit_exceeded') else 400)
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
    return PreSaleOrder.objects.prefetch_related('items__option__presale').filter(
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
    try:
        item_amount, shipping_amount = svc.settle_split(order, request.data.get('amount'))
    except svc.PresaleError as exc:
        return presale_error(exc)
    label = 'liquidación' if item_amount + shipping_amount == due else 'abono'
    try:
        session = _checkout(order, purpose='settle', item_amount=item_amount,
                            shipping_amount=shipping_amount, item_label=label)
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
    orders = PreSaleOrder.objects.prefetch_related('payments', 'items__option__presale').filter(
        user=request.user).exclude(status='pending_payment')
    return Response([presale_order_payload(o, request) for o in orders])


# ── announcement list ──────────────────────────────────────────────────────

SUBSCRIBE_OK = {"message": "Revisa tu correo para confirmar."}
CONFIRM_MAX_AGE = 7 * 24 * 3600


@api_view(['POST'])
@throttle_classes([LiveScopedRateThrottle])
def presale_subscribe(request):
    if str(request.data.get('website') or '').strip():
        return Response(SUBSCRIBE_OK)  # bot: pretend it worked
    email = str(request.data.get('email') or '').strip().lower()
    try:
        validate_email(email)
    except DjangoValidationError:
        return error_response("Escribe un correo válido.", status_code=400, code="invalid_email")
    subscriber, _ = PreSaleSubscriber.objects.get_or_create(email=email)
    if subscriber.confirmed_at is None or subscriber.unsubscribed_at is not None:
        # Same answer either way: the response never tells whether an email is subscribed.
        send_presale_subscribe_confirmation(email)
    return Response(SUBSCRIBE_OK)


presale_subscribe.view_class.throttle_scope = 'presale_subscribe'


def _email_from_token(token, salt, max_age=None):
    try:
        return signing.loads(str(token or ''), salt=salt, max_age=max_age)
    except signing.BadSignature:
        return None


@api_view(['POST'])
def presale_subscribe_confirm(request):
    email = _email_from_token(request.data.get('token'), 'presale-subscribe', CONFIRM_MAX_AGE)
    if not email:
        return error_response("Este enlace ya no es válido. Suscríbete de nuevo.", status_code=400, code="invalid_token")
    subscriber, _ = PreSaleSubscriber.objects.get_or_create(email=email)
    subscriber.confirmed_at = timezone.now()
    subscriber.unsubscribed_at = None
    subscriber.save(update_fields=['confirmed_at', 'unsubscribed_at'])
    return Response({"subscribed": True})


@api_view(['POST'])
def presale_unsubscribe(request):
    email = _email_from_token(request.data.get('token'), 'presale-unsubscribe')
    if not email:
        return error_response("Este enlace no es válido.", status_code=400, code="invalid_token")
    PreSaleSubscriber.objects.filter(email=email).update(unsubscribed_at=timezone.now())
    return Response({"subscribed": False})


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def presale_subscription(request):
    email = (request.user.email or '').strip().lower()
    subscriber = PreSaleSubscriber.objects.filter(email=email).first()
    if request.method == 'PATCH' and email:
        if request.data.get('subscribed') is True:
            subscriber = subscriber or PreSaleSubscriber(email=email)
            subscriber.user = request.user
            subscriber.confirmed_at = subscriber.confirmed_at or timezone.now()
            subscriber.unsubscribed_at = None
            subscriber.save()
        elif subscriber:
            subscriber.unsubscribed_at = timezone.now()
            subscriber.save(update_fields=['unsubscribed_at'])
    subscribed = bool(subscriber and subscriber.confirmed_at and subscriber.unsubscribed_at is None)
    return Response({'subscribed': subscribed})


CODE_RE = re.compile(r'^PV-[A-HJ-NP-Z2-9]{4}$')


@api_view(['GET'])
@throttle_classes([LiveScopedRateThrottle])
def presale_order_qr(request, code):
    """PNG QR of an order code. Drawn for any well-formed code, so it never
    reveals which codes exist (the code is what releases the record)."""
    from django.http import HttpResponse
    from ..services.emailing import presale_qr_png
    code = code.upper()
    if not CODE_RE.match(code):
        return error_response("Código inválido.", status_code=400, code="invalid_code")
    response = HttpResponse(presale_qr_png(code), content_type='image/png')
    response['Cache-Control'] = 'private, max-age=86400'
    return response


presale_order_qr.view_class.throttle_scope = 'presale_qr'
