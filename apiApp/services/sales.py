"""Sales registered in Punto de venta: card commission, owner notifications
and the Ventas metrics (point of sale + online orders + paid presales)."""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

from ..emails import send_email
from types import SimpleNamespace

from ..models import Order, PreSaleOrder, Sale

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
                        "image_url": item.cover_image_url,
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


def _paid_presales(date_from=None, date_to=None):
    """Presale orders whose record price is fully paid, on the store day it got paid."""
    orders = PreSaleOrder.objects.filter(paid_at__isnull=False).exclude(status='cancelado').prefetch_related(
        'payments', 'items__option__presale', 'items__option__owner')
    if date_from:
        orders = orders.filter(paid_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(paid_at__date__lte=date_to)
    return orders


def presale_figures(order):
    """Ventas numbers of a paid presale order.

    Gross = item_price (shipping is pass-through, reported apart). Commission =
    estimated Stripe fee on each online payment plus the card commission
    (CARD_COMMISSION_RATE) on each in-store card payment; cash/transfer carry none.
    Gross is split across payment methods by what each line paid for the record.
    """
    payments = [p for p in order.payments.all() if p.voided_at is None]
    item_paid = {}
    commission = ZERO
    shipping = ZERO
    for payment in payments:
        item_paid[payment.method] = item_paid.get(payment.method, ZERO) + payment.amount - payment.shipping_amount
        shipping += payment.shipping_amount
        if payment.method == 'stripe':
            commission += estimated_stripe_fee(payment.amount)
        elif payment.method == 'card':
            commission += _cents(payment.amount * CARD_COMMISSION_RATE / 100)
    gross = order.item_price
    total_item = sum(item_paid.values(), ZERO)
    split = {m: _cents(gross * amount / total_item) for m, amount in item_paid.items()} if total_item else {}
    labels = dict(order.payments.model.METHODS)
    # One sale line per version, shaped like a point-of-sale line (price = per copy, net of discount).
    lines = [
        SimpleNamespace(record=None, option=item.option, owner=item.option.owner, owner_id=item.option.owner_id,
                        price=item.unit_price - item.unit_discount, quantity=item.quantity,
                        cost_price=item.option.cost_price or ZERO)
        for item in order.items.all()
    ]
    cost, uncosted = _costs(lines)
    return {
        'gross': gross,
        'commission': commission,
        'shipping': shipping,
        'cost': cost,
        'uncosted': uncosted,
        'units': sum(line.quantity for line in lines),
        'split': split,
        'dominant': max(split, key=split.get) if split else 'unknown',
        'method_label': ' + '.join(labels.get(m, m) for m in item_paid),
        'lines': lines,
    }


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
    return {'count': 0, 'units': 0, 'gross': ZERO, 'commission': ZERO, 'cost': ZERO, 'units_without_cost': 0}


def _add(bucket, gross, commission, count=1, units=0, cost=ZERO, units_without_cost=0):
    bucket['count'] += count
    bucket['units'] += units
    bucket['gross'] += gross
    bucket['commission'] += commission
    bucket['cost'] += cost
    bucket['units_without_cost'] += units_without_cost


def _costs(items):
    """(cost, units_without_cost) of sale lines, from the cost saved at sale time.
    A 0 cost means none was recorded: profit counts those units at full net, so
    the UI flags how many there are instead of showing an inflated number silently."""
    cost = sum((item.cost_price * item.quantity for item in items), ZERO)
    return cost, sum(item.quantity for item in items if not item.cost_price)


def _money_fields(bucket):
    gross, commission, count = bucket['gross'], bucket['commission'], bucket['count']
    return {
        'count': count,
        'units': bucket['units'],
        'gross': str(_cents(gross)),
        'commission': str(_cents(commission)),
        'net': str(_cents(gross - commission)),
        'average_ticket': str(_cents(gross / count) if count else ZERO),
        # Profit = net (after card/Stripe commission) - cost of the records sold.
        'cost': str(_cents(bucket['cost'])),
        'profit': str(_cents(gross - commission - bucket['cost'])),
        'units_without_cost': bucket['units_without_cost'],
    }


# Longer ranges than this are charted by month (a bar per day stops reading).
TIMELINE_MAX_DAYS = 92


def _timeline(days, date_from, date_to):
    """Gross / profit / ticket count per period, every channel side by side,
    every period of the range present (zeros included) so the chart's x axis
    is continuous. By day, or by month past TIMELINE_MAX_DAYS."""
    if not days and not (date_from and date_to):
        return {'grain': 'day', 'points': []}
    start = date_from or min(days)
    end = date_to or max(max(days), timezone.localdate())
    monthly = (end - start).days + 1 > TIMELINE_MAX_DAYS
    key = (lambda d: d.strftime('%Y-%m')) if monthly else (lambda d: d.isoformat())

    periods = {}
    day = start
    while day <= end:
        periods.setdefault(key(day), {'pos': _bucket(), 'online': _bucket(), 'presale': _bucket()})
        day += timedelta(days=1)
    for date, by_channel in days.items():
        if key(date) not in periods:
            continue
        for name, bucket in by_channel.items():
            _add(periods[key(date)][name], bucket['gross'], bucket['commission'], count=bucket['count'],
                 units=bucket['units'], cost=bucket['cost'], units_without_cost=bucket['units_without_cost'])

    def point(period, by_channel):
        buckets = by_channel.values()
        gross = sum((b['gross'] for b in buckets), ZERO)
        profit = gross - sum((b['commission'] + b['cost'] for b in buckets), ZERO)
        return {
            'period': period,
            'pos': str(_cents(by_channel['pos']['gross'])),
            'online': str(_cents(by_channel['online']['gross'])),
            'presale': str(_cents(by_channel['presale']['gross'])),
            'profit': str(_cents(profit)),
            'count': sum(b['count'] for b in buckets),
        }

    return {'grain': 'month' if monthly else 'day', 'points': [point(p, b) for p, b in periods.items()]}


def previous_period(date_from, date_to):
    """The same number of days right before [date_from, date_to]."""
    span = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    return prev_to - timedelta(days=span - 1), prev_to


def sales_metrics(date_from=None, date_to=None):
    """Ventas → Métricas: point-of-sale tickets, paid online orders and presales
    (on the day their record price got fully paid).

    Gross is what the records sold for (online excludes shipping, which is
    reported apart). Commission is the stored card commission for the point
    of sale and an *estimated* Stripe fee for online orders; online owners
    get the fee share of their lines, the shipping share stays with the store.
    Profit is net minus the cost saved on each line at sale time.
    """
    sales = Sale.objects.prefetch_related('items__owner')
    orders = Order.objects.filter(status__in=ONLINE_SALE_STATUSES).prefetch_related(
        'order_items__record__artist', 'order_items__owner'
    )
    # __date converts to TIME_ZONE: store days, like the Ventas history.
    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)
        orders = orders.filter(created_at__date__lte=date_to)

    channels = {'pos': _bucket(), 'online': _bucket(), 'presale': _bucket()}
    methods = defaultdict(_bucket)
    # Point-of-sale tickets by where they sold (store, bazar, other).
    places = defaultdict(_bucket)
    # Per store day and channel, for the timeline.
    days = defaultdict(lambda: {'pos': _bucket(), 'online': _bucket(), 'presale': _bucket()})
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

    def add_owner(owner, item, gross, commission):
        key = owner.id if owner else None
        if key not in owners:
            owners[key] = {'owner': {'id': owner.id, 'name': owner.name} if owner else None, **_bucket()}
        cost, uncosted = _costs([item])
        _add(owners[key], gross, commission, count=0, units=item.quantity, cost=cost, units_without_cost=uncosted)

    # ponytail: aggregated in Python (exact Decimals, one shape for both channels);
    # the date range bounds it. Move to DB aggregates if the range gets slow.
    for sale in sales:
        items = list(sale.items.all())
        gross = sum((item.price * item.quantity for item in items), ZERO)
        units = sum(item.quantity for item in items)
        cost, uncosted = _costs(items)
        place = places[sale.channel or 'unknown']
        day = days[timezone.localdate(sale.created_at)]['pos']
        for bucket in (channels['pos'], methods[sale.payment_method or 'unknown'], place, day):
            _add(bucket, gross, sale.commission_amount, units=units, cost=cost, units_without_cost=uncosted)
        for item in items:
            line = item.price * item.quantity
            add_record(item.record_id, item.title or '(disco eliminado)', item.artist,
                       item.cover_image_url, item.quantity, line)
            add_owner(item.owner, item, line, item.commission_amount)

    shipping = ZERO
    for order in orders:
        order_shipping = order.shipping_cost or ZERO
        fee = estimated_stripe_fee(order.amount)
        items = list(order.order_items.all())
        units = sum(item.quantity for item in items)
        cost, uncosted = _costs(items)
        day = days[timezone.localdate(order.created_at)]['online']
        for bucket in (channels['online'], methods['stripe'], day):
            _add(bucket, order.amount - order_shipping, fee, units=units, cost=cost, units_without_cost=uncosted)
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
            add_owner(item.owner, item, line, fee * line / order.amount)

    for order in _paid_presales(date_from, date_to):
        figures = presale_figures(order)
        gross, commission = figures['gross'], figures['commission']
        cost, uncosted = figures['cost'], figures['uncosted']
        for bucket in (channels['presale'], days[timezone.localdate(order.paid_at)]['presale']):
            _add(bucket, gross, commission, units=figures['units'], cost=cost, units_without_cost=uncosted)
        for name, part in figures['split'].items():
            share = part / gross if gross else ZERO
            _add(methods[name], part, commission * share, count=int(name == figures['dominant']), units=0,
                 cost=cost * share, units_without_cost=0)
        shipping += figures['shipping']
        for line in figures['lines']:
            line_gross = line.price * line.quantity
            presale = line.option.presale
            add_record(f'presale:{line.option.id}', f'{presale.title} · {line.option.name}', presale.artist,
                       None, line.quantity, line_gross)
            add_owner(line.owner, line, line_gross, commission * line_gross / gross if gross else ZERO)

    summary = _bucket()
    for bucket in channels.values():
        _add(summary, bucket['gross'], bucket['commission'], count=bucket['count'], units=bucket['units'],
             cost=bucket['cost'], units_without_cost=bucket['units_without_cost'])

    top_records = sorted(records.values(), key=lambda r: (r['units'], r['gross']), reverse=True)[:10]
    return {
        'summary': {**_money_fields(summary), 'shipping': str(_cents(shipping))},
        'channels': [{'channel': name, **_money_fields(bucket)} for name, bucket in channels.items()],
        'payment_methods': [
            {'method': name, **_money_fields(bucket)}
            for name, bucket in sorted(methods.items(), key=lambda m: m[1]['gross'], reverse=True)
        ],
        'timeline': _timeline(days, date_from, date_to),
        'sale_channels': [
            {'channel': name, **_money_fields(bucket)}
            for name, bucket in sorted(places.items(), key=lambda m: m[1]['gross'], reverse=True)
        ],
        'top_records': [{**row, 'gross': str(_cents(row['gross']))} for row in top_records],
        'owners': [
            {'owner': row['owner'], **_money_fields(row)}
            for row in sorted(owners.values(), key=lambda r: r['gross'], reverse=True)
        ],
    }


# ── Ventas → Exportar CSV ────────────────────────────────────────────────

SALES_CSV_HEADER = [
    'Fecha', 'Hora', 'Canal', 'Lugar de venta', 'Folio', 'Forma de pago', 'Cliente',
    'Disco', 'Artista', 'Condición', 'Dueño', 'Correo del dueño',
    'Cantidad', 'Precio unitario', 'Subtotal', 'Comisión', 'Neto',
    'Costo unitario', 'Costo total', 'Ganancia',
]


def sale_place(sale):
    """"Bazar · Bazar del Chopo": the channel plus its note, or "Sin registrar"."""
    place = sale.get_channel_display() or 'Sin registrar'
    return f'{place} · {sale.channel_note}' if sale.channel_note else place


def sales_csv_rows(date_from=None, date_to=None, owner_id=None):
    """Every sold line of the period, oldest first: point of sale plus paid
    online orders, one row each (SALES_CSV_HEADER columns).

    Same numbers as the metrics: online commission is the estimated Stripe fee
    share of the line, shipping is left out. With ``owner_id`` only that
    owner's lines. Money as plain decimals so spreadsheets read them as numbers.
    """
    sales = Sale.objects.prefetch_related('items__owner', 'items__record')
    orders = Order.objects.filter(status__in=ONLINE_SALE_STATUSES).prefetch_related(
        'order_items__record__artist', 'order_items__owner'
    )
    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)
        orders = orders.filter(created_at__date__lte=date_to)

    def row(created_at, channel, place, folio, method, customer, item, title, artist, commission):
        when = timezone.localtime(created_at)
        record, owner = item.record, item.owner
        gross = item.price * item.quantity
        cost = item.cost_price * item.quantity
        return created_at, [
            when.strftime('%Y-%m-%d'), when.strftime('%H:%M'), channel, place, folio, method, customer,
            title, artist, record.get_condition_display() if record else '',
            owner.name if owner else 'Sin dueño', owner.email if owner else '',
            item.quantity, f'{item.price:.2f}', f'{gross:.2f}', f'{_cents(commission):.2f}',
            f'{_cents(gross - commission):.2f}', f'{item.cost_price:.2f}', f'{cost:.2f}',
            f'{_cents(gross - commission - cost):.2f}',
        ]

    rows = []
    for sale in sales:
        for item in sale.items.all():
            if owner_id and item.owner_id != owner_id:
                continue
            rows.append(row(
                sale.created_at, 'Punto de venta', sale_place(sale), f'Venta #{sale.id}',
                sale.get_payment_method_display() or 'Sin registrar', '', item,
                item.title or '(disco eliminado)', item.artist, item.commission_amount,
            ))
    for order in orders:
        fee = estimated_stripe_fee(order.amount)
        for item in order.order_items.all():
            if owner_id and item.owner_id != owner_id:
                continue
            record = item.record
            rows.append(row(
                order.created_at, 'En línea', 'Tienda en línea', f'Orden #{order.id}', 'Stripe', order.user_email, item,
                record.title if record else '(disco eliminado)',
                record.artist.name if record and record.artist else '',
                fee * item.price * item.quantity / order.amount if order.amount else ZERO,
            ))
    for order in _paid_presales(date_from, date_to):
        figures = presale_figures(order)
        for line in figures['lines']:
            if owner_id and line.owner_id != owner_id:
                continue
            line_gross = line.price * line.quantity
            presale = line.option.presale
            rows.append(row(
                order.paid_at, 'Preventa', 'Preventa', order.code, figures['method_label'] or 'Sin registrar',
                order.buyer_email or (order.user.email if order.user_id else ''), line,
                f'{presale.title} · {line.option.name}', presale.artist,
                figures['commission'] * line_gross / figures['gross'] if figures['gross'] else ZERO,
            ))
    rows.sort(key=lambda r: r[0])
    return [values for _, values in rows]


def _image_url(image):
    try:
        return image.url if image else None
    except ValueError:  # the file field has no file
        return None


def presale_history(date_from=None, date_to=None, owner_id=None):
    """Ventas → Registro rows for paid presale orders (shaped like tickets), newest first.

    With ``owner_id`` only orders holding that owner's versions are listed, and
    each row's money counts only those lines (as tickets do).
    """
    rows = []
    for order in _paid_presales(date_from, date_to):
        figures = presale_figures(order)
        lines = [line for line in figures['lines'] if not owner_id or line.owner_id == owner_id]
        if not lines:
            continue
        gross_all = figures['gross']
        subtotal = sum((line.price * line.quantity for line in lines), ZERO)
        commission = _cents(figures['commission'] * subtotal / gross_all) if gross_all else ZERO
        cost = sum((line.cost_price * line.quantity for line in lines), ZERO)
        items = []
        for index, line in enumerate(figures['lines']):
            line_gross = line.price * line.quantity
            share = _cents(figures['commission'] * line_gross / gross_all) if gross_all else ZERO
            option = line.option
            items.append({
                'id': f'{order.code}-{index}',
                'title': f'{option.presale.title} · {option.name}',
                'artist': option.presale.artist,
                'cover_image_url': _image_url(option.image or option.presale.cover),
                'owner': {'id': line.owner.id, 'name': line.owner.name, 'email': line.owner.email} if line.owner else None,
                'quantity': line.quantity,
                'price': str(_cents(line.price)),
                'cost_price': str(_cents(line.cost_price)),
                'profit': str(_cents(line_gross - share - line.cost_price * line.quantity)),
            })
        rows.append({
            'code': order.code,
            'paid_at': order.paid_at.isoformat(),
            'buyer': order.buyer_name,
            'method_label': figures['method_label'] or 'Sin registrar',
            'subtotal': str(_cents(subtotal)),
            'commission': str(commission),
            'final_sale_price': str(_cents(subtotal - commission)),
            'cost': str(_cents(cost)),
            'profit': str(_cents(subtotal - commission - cost)),
            'units_without_cost': sum(line.quantity for line in lines if not line.cost_price),
            'items': items,
        })
    rows.sort(key=lambda row: row['paid_at'], reverse=True)
    return rows


def notify_presale_owner(order):
    """Consignor emails when a presale order is fully paid: one per owner, listing
    their versions in it (once per order). Never raises."""
    if order.owner_notified:
        return
    by_owner = defaultdict(list)
    for item in order.items.select_related('option__presale', 'option__owner'):
        if item.option.owner_id:
            by_owner[item.option.owner].append(item)
    for owner, items in by_owner.items():
        gross = sum((item.line_total for item in items), ZERO)
        try:
            send_email(
                template_name="owner_sale",
                context={
                    "owner_name": owner.name,
                    "sale_id": order.code,
                    "sold_at": timezone.localtime(order.paid_at or timezone.now()).strftime("%d/%m/%Y %H:%M"),
                    "items": [{
                        "title": f"{item.option.presale.title} · {item.option.name}",
                        "artist": item.option.presale.artist,
                        "image_url": None,
                        "quantity": item.quantity,
                        "price_str": _money(item.unit_price - item.unit_discount),
                        "subtotal_str": _money(item.line_total),
                    } for item in items],
                    "payment_method": "Preventa",
                    "has_commission": False,
                    "commission_rate": "0.00",
                    "gross_str": _money(gross),
                    "commission_str": _money(ZERO),
                    "total_str": _money(gross),
                    "frontend_url": settings.FRONTEND_URL,
                },
                subject=f"Vendimos tu disco en preventa 🎶 — {order.code}",
                to=[owner.email],
            )
        except Exception:
            logger.exception("Presale %s: email to owner %s failed", order.code, owner.email)
    PreSaleOrder.objects.filter(pk=order.pk).update(owner_notified=True)
