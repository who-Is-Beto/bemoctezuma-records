"""Checkout views: Stripe session creation, webhook, and order completion."""
import json
import logging
import time
from decimal import Decimal

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_email_verified, error_response
from ..models import Bazar, Cart, Order
from ..services import presales as presale_svc
from ..services.presales import fulfil_presale_session
from ..services import (
    PREFERRED_COURIER,
    ShippingQuoteError,
    _resolve_cart_code_from_session,
    build_package_from_cart,
    fulfill_checkout,
    get_shipping_quotes,
    normalize_zip_code,
    select_cheapest_quote,
)

logger = logging.getLogger(__name__)

endpoint_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None) or getattr(settings, "WEBHOOK_SECRET", None)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_stripe_checkout_session(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_code = request.data.get('cart_code')
    email = getattr(request.user, 'email', None)
    shipped_to = request.data.get('shipped_to')
    shipping_details = request.data.get('shippingDetails') or request.data.get('shipping_details')
    zip_code = None

    if not shipped_to:
        return error_response("shipped_to is required", status_code=400, code="missing_shipped_to")

    if shipped_to == "home":
        if not shipping_details:
            return error_response("shippingDetails is required when shipped_to is 'home'", status_code=400, code="missing_shipping_details")
        if isinstance(shipping_details, str):
            try:
                shipping_details = json.loads(shipping_details)
            except json.JSONDecodeError:
                return error_response("shippingDetails must be valid JSON", status_code=400, code="invalid_shipping_details")

        # minimal shape validation
        required_keys = ["fullName", "phone", "street", "number", "neighborhood", "city", "state", "zip"]
        missing = [k for k in required_keys if not shipping_details.get(k)]
        if missing:
            return error_response(f"shippingDetails missing: {', '.join(missing)}", status_code=400, code="missing_shipping_fields")

        zip_code = normalize_zip_code(shipping_details.get('zip'))
        if not zip_code:
            return error_response(
                "zip debe ser un código postal válido de 5 dígitos.",
                status_code=400,
                code="invalid_zip_code",
            )
        # Store the normalized (zero-padded, digits-only) ZIP so fulfillment
        # and the future label generation always see a clean value.
        shipping_details['zip'] = zip_code

    pickup_bazar = None
    if shipped_to == "bazar":
        bazar_id = request.data.get('bazar_id')
        if not bazar_id:
            return error_response(
                "bazar_id is required when shipped_to is 'bazar'",
                status_code=400,
                code="missing_bazar",
            )
        try:
            pickup_bazar = Bazar.objects.get(pk=bazar_id)
        except (Bazar.DoesNotExist, ValueError, TypeError):
            return error_response(
                "El bazar seleccionado no existe.",
                status_code=400,
                code="invalid_bazar",
            )
        if pickup_bazar.date < timezone.localdate():
            return error_response(
                "Ese bazar ya pasó. Elige uno próximo.",
                status_code=400,
                code="bazar_in_past",
            )

    if not email:
        return error_response("User email not found", status_code=400, code="user_email_missing")

    cart = Cart.objects.filter(cart_code=cart_code).prefetch_related('cart_items__record').first()

    if not cart or cart.cart_items.count() == 0:
        return error_response("Cart is empty or not found", status_code=400, code="cart_empty")

    # Presales picked on their pages can be paid in this same checkout.
    presale_req = request.data.get('presale') or None
    presale_lines = None
    if presale_req is not None:
        if not isinstance(presale_req, dict):
            return error_response("La preventa no es válida.", status_code=400, code="invalid_items")
        try:
            presale_lines = presale_svc.parse_items(presale_req.get('items'))
        except presale_svc.PresaleError as exc:
            return error_response(exc.message, status_code=400, code=exc.code)
        if presale_req.get('accept_terms') is not True:
            exc = presale_svc.PresaleError('terms_required')
            return error_response(exc.message, status_code=400, code=exc.code)
    # Home delivery with a presale: everything waits for the presale and goes in one
    # box (one fee), or the records ship now and the presale later (two fees).
    ship_together = bool(presale_lines) and shipped_to == "home" and presale_req.get('ship_together') is True

    # Home deliveries: re-quote server-side so the customer is charged the
    # real courier price — never an amount sent from the browser.
    shipping_line_item = None
    shipping_meta = {}
    if shipped_to == "home" and not ship_together:
        package = build_package_from_cart(cart)
        try:
            quotes = get_shipping_quotes(zip_code, package)
        except ShippingQuoteError as exc:
            return error_response(exc.message, status_code=502, code=exc.code)
        selected_quote = select_cheapest_quote(quotes)
        if selected_quote is None:
            return error_response(
                f"No hay opciones de envío con {PREFERRED_COURIER} para el código postal {zip_code}.",
                status_code=400,
                code="shipping_unavailable",
            )
        shipping_cost = Decimal(str(selected_quote.get('total', '0'))).quantize(Decimal('0.01'))
        shipping_meta = {
            'shipping_cost': str(shipping_cost),
            'shipping_courier': str(selected_quote.get('courier') or ''),
            'shipping_service': str(selected_quote.get('serviceType') or ''),
        }
        if shipping_cost > 0:
            shipping_line_item = {
                'price_data': {
                    'currency': 'mxn',
                    'product_data': {'name': f"Envío ({selected_quote.get('title')})"},
                    'unit_amount': int(shipping_cost * 100),
                },
                'quantity': 1,
            }

    presale_order = None
    try:
        line_items = []
        for item in cart.cart_items.all():
            if item.quantity > item.record.stock:
                return error_response(
                    f'No hay suficiente stock de "{item.record.title}" (disponible: {item.record.stock})',
                    status_code=400,
                    code="insufficient_stock",
                )
            line_items.append({
                'price_data': {
                    'currency': 'mxn',
                    'product_data': {
                        'name': item.record.title,
                        'metadata': {
                            'record_id': str(item.record.id),
                        },
                    },
                    'unit_amount': int(item.record.effective_price * 100),
                },
                'quantity': item.quantity,
            })

        if shipping_line_item:
            line_items.append(shipping_line_item)

        presale_meta = {}
        if presale_lines:
            try:
                presale_order, presale_items, presale_meta = _presale_part(
                    request.user, presale_lines, presale_req.get('amount'), shipped_to, shipping_details,
                    cart if ship_together else None)
            except presale_svc.PresaleError as exc:
                return error_response(exc.message, status_code=409 if exc.code in ('sold_out', 'limit_exceeded') else 400,
                                      code=exc.code)
            except ShippingQuoteError as exc:
                return error_response(exc.message, status_code=502, code=exc.code)
            line_items.extend(presale_items)

        metadata = {'cart_code': cart_code, 'shipped_to': shipped_to}
        metadata.update(presale_meta)
        if shipping_details:
            metadata['shipping_details'] = json.dumps(shipping_details)
        metadata.update(shipping_meta)
        if pickup_bazar is not None:
            # Only the id travels in metadata (Stripe metadata values are
            # strings); fulfillment re-resolves the Bazar from the DB.
            metadata['bazar_id'] = str(pickup_bazar.id)

        success_url = f"{settings.FRONTEND_URL.rstrip('/')}/mis-ordenes?session_id={{CHECKOUT_SESSION_ID}}"

        extra = {}
        if presale_order is not None:
            # The presale copies are held while the buyer pays; the session can't outlive the hold.
            extra['expires_at'] = int(time.time()) + presale_svc.CHECKOUT_MINUTES * 60
        checkout_session = stripe.checkout.Session.create(
            **extra,
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            locale='es',
            success_url=success_url,
            cancel_url=f"{settings.FRONTEND_URL.rstrip('/')}/carrito",
            customer_email=email,
            # Keep cart reference in two places to make webhook processing resilient.
            client_reference_id=cart_code,
            metadata=metadata,
        )
        print(
            f">>> checkout session created | id={checkout_session.get('id')} "
            f"cart_code={cart_code} client_reference_id={checkout_session.get('client_reference_id')}"
        )
        return Response(
            {
                "checkout_url": checkout_session.get("url"),
                "session_id": checkout_session.get("id"),
                # Backward compatibility for clients reading nested fields.
                "checkout_session": checkout_session,
            },
            status=200,
        )
    except Exception as e:
        if presale_order is not None:
            # Stripe down: release the presale hold right away.
            presale_order.status = 'cancelado'
            presale_order.save(update_fields=['status'])
        return error_response(str(e), status_code=500, code="checkout_error")


def _presale_part(user, lines, amount, shipped_to, shipping_details, together_cart):
    """Reserve the presale lines of a cart checkout: (order, Stripe lines, metadata).

    Pickup / bazar orders pick the presale up at the store. With ``together_cart``
    the shipping quote is for the presale and the cart records in one box.
    """
    from .presales import _line, _record_lines
    shipping = shipped_to == "home"
    quote = None
    if shipping:
        records = [(item.record, item.quantity) for item in together_cart.cart_items.all()] if together_cart else ()
        quote = presale_svc.quote_shipping(lines, shipping_details['zip'], records)
    order = presale_svc.create_order(
        lines, buyer=user, source='online', delivery='shipping' if shipping else 'pickup',
        shipping_details=dict(shipping_details) if shipping else None, amount=amount,
        shipping_quote=quote['cost'] if quote else None, accept_terms=True,
    )
    if quote:
        order.shipping_courier, order.shipping_service = quote['courier'], quote['service']
        order.save(update_fields=['shipping_courier', 'shipping_service'])
    amount = presale_svc._money(amount)
    full = amount >= order.item_price
    item_amount = order.item_price if full else amount
    shipping_now = order.shipping_cost or presale_svc.ZERO
    items = _record_lines(order, item_amount, 'pago completo' if full else 'apartado')
    if shipping_now > 0:
        items.append(_line("Envío (todo junto en una caja)" if together_cart else "Envío de la preventa", shipping_now))
    meta = {
        'kind': 'combined', 'order_id': str(order.id), 'purpose': 'reserve',
        'shipping': str(shipping_now.quantize(presale_svc.CENT)),
        'presale_total': str((item_amount + shipping_now).quantize(presale_svc.CENT)),
    }
    if together_cart:
        meta['ships_together'] = '1'
    return order, items, meta


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    print(
        f">>> stripe_webhook called | method={request.method} path={request.path} "
        f"content_length={len(payload) if payload is not None else 0} has_signature={bool(sig_header)}"
    )
    logger.info(
        "Stripe webhook hit",
        extra={
            "path": request.path,
            "content_length": len(payload) if payload is not None else 0,
            "has_signature": bool(sig_header),
        },
    )
    secrets_to_try = [
        getattr(settings, "STRIPE_WEBHOOK_SECRET", None),
        getattr(settings, "WEBHOOK_SECRET", None),
    ]
    secrets_to_try = [s for i, s in enumerate(secrets_to_try) if s and s not in secrets_to_try[:i]]

    event = None
    last_error = None

    for secret in secrets_to_try:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, secret
            )
            print(">>> webhook signature validated with configured secret")
            break
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            last_error = e
            logger.warning("Stripe webhook signature validation failed: %s", e)
            print(">>> webhook signature validation failed:", e)

    if event is None and settings.DEBUG:
        try:
            event = json.loads(payload.decode() if isinstance(payload, (bytes, bytearray)) else payload)
            logger.warning("Proceeding with unverified Stripe webhook payload because DEBUG=True.")
            print(">>> proceeding with unverified payload (DEBUG)")
        except Exception as e:
            last_error = e

    if event is None:
        if last_error:
            logger.error("Stripe webhook rejected: %s", last_error)
            print(">>> webhook rejected (400):", last_error)
        else:
            print(">>> webhook rejected (400): event could not be parsed")
        return HttpResponse(status=400)

    event_type = event.get('type')
    event_id = event.get('id')
    print(f">>> webhook event received | id={event_id} type={event_type}")
    if settings.DEBUG:
        logger.info("Stripe webhook event: %s | payload: %s", event_type, payload)

    if event_type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        session = event['data']['object']
        kind = (session.get('metadata') or {}).get('kind')
        if kind == 'presale':
            fulfil_presale_session(session)
            return HttpResponse(status=200)
        if kind == 'combined':
            fulfil_presale_session(session)  # idempotent; the records order follows below
        cart_code = _resolve_cart_code_from_session(session)
        session_id = session.get('id')
        print(f">>> checkout event | session_id={session_id} cart_code={cart_code}")
        logger.info(
            "Stripe checkout event",
            extra={
                "event_type": event_type,
                "session_id": session_id,
                "cart_code": cart_code,
            },
        )

        if session_id and Order.objects.filter(stripe_checkout_session_id=session_id).exists():
            logger.info("Order already created for this session.")
            print(f">>> order already exists for session {session_id} (200)")
            return HttpResponse(status=200)

        fulfill_checkout(session, cart_code)
        print(f">>> fulfill_checkout called for cart_code={cart_code} (200)")
    elif event_type == 'checkout.session.async_payment_failed':
        # TODO: notify the user or mark a pending order/cart as failed if needed.
        print(">>> async payment failed event received (200)")
        pass

    print(">>> webhook handled successfully (200)")
    return HttpResponse(status=200)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_checkout_session(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    session_id = (request.data.get('session_id') or '').strip()

    if not session_id:
        return error_response("session_id is required", status_code=400, code="missing_session_id")

    if Order.objects.filter(stripe_checkout_session_id=session_id).exists():
        return Response({"message": "Order already created for this session"}, status=200)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if (session.get('metadata') or {}).get('kind') == 'combined':
            fulfil_presale_session(session)
        cart_code = _resolve_cart_code_from_session(session)
        fulfill_checkout(session, cart_code)
        return Response({"message": "Order created successfully"}, status=200)
    except Exception as e:
        return error_response(str(e), status_code=500, code="checkout_complete_error")


@api_view(['GET'])
def checkout_success(request):
    session_id = request.query_params.get('session_id', '').strip()

    if not session_id:
        return error_response("session_id is required", status_code=400, code="missing_session_id")

    order = Order.objects.filter(stripe_checkout_session_id=session_id).first()

    if order:
        return Response({"message": "Payment confirmed ✅ / order created", "status": order.status}, status=200)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.get("payment_status") == "paid":
            if (session.get('metadata') or {}).get('kind') == 'combined':
                fulfil_presale_session(session)
            cart_code = _resolve_cart_code_from_session(session)
            fulfill_checkout(session, cart_code)
            order = Order.objects.filter(stripe_checkout_session_id=session_id).first()
            if order:
                return Response({"message": "Payment confirmed ✅ / order created", "status": order.status}, status=200)
    except Exception as exc:
        logger.warning("checkout_success fallback failed for session %s: %s", session_id, exc)

    return Response({"message": "We’re still confirming your payment… refresh in a moment", "status": "pending"}, status=200)