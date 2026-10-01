import json
import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.db import transaction

from ..models import Bazar, Cart, Order, OrderItem, Record
from .emailing import send_order_created_email, send_order_notification_email
from .inventory import take_stock

logger = logging.getLogger(__name__)

# Stripe's Python client reads this global; configured once at import time.
stripe.api_key = settings.STRIPE_SECRET_KEY


def _resolve_cart_code_from_session(session):
    metadata = session.get('metadata', {}) or {}
    cart_code = metadata.get('cart_code') or session.get('client_reference_id')
    if cart_code:
        return cart_code

    # Fallback for sessions created without metadata/client reference:
    # try matching the user's open cart by total amount.
    email = session.get('customer_email') or (session.get('customer_details') or {}).get('email')
    amount_total = session.get('amount_total')
    if not email:
        return None

    carts = Cart.objects.filter(user__email=email).prefetch_related('cart_items__record')
    # amount_total includes the shipping line item (when present), so subtract
    # it before comparing against product-only cart totals.
    metadata = session.get('metadata', {}) or {}
    try:
        shipping_cents = int(Decimal(metadata.get('shipping_cost') or '0') * 100)
    except Exception:
        shipping_cents = 0
    if amount_total is not None and shipping_cents:
        amount_total -= shipping_cents
    candidates = []

    for cart in carts:
        cents = 0
        for item in cart.cart_items.all():
            cents += int(item.record.effective_price * 100) * int(item.quantity)
        if amount_total is not None and cents == amount_total:
            candidates.append(cart.cart_code)

    if len(candidates) == 1:
        print(f">>> inferred cart_code from email+amount match: {candidates[0]}")
        return candidates[0]

    return None


def fulfill_checkout(session, cart_code=None):
    metadata = session.get('metadata', {}) or {}
    shipped_to = metadata.get('shipped_to', '')
    shipping_details = None
    raw_shipping = metadata.get('shipping_details')
    if raw_shipping:
        try:
            shipping_details = json.loads(raw_shipping) if isinstance(raw_shipping, str) else raw_shipping
        except Exception:
            shipping_details = None
    if isinstance(shipping_details, dict):
        # Address values are stored as plain strings for the admin orders view.
        shipping_details = {key: str(value) for key, value in shipping_details.items()}
    try:
        shipping_cost = Decimal(metadata.get('shipping_cost') or '0') or None
    except Exception:
        shipping_cost = None

    pickup_bazar = None
    raw_bazar_id = metadata.get('bazar_id')
    if raw_bazar_id:
        try:
            pickup_bazar = Bazar.objects.get(pk=raw_bazar_id)
        except (Bazar.DoesNotExist, ValueError, TypeError):
            pickup_bazar = None
            logger.warning("Checkout session %s referenced missing bazar %r", session.get('id'), raw_bazar_id)

    cart = None
    if cart_code:
        cart = Cart.objects.filter(cart_code=cart_code).prefetch_related('cart_items__record').first()

    customer_email = session.get('customer_email') or (session.get('customer_details') or {}).get('email')
    if not customer_email:
        customer_email = "unknown@checkout.local"

    with transaction.atomic():
        order = Order.objects.create(
            stripe_checkout_session_id=session['id'],
            amount=Decimal(session.get('amount_total') or 0) / Decimal(100),
            currency=session['currency'],
            user_email=customer_email,
            shipped_to=shipped_to,
            shipping_details=shipping_details,
            shipping_cost=shipping_cost,
            shipping_courier=metadata.get('shipping_courier', ''),
            shipping_service=metadata.get('shipping_service', ''),
            shipping_link="Preparando para envío" if shipped_to == "home" else "",
            pickup_bazar=pickup_bazar,
            status='paid',
        )

        created_items = False

        try:
            stripe_line_items = stripe.checkout.Session.list_line_items(
                session['id'], expand=['data.price.product']
            )

            for line_item in stripe_line_items.auto_paging_iter():
                product = line_item.get('price', {}).get('product')
                record_id = None
                if isinstance(product, dict):
                    record_id = product.get('metadata', {}).get('record_id')

                quantity = line_item.get('quantity') or 1
                price_cents = line_item.get('price', {}).get('unit_amount')
                if price_cents is None and quantity:
                    price_cents = (line_item.get('amount_total') or 0) // quantity
                price = Decimal(price_cents or 0) / Decimal(100)

                if record_id:
                    try:
                        record = Record.objects.get(id=record_id)
                    except Record.DoesNotExist:
                        continue

                    OrderItem.objects.create(
                        order=order,
                        record=record,
                        quantity=quantity,
                        price=price,
                    )
                    created_items = True
        except Exception:
            created_items = False

        if not created_items and cart:
            for item in cart.cart_items.all():
                OrderItem.objects.create(
                    order=order,
                    record=item.record,
                    quantity=item.quantity,
                    price=item.record.effective_price,
                )

        # Take stock per item (row-locked, never below zero), oldest owner
        # first; a line covered by several owners becomes one line per owner.
        for item in list(order.order_items.all()):
            splits = take_stock(item.record_id, item.quantity) if item.record_id else None
            if splits:
                (item.owner_id, item.quantity), *rest = splits
                item.save(update_fields=['owner', 'quantity'])
                for owner_id, quantity in rest:
                    OrderItem.objects.create(
                        order=order, record_id=item.record_id, owner_id=owner_id,
                        quantity=quantity, price=item.price,
                    )
            else:
                logger.warning(
                    "Insufficient stock for record %s (order %s): needed %s",
                    item.record_id,
                    order.id,
                    item.quantity,
                )

        if cart:
            cart.cart_items.all().delete()

    try:
        send_order_created_email(order)
    except Exception as exc:
        logger.warning("Order created but email notification failed for %s: %s", order.id, exc)

    try:
        send_order_notification_email(order)
    except Exception as exc:
        logger.warning("Order created but seller notification failed for %s: %s", order.id, exc)