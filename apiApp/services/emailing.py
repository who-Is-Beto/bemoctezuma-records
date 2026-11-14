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


def _image_url(image):
    """Absolute URL of an uploaded image, or None (mail clients need http(s))."""
    if not image:
        return None
    try:
        url = image.url
    except ValueError:
        return None
    return url if url.startswith(('http://', 'https://')) else None


def _presale_cover(presale):
    return _image_url(presale.cover)


def _send_announcements(presale_id, emails):
    from ..models import PreSale
    from .presales import copies_left
    presale = PreSale.objects.prefetch_related('options').get(pk=presale_id)
    options = [o for o in presale.options.all() if o.is_active]
    cover_url = _presale_cover(presale)
    max_discount = max((o.full_payment_discount for o in options), default=0)
    context = {
        "presale": presale,
        "options": options,
        "versions": [
            {"name": o.name, "price": _money_str(o.price), "left": copies_left(o), "image_url": _image_url(o.image) or cover_url}
            for o in options
        ],
        "price_from": _money_str(min((o.price for o in options), default=0)),
        "max_discount": max_discount and _money_str(max_discount),
        "min_deposit": _money_str(min((o.min_deposit for o in options), default=0)),
        "cover_url": cover_url,
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


PRESALE_EMAILS = {
    # kind: (subject, headline, intro)
    'reserved': ("Tu preventa está apartada", "¡Listo, ya es tuyo!",
                 "Guardamos tu copia. Aquí está todo lo que necesitas saber."),
    'payment': ("Recibimos tu pago", "Recibimos tu pago",
                "Este es el estado de tu preventa."),
    'arrived': ("Tu disco ya llegó", "¡Ya llegó tu disco!", None),
    'reminder': ("Tu apartado vence pronto", "Tu apartado vence pronto", None),
    'shipped': ("Tu disco va en camino", "Tu disco va en camino",
                "Ya lo enviamos. Puedes seguir el paquete con la guía."),
    'delivered': ("Entregamos tu disco", "¡Disfrútalo!",
                  "Entregamos tu disco. Gracias por apoyar a la tienda."),
}


def presale_qr_png(code, scale=8):
    """QR for an order code: opens the code in Administración → Preventas."""
    import io
    import segno
    buffer = io.BytesIO()
    target = _frontend(f"/admin?tab=presales&code={code}")
    segno.make(target, error='m').save(buffer, kind='png', scale=scale, border=2)
    return buffer.getvalue()


def _qr_attachment(code):
    from email.mime.image import MIMEImage
    image = MIMEImage(presale_qr_png(code), _subtype='png')
    image.add_header('Content-ID', '<presale-qr>')
    image.add_header('Content-Disposition', 'inline', filename=f'{code}.png')
    return image


def _money_str(value):
    return f"${value:,.2f}"


def _presale_email_context(order, kind):
    from . import presales as svc
    option = order.option
    presale = option.presale
    balance = svc.balance(order)
    subject, headline, intro = PRESALE_EMAILS[kind]
    if kind == 'arrived':
        if balance > 0:
            intro = f"Liquida {_money_str(balance)} y es tuyo."
        elif order.delivery == 'shipping':
            intro = "Ya está pagado: lo preparamos para enviártelo."
        else:
            intro = "Ya está pagado: pasa por él a la tienda con tu código."
    if kind == 'reminder':
        intro = f"Te faltan {_money_str(balance)}. Tienes hasta el {svc.deadline(order).strftime('%d/%m/%Y')} para liquidar o pierdes tu apartado."
    total_due = svc.total_due(order)
    paid = svc.paid_amount(order)
    return {
        "subject": f"{subject} · {order.code}",
        "headline": headline,
        "intro": intro,
        "code": order.code,
        "title": presale.title,
        "artist": presale.artist,
        "option": option.name,
        "cover_url": _image_url(option.image or presale.cover),
        "total": _money_str(total_due),
        "paid": _money_str(paid),
        "paid_percent": min(int(paid * 100 / total_due), 100) if total_due > 0 else 100,
        "balance": _money_str(balance),
        "owes": balance > 0,
        "shipping_pending": svc.shipping_pending(order),
        "deadline": svc.deadline(order).strftime('%d/%m/%Y'),
        "arrived": option.arrived_at is not None,
        "delivery_label": order.get_delivery_display(),
        "tracking": order.shipping_link,
        "orders_link": _frontend("/mis-ordenes"),
        "show_code": kind not in ('delivered',),
    }


def send_presale_email(order, kind):
    """Email the buyer about their presale order. Never raises; False when not sent."""
    to = (order.user.email if order.user_id else '') or order.buyer_email
    if not to:
        return False
    try:
        context = _presale_email_context(order, kind)
        send_email(
            template_name="presale_order",
            context=context,
            subject=context["subject"],
            to=[to],
            attachments=[_qr_attachment(order.code)] if context["show_code"] else None,
        )
        return True
    except Exception:
        logger.exception("Presale %s email failed for order %s", kind, order.code)
        return False


def send_presale_store_notice(order, kind):
    """Tell the store about a new online reservation (or an oversold one)."""
    recipients = list(settings.SELLER_NOTIFY_EMAILS)
    if not recipients:
        return False
    from . import presales as svc
    title = {
        'oversold': "Sobreventa en preventa",
        'overpaid': "Pago de más en preventa",
    }.get(kind, "Nueva preventa apartada")
    try:
        send_email(
            template_name="presale_store_notice",
            context={
                "title": title,
                "oversold": kind == 'oversold',
                "overpaid": kind == 'overpaid',
                "order": order,
                "presale": order.option.presale,
                "option": order.option,
                "paid": _money_str(svc.paid_amount(order)),
                "balance": _money_str(svc.balance(order)),
                "admin_link": _frontend(f"/admin?tab=presales&code={order.code}"),
            },
            subject=f"{title}: {order.option.presale.title} · {order.code}",
            to=recipients,
        )
        return True
    except Exception:
        logger.exception("Presale store notice failed for %s", order.code)
        return False
