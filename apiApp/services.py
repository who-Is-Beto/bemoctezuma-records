from django.conf import settings
from .emails import send_email
import logging

logger = logging.getLogger(__name__)


def _order_email_context(order):
    """Build the template context for the order-created email."""
    amount_str = f"${order.amount:.2f} {order.currency.upper()}"
    shipped_label = "Enviado a domicilio" if order.shipped_to.lower() == "home" else order.shipped_to
    tracking = order.ship_link or "Preparando para envío"
    orders_link = f"{settings.FRONTEND_URL.rstrip('/')}/mis-ordenes"
    items = [
        {
            "title": getattr(item.record, "title", "Artículo"),
            "quantity": item.quantity,
            "price_str": f"${item.price:.2f} {order.currency.upper()}",
            "image_url": getattr(item.record, "cover_image_url", None),
        }
        for item in order.order_items.select_related("record")
    ]
    return {
        "order_id": order.id,
        "amount_str": amount_str,
        "shipped_label": shipped_label,
        "tracking": tracking,
        "orders_link": orders_link,
        "items": items,
        "shipping": order.shipping_details or {},
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
