import logging
import threading

from django.conf import settings
from django.core import signing

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

def send_record_request_email(data):
    """Forward an "Encargar un disco" request to the store, Reply-To the visitor.
    Returns False (and logs) when the email can't be sent."""
    try:
        send_email(
            template_name="record_request",
            context={**data, "frontend_url": settings.FRONTEND_URL},
            subject=f"Encargo: {data['title']} — {data['artist']}",
            to=settings.SELLER_NOTIFY_EMAILS,
            reply_to=[data['email']],
        )
        return True
    except Exception:
        logger.exception("Record request email failed for %s", data.get('email'))
        return False


# ── Preventas ──────────────────────────────────────────────────────────────

def _frontend(path):
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}"


def presale_unsubscribe_link(email):
    token = signing.dumps(email, salt='presale-unsubscribe')
    return _frontend(f"/preventas/baja?token={token}")


def send_presale_subscribe_confirmation(email):
    token = signing.dumps(email, salt='presale-subscribe')
    try:
        send_email(
            template_name="presale_subscribe_confirm",
            context={"confirm_link": _frontend(f"/preventas/confirmar?token={token}")},
            subject="Confirma tu suscripción a las preventas",
            to=[email],
        )
        return True
    except Exception:
        logger.exception("Presale subscription confirmation failed for %s", email)
        return False


def _subscribers():
    from ..models import PreSaleSubscriber
    return PreSaleSubscriber.objects.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True)


def presale_recipient_count():
    return _subscribers().count()


def _presale_cover(presale):
    if not presale.cover:
        return None
    try:
        url = presale.cover.url
    except ValueError:
        return None
    return url if url.startswith(('http://', 'https://')) else None


def _send_announcements(presale_id, emails):
    from ..models import PreSale
    presale = PreSale.objects.prefetch_related('options').get(pk=presale_id)
    options = [o for o in presale.options.all() if o.is_active]
    context = {
        "presale": presale,
        "options": options,
        "price_from": min((o.price for o in options), default=None),
        "max_discount": max((o.full_payment_discount for o in options), default=0),
        "min_deposit": min((o.min_deposit for o in options), default=None),
        "cover_url": _presale_cover(presale),
        "presale_link": _frontend(f"/preventas/{presale.slug}"),
    }
    sent = 0
    for email in emails:
        unsubscribe = presale_unsubscribe_link(email)
        try:
            send_email(
                template_name="presale_announcement",
                context={**context, "unsubscribe_link": unsubscribe},
                subject=f"Nueva preventa: {presale.title} — {presale.artist}",
                to=[email],
                headers={"List-Unsubscribe": f"<{unsubscribe}>"},
            )
            sent += 1
        except Exception:
            logger.exception("Presale announcement to %s failed", email)
    logger.info("Presale %s announced to %s/%s subscribers", presale_id, sent, len(emails))


def announce_presale(presale):
    """Email every confirmed subscriber once per presale. Returns how many will get it.

    Runs in a background thread (no task queue here) so publishing doesn't wait
    on one email per subscriber. ``announced_at`` is claimed first and atomically,
    so a double click can't announce twice.
    """
    from django.utils import timezone
    from ..models import PreSale
    claimed = PreSale.objects.filter(pk=presale.pk, announced_at__isnull=True).update(announced_at=timezone.now())
    if not claimed:
        return 0
    emails = list(_subscribers().values_list('email', flat=True))
    if emails:
        threading.Thread(target=_send_announcements, args=(presale.pk, emails), daemon=True).start()
    return len(emails)


def send_presale_email(order, kind):
    return False
