"""Sales registered in Punto de venta: card commission, owner notifications
and the Ventas metrics (point of sale + online orders)."""
import logging
from collections import defaultdict
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

from ..emails import send_email
from ..models import Order, Sale

logger = logging.getLogger(__name__)

CENT = Decimal('0.01')
ZERO = Decimal('0.00')
CARD_COMMISSION_RATE = Decimal('4.06')


def _money(amount):
    return f"${amount:.2f} MXN"


def commission_rate_for(payment_method, requested_rate=None):
    """Card pays the requested % (default 4.06); cash and transfer never pay commission."""
    if payment_method != 'card':
        return ZERO
    return CARD_COMMISSION_RATE if requested_rate is None else requested_rate


def split_commission(subtotals, rate):
    """Commission on the whole ticket and each line's share of it.

    The commission is ``rate`` % of the sum of ``subtotals``, rounded half-up to
    cents. Shares are proportional to each subtotal; the cents lost when
    flooring go to the largest remainders, so shares are never negative and
    always add up to the commission exactly.
    """
    total = sum(subtotals, ZERO)
    commission = (total * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    if not total:
        return commission, [ZERO for _ in subtotals]
    exact = [commission * subtotal / total for subtotal in subtotals]
    shares = [share.quantize(CENT, rounding=ROUND_DOWN) for share in exact]
    leftover_cents = int((commission - sum(shares, ZERO)) / CENT)
    by_remainder = sorted(range(len(shares)), key=lambda i: exact[i] - shares[i], reverse=True)
    for i in by_remainder[:leftover_cents]:
        shares[i] += CENT
    return commission, shares


def notify_sale_owners(sale):
    """Email each owner once, listing only their records from ``sale``.

    Marks those items ``email_sent``. Never raises: a record without owner or
    a failed email becomes a log line plus a warning for the admin UI, so the
    already-saved sale is never affected.
    """
    warnings = []
    by_owner = defaultdict(list)
    for item in sale.items.select_related('owner'):
        if item.owner is None:
            logger.warning("Sale %s: record %s has no owner, no email sent", sale.id, item.record_id)
            warnings.append(f'"{item.title or "(disco eliminado)"}" no tiene dueño asignado; no se envió correo.')
        else:
            by_owner[item.owner].append(item)

    sold_at = timezone.localtime(sale.created_at).strftime("%d/%m/%Y %H:%M")
    for owner, items in by_owner.items():
        gross = sum((item.price * item.quantity for item in items), ZERO)
        commission = sum((item.commission_amount for item in items), ZERO)
        try:
            send_email(
                template_name="owner_sale",
                context={
                    "owner_name": owner.name,
                    "sale_id": sale.id,
                    "sold_at": sold_at,
                    "items": [{
                        "title": item.title,
                        "artist": item.artist,
                        "quantity": item.quantity,
                        "price_str": _money(item.price),
                        "subtotal_str": _money(item.price * item.quantity),
                    } for item in items],
                    "payment_method": sale.get_payment_method_display(),
                    "has_commission": commission > 0,
                    "commission_rate": f"{sale.commission_rate:.2f}",
                    "gross_str": _money(gross),
                    "commission_str": _money(commission),
                    "total_str": _money(gross - commission),
                    "frontend_url": settings.FRONTEND_URL,
                },
                subject=f"Vendimos {'tus discos' if len(items) > 1 else 'tu disco'} 🎶 — Venta #{sale.id}",
                to=[owner.email],
            )
        except Exception:
            logger.exception("Sale %s: email to owner %s failed", sale.id, owner.email)
            warnings.append(f"No se pudo enviar el correo a {owner.name} ({owner.email}).")
            continue
        sale.items.filter(owner=owner).update(email_sent=True)
    return warnings


# ── Ventas metrics ───────────────────────────────────────────────────────

# Online orders that count as sales (pending never got paid; canceled was undone).
ONLINE_SALE_STATUSES = ('paid', 'shipped', 'delivered')


def _cents(amount):
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def estimated_stripe_fee(amount):
    """Stripe doesn't report its fee back to us, so estimate it from settings:
    (amount × STRIPE_FEE_PERCENT + STRIPE_FEE_FIXED) × (1 + STRIPE_FEE_VAT)."""
    if amount <= 0:
        return ZERO
    fee = amount * settings.STRIPE_FEE_PERCENT / 100 + settings.STRIPE_FEE_FIXED
    return _cents(fee * (1 + settings.STRIPE_FEE_VAT / 100))


def _bucket():
    return {'count': 0, 'units': 0, 'gross': ZERO, 'commission': ZERO}


def _add(bucket, gross, commission, count=1, units=0):
    bucket['count'] += count
    bucket['units'] += units
    bucket['gross'] += gross
    bucket['commission'] += commission


def _money_fields(bucket):
    gross, commission, count = bucket['gross'], bucket['commission'], bucket['count']
    return {
        'count': count,
        'units': bucket['units'],
        'gross': str(_cents(gross)),
        'commission': str(_cents(commission)),
        'net': str(_cents(gross - commission)),
        'average_ticket': str(_cents(gross / count) if count else ZERO),
    }


def sales_metrics(date_from=None, date_to=None):
    """Ventas → Métricas: point-of-sale tickets plus paid online orders.

    Gross is what the records sold for (online excludes shipping, which is
    reported apart). Commission is the stored card commission for the point
    of sale and an *estimated* Stripe fee for online orders; online owners
    get the fee share of their lines, the shipping share stays with the store.
    Online owner = the record's current owner (orders don't snapshot it).
    """
    sales = Sale.objects.prefetch_related('items__owner')
    orders = Order.objects.filter(status__in=ONLINE_SALE_STATUSES).prefetch_related(
        'order_items__record__artist', 'order_items__record__owner'
    )
    # __date converts to TIME_ZONE: store days, like the Ventas history.
    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)
        orders = orders.filter(created_at__date__lte=date_to)

    channels = {'pos': _bucket(), 'online': _bucket()}
    methods = defaultdict(_bucket)
    records = {}
    owners = {}

    def add_record(record_id, title, artist, cover, quantity, gross):
        key = record_id or f'deleted:{title}'
        row = records.setdefault(key, {
            'record': record_id, 'title': title, 'artist': artist,
            'cover_image_url': cover, 'units': 0, 'gross': ZERO,
        })
        row['units'] += quantity
        row['gross'] += gross

    def add_owner(owner, quantity, gross, commission):
        key = owner.id if owner else None
        if key not in owners:
            owners[key] = {'owner': {'id': owner.id, 'name': owner.name} if owner else None, **_bucket()}
        _add(owners[key], gross, commission, count=0, units=quantity)

    # ponytail: aggregated in Python (exact Decimals, one shape for both channels);
    # the date range bounds it. Move to DB aggregates if the range gets slow.
    for sale in sales:
        items = list(sale.items.all())
        gross = sum((item.price * item.quantity for item in items), ZERO)
        units = sum(item.quantity for item in items)
        _add(channels['pos'], gross, sale.commission_amount, units=units)
        _add(methods[sale.payment_method or 'unknown'], gross, sale.commission_amount, units=units)
        for item in items:
            line = item.price * item.quantity
            add_record(item.record_id, item.title or '(disco eliminado)', item.artist,
                       item.cover_image_url, item.quantity, line)
            add_owner(item.owner, item.quantity, line, item.commission_amount)

    shipping = ZERO
    for order in orders:
        order_shipping = order.shipping_cost or ZERO
        fee = estimated_stripe_fee(order.amount)
        items = list(order.order_items.all())
        units = sum(item.quantity for item in items)
        _add(channels['online'], order.amount - order_shipping, fee, units=units)
        _add(methods['stripe'], order.amount - order_shipping, fee, units=units)
        shipping += order_shipping
        for item in items:
            record = item.record
            line = item.price * item.quantity
            add_record(
                item.record_id,
                record.title if record else '(disco eliminado)',
                record.artist.name if record and record.artist else '',
                record.cover_image_url if record else None,
                item.quantity, line,
            )
            add_owner(record.owner if record else None, item.quantity, line, fee * line / order.amount)

    summary = _bucket()
    for bucket in channels.values():
        _add(summary, bucket['gross'], bucket['commission'], count=bucket['count'], units=bucket['units'])

    top_records = sorted(records.values(), key=lambda r: (r['units'], r['gross']), reverse=True)[:10]
    return {
        'summary': {**_money_fields(summary), 'shipping': str(_cents(shipping))},
        'channels': [{'channel': name, **_money_fields(bucket)} for name, bucket in channels.items()],
        'payment_methods': [
            {'method': name, **_money_fields(bucket)}
            for name, bucket in sorted(methods.items(), key=lambda m: m[1]['gross'], reverse=True)
        ],
        'top_records': [{**row, 'gross': str(_cents(row['gross']))} for row in top_records],
        'owners': [
            {'owner': row['owner'], **_money_fields(row)}
            for row in sorted(owners.values(), key=lambda r: r['gross'], reverse=True)
        ],
    }
