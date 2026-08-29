import logging

from django.conf import settings

from ..emails import send_email

logger = logging.getLogger(__name__)


def _order_email_context(order):
    """Build the template context for the order-created email."""
    amount_str = f"${order.amount:.2f} {order.currency.upper()}"
    _shipped_labels = {
        "home": "Enviado a domicilio",
        "bazar": "Recoges en bazar",
        "store": "Recoges en tienda",
    }
    shipped_label = _shipped_labels.get(order.shipped_to.lower(), order.shipped_to)
    tracking = order.shipping_link or "Preparando para envío"
    orders_link = f"{settings.FRONTEND_URL.rstrip('/')}/mis-ordenes"

    # Bazar pickup details (shipped_to == 'bazar'): shown to the customer
    # and to the store so both know where/when the hand-off happens.
    pickup_bazar = None
    bazar = order.pickup_bazar
    if bazar is not None:
        try:
            date_str = bazar.date.strftime("%d/%m/%Y")
        except Exception:
            date_str = str(bazar.date)
        pickup_bazar = {
            "name": bazar.name,
            "date_str": date_str,
            "schedule": bazar.schedule,
            "address": bazar.address,
            "google_maps_url": bazar.google_maps_url,
        }

    items = []
    for item in order.order_items.select_related("record"):
        record = item.record
        original_price = getattr(record, "price", item.price)
        discount_pct = getattr(record, "discount_porcentage", 0) or 0
        line_total = item.price * item.quantity
        original_total = original_price * item.quantity
        items.append({
            "title": getattr(record, "title", "Artículo"),
            "quantity": item.quantity,
            "price_str": f"${item.price:.2f} {order.currency.upper()}",
            "image_url": getattr(record, "cover_image_url", None),
            "original_price_str": f"${original_price:.2f} {order.currency.upper()}" if discount_pct > 0 else None,
            "discount_pct": discount_pct if discount_pct > 0 else None,
            "line_total_str": f"${line_total:.2f} {order.currency.upper()}",
            "original_total_str": f"${original_total:.2f} {order.currency.upper()}" if discount_pct > 0 else None,
        })
    return {
        "order_id": order.id,
        "amount_str": amount_str,
        "shipped_label": shipped_label,
        "tracking": tracking,
        "orders_link": orders_link,
        "items": items,
        "shipping": order.shipping_details or {},
        "pickup_bazar": pickup_bazar,
        "customer_email": order.user_email,
        "frontend_url": settings.FRONTEND_URL,
    }


def send_order_created_email(order):
    """
    Notify the user that an order was created. Uses the configured email backend.
    Falls back to logging errors so order creation is not blocked.
    """
    subject = f"Tu orden #{order.id} fue creada"
    try:
        context = _order_email_context(order)
        send_email(
            template_name="order_created",
            context=context,
            subject=subject,
            to=[order.user_email],
        )
        logger.info("Order creation email sent for order %s", order.id)
    except Exception as exc:
        logger.warning("Failed to send order creation email for order %s: %s", order.id, exc)


def send_order_shipped_email(order):
    """
    Notify the customer that their order has shipped, including the courier
    and tracking link when available. Never raises — admin flows must not
    fail because of email problems.
    """
    subject = f"Tu pedido #{order.id} va en camino 📦"
    try:
        context = _order_email_context(order)
        link = (order.shipping_link or "").strip()
        context["courier_service"] = " ".join(
            part for part in [order.shipping_courier, order.shipping_service] if part
        ) or "Paquetería"
        # Only offer a clickable button for real URLs; plain codes stay text.
        context["tracking_url"] = link if link.lower().startswith(("http://", "https://")) else ""
        send_email(
            template_name="order_shipped",
            context=context,
            subject=subject,
            to=[order.user_email],
        )
        logger.info("Order shipped email sent for order %s", order.id)
    except Exception as exc:
        logger.warning("Failed to send order shipped email for order %s: %s", order.id, exc)


def send_order_notification_email(order):
    """
    Notify the store (SELLER_NOTIFY_EMAILS) that a customer placed a paid order
    so it can be prepared/shipped. Never raises — the webhook must not fail.
    """
    recipients = list(settings.SELLER_NOTIFY_EMAILS)
    if not recipients:
        logger.info("No SELLER_NOTIFY_EMAILS configured; skipping seller notification for order %s", order.id)
        return
    subject = f"🛒 Nueva orden #{order.id} — {order.user_email}"
    try:
        context = _order_email_context(order)
        send_email(
            template_name="order_notification",
            context=context,
            subject=subject,
            to=recipients,
            reply_to=[order.user_email],
        )
        logger.info("Seller notification sent for order %s to %s", order.id, recipients)
    except Exception as exc:
        logger.warning("Failed to send seller notification for order %s: %s", order.id, exc)