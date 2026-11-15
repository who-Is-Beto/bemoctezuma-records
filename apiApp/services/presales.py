"""Preventas money and state rules. Views stay thin and call these.

Money: an order's paid amount is always the sum of its non-voided payments.
Total due = item_price + shipping_cost (once known). Shipping is charged with
the last payment (or right away when paying 100% at reservation).
"""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ..models import PreSaleOption, PreSaleOrder, PreSaleOrderEvent, PreSaleOrderItem, PreSalePayment
from . import shipping

logger = logging.getLogger(__name__)

DEPOSIT_DAYS = 120
AFTER_ARRIVAL_DAYS = 15
HOLD_MINUTES = 35
# Stripe Checkout's shortest lifetime; the hold outlasts it, so Stripe can't
# complete a payment after the copy was released.
CHECKOUT_MINUTES = 31
TERMS_VERSION = '2026-11'

CENT = Decimal('0.01')
ZERO = Decimal('0.00')
# Orders that keep their copy (pending_payment only while its hold is alive).
HOLDING_STATUSES = ('apartado', 'pagado', 'listo', 'enviado', 'entregado')
# Final states refresh_status never moves out of.
LOCKED_STATUSES = ('enviado', 'entregado', 'vencido', 'cancelado')


class PresaleError(Exception):
    MESSAGES = {
        'invalid_amount': 'La cantidad no es válida.',
        'amount_too_low': 'La cantidad es menor al apartado mínimo.',
        'amount_exceeds_balance': 'La cantidad es mayor a lo que falta por pagar.',
        'shipping_quote_required': 'Necesitamos cotizar el envío para cobrarlo completo.',
        'terms_required': 'Debes aceptar las condiciones de la preventa.',
        'sold_out': 'Ya no quedan copias de esta opción.',
        'deadline_passed': 'Esta preventa ya cerró.',
        'inactive': 'Esta opción ya no está disponible.',
        'not_published': 'Esta preventa no está disponible.',
        'reason_required': 'Escribe el motivo.',
        'balance_due': 'Todavía hay saldo pendiente.',
        'already_delivered': 'Esta preventa ya fue entregada.',
        'not_pickup': 'Esta preventa es para envío; márcala como enviada.',
        'not_shipping': 'Esta preventa es para recoger en tienda.',
        'not_expirable': 'Este apartado todavía no vence.',
        'invalid_status': 'No se puede hacer eso en el estado actual.',
        'nothing_due': 'No hay saldo pendiente.',
        'invalid_items': 'Revisa las versiones y cantidades.',
        'limit_exceeded': 'Llegaste al máximo por persona de esta versión.',
        'paid_not_enough': 'Falta pagar para entregar esto.',
        'refund_failed': 'Stripe no pudo hacer el reembolso; el pago sigue vigente.',
        'invalid_partial': 'Abona desde $100 en múltiplos de $50, o liquida lo que falta.',
    }

    def __init__(self, code, message=None):
        self.code = code
        self.message = message or self.MESSAGES.get(code, 'No se pudo completar la acción.')
        super().__init__(self.message)


def _money(value):
    """Decimal with at most 2 decimals, or PresaleError('invalid_amount')."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PresaleError('invalid_amount')
    if not amount.is_finite() or amount.quantize(CENT) != amount:
        raise PresaleError('invalid_amount')
    return amount.quantize(CENT)


def _log(order, kind, note='', actor=None):
    PreSaleOrderEvent.objects.create(order=order, kind=kind, note=note[:255], actor=actor)


# ── reads ──────────────────────────────────────────────────────────────────

def _holding():
    """Lines whose order keeps its copies (pending_payment only while its hold is alive)."""
    return Q(order__status__in=HOLDING_STATUSES) | Q(order__status='pending_payment',
                                                     order__hold_expires_at__gt=timezone.now())


def copies_left(option):
    # A delivered copy left the store even if its order was later cancelled.
    taken = option.items.filter(_holding() | Q(delivered_at__isnull=False)).aggregate(s=Sum('quantity'))['s']
    return option.copies - (taken or 0)


def held_by_user(option, user):
    """Copies of ``option`` this user already holds (live online holds included)."""
    return option.items.filter(_holding(), order__user=user).aggregate(s=Sum('quantity'))['s'] or 0


def parse_items(raw, presale=None):
    """[{option_id, quantity}] → [(PreSaleOption, qty)], or PresaleError('invalid_items').

    One order can hold versions of several presales (one box, one payment);
    with ``presale`` every line must belong to it.
    """
    if not isinstance(raw, list) or not raw:
        raise PresaleError('invalid_items')
    wanted = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise PresaleError('invalid_items')
        option_id, quantity = str(entry.get('option_id', '')), str(entry.get('quantity', ''))
        if not option_id.isdigit() or not quantity.isdigit() or int(quantity) < 1 or int(option_id) in wanted:
            raise PresaleError('invalid_items')
        wanted[int(option_id)] = int(quantity)
    options = PreSaleOption.objects.select_related('presale', 'category').in_bulk(list(wanted))
    if len(options) != len(wanted):
        raise PresaleError('invalid_items')
    if presale is not None and any(o.presale_id != presale.pk for o in options.values()):
        raise PresaleError('invalid_items')
    return [(options[option_id], quantity) for option_id, quantity in wanted.items()]


def order_lines(order):
    return [(item.option, item.quantity) for item in order.items.all()]


def order_presales(order):
    """The presales in the order, in line order, each once."""
    return list({item.option.presale_id: item.option.presale for item in order.items.all()}.values())


def order_title(order):
    """'Kind of Blue' or 'Kind of Blue + Abbey Road' (an order can span presales)."""
    return ' + '.join(p.title for p in order_presales(order))


def line_name(item, multi):
    """'Rojo' or, when the order spans presales, 'Kind of Blue · Rojo'."""
    return f'{item.option.presale.title} · {item.option.name}' if multi else item.option.name


def lines_label(order):
    """'Rojo ×2, Negro ×1' (presale titles in front when the order spans several)."""
    items = list(order.items.all())
    multi = len({i.option.presale_id for i in items}) > 1
    return ', '.join(f'{line_name(item, multi)} ×{item.quantity}' for item in items)


def all_arrived(order):
    return all(item.arrived_at for item in order.items.all())


def last_arrival(order):
    """When the last line of the order arrived; None while any is on its way."""
    dates = [item.arrived_at for item in order.items.all()]
    return max(dates) if dates and all(dates) else None


def paid_amount(order):
    total = order.payments.filter(voided_at__isnull=True).aggregate(s=Sum('amount'))['s']
    return (total or ZERO).quantize(CENT)


def total_due(order):
    return (order.item_price + (order.shipping_cost or ZERO)).quantize(CENT)


def balance(order):
    return max(total_due(order) - paid_amount(order), ZERO)


def shipping_pending(order):
    return order.delivery == 'shipping' and order.shipping_cost is None


def deadline(order):
    """Reservation + 120 days, never before the last arrival + 15 days, extended by the admin."""
    dates = [timezone.localdate(order.reserved_at) + timedelta(days=DEPOSIT_DAYS)]
    arrived = last_arrival(order)
    if arrived:
        dates.append(timezone.localdate(arrived) + timedelta(days=AFTER_ARRIVAL_DAYS))
    if order.deadline_override:
        dates.append(order.deadline_override)
    return max(dates)


def days_left(order):
    return (deadline(order) - timezone.localdate()).days


def is_expirable(order):
    return (
        order.status in ('apartado', 'pagado')
        and last_arrival(order) is not None
        and timezone.localdate() > deadline(order)
        # Owing only shipping that was never quoted is the store's to fix, not a forfeit.
        and balance(order) > 0
    )


# ── state ──────────────────────────────────────────────────────────────────

def refresh_status(order, actor=None):
    """Derive status from money and arrival. Final states are left alone."""
    if order.status in LOCKED_STATUSES:
        return
    paid = paid_amount(order)
    previous = order.status
    if paid <= 0 and order.status == 'pending_payment':
        return
    newly_paid = False
    if paid >= order.item_price:
        status = 'pagado'
        if order.paid_at is None:
            order.paid_at = timezone.now()
            newly_paid = True
    else:
        status = 'apartado'
        order.paid_at = None
    if status == 'pagado' and order.delivery == 'pickup' and all_arrived(order) and balance(order) == 0:
        status = 'listo'
    order.status = status
    if status != 'pending_payment':
        order.hold_expires_at = None
    order.save(update_fields=['status', 'paid_at', 'hold_expires_at', 'updated_at'])
    if status != previous:
        _log(order, 'status', f'{previous} → {status}', actor)
    if newly_paid and not order.owner_notified and any(item.option.owner_id for item in order.items.all()):
        from .sales import notify_presale_owner
        notify_presale_owner(order)


def split_amount(lines, amount, delivery, shipping_quote):
    """What paying ``amount`` for ``lines`` [(option, qty)] means: deposit, or full payment with discounts."""
    amount = _money(amount)
    price = sum((option.price * qty for option, qty in lines), ZERO)
    full = sum(((option.price - (option.full_payment_discount or ZERO)) * qty for option, qty in lines), ZERO)
    minimum = sum((option.min_deposit * qty for option, qty in lines), ZERO)
    if amount > price or amount <= 0:
        raise PresaleError('invalid_amount')
    if amount >= full:
        shipping = None
        if delivery == 'shipping':
            if shipping_quote is None:
                raise PresaleError('shipping_quote_required')
            shipping = _money(shipping_quote)
        return {
            'item_price': full,
            'discount': price - full,
            'shipping_cost': shipping,
            'charge': full + (shipping or ZERO),
            'full': True,
        }
    if amount < minimum:
        raise PresaleError('amount_too_low')
    return {'item_price': price, 'discount': ZERO, 'shipping_cost': None, 'charge': amount, 'full': False}


def _check_open(option):
    presale = option.presale
    if presale.status != 'published':
        raise PresaleError('not_published')
    if not option.is_active:
        raise PresaleError('inactive')
    if timezone.localdate() > presale.purchase_deadline:
        raise PresaleError('deadline_passed')


def create_order(lines, *, buyer, source, delivery, shipping_details, amount, method=None,
                 shipping_quote=None, created_by=None, accept_terms, note=''):
    """Reserve ``lines`` [(option, qty)] in one order. ``buyer`` is a User (online) or
    {'name','email','phone'} (admin).

    Online (method=None): the order waits in pending_payment with a 30-minute hold
    while the customer pays in Stripe. Admin: the payment is recorded right away.
    """
    if not accept_terms:
        raise PresaleError('terms_required')
    split = split_amount(lines, amount, delivery, shipping_quote)
    user = None if isinstance(buyer, dict) else buyer
    online = method is None and source == 'online'
    with transaction.atomic():
        locked = {o.pk: o for o in PreSaleOption.objects.select_for_update().select_related('presale')
                  .filter(pk__in=[option.pk for option, _ in lines]).order_by('pk')}
        lines = [(locked[option.pk], qty) for option, qty in lines]
        for option, qty in lines:
            if online:
                _check_open(option)
            elif not option.is_active:
                # Admins may register buyers after the public deadline (Instagram DMs).
                raise PresaleError('inactive')
            left = copies_left(option)
            if left < qty:
                raise PresaleError('sold_out', f'Ya no quedan copias de {option.name}.' if left <= 0
                                   else f'Solo quedan {left} de {option.name}.')
            # The per-buyer limit is for the website; staff decide in Administración.
            if online and user and option.max_per_buyer:
                held = held_by_user(option, user)
                if held + qty > option.max_per_buyer:
                    raise PresaleError('limit_exceeded', f'Puedes llevar hasta {option.max_per_buyer} de {option.name}'
                                       + (f'; ya tienes {held}.' if held else '.'))

        now = timezone.now()
        if user is None:
            name, email, phone = buyer.get('name', ''), buyer.get('email', ''), buyer.get('phone', '')
        else:
            name = user.get_full_name() or user.username
            email, phone = user.email, ''
        details = shipping_details or {}
        if details.get('phone') and not phone:
            phone = details['phone']
        order = PreSaleOrder.objects.create(
            user=user,
            buyer_name=name[:120],
            buyer_email=email or '',
            buyer_phone=(phone or '')[:30],
            source=source,
            status='pending_payment',
            delivery=delivery,
            shipping_details=details if delivery == 'shipping' else {},
            shipping_estimate=shipping_quote if delivery == 'shipping' and shipping_quote is not None else None,
            shipping_cost=split['shipping_cost'],
            item_price=split['item_price'],
            discount_applied=split['discount'],
            reserved_at=now,
            hold_expires_at=now + timedelta(minutes=HOLD_MINUTES) if method is None else None,
            created_by=created_by,
            terms_accepted_at=now,
            terms_version=TERMS_VERSION,
        )
        PreSaleOrderItem.objects.bulk_create([
            PreSaleOrderItem(order=order, option=option, quantity=qty, unit_price=option.price,
                             unit_discount=(option.full_payment_discount or ZERO) if split['full'] else ZERO)
            for option, qty in lines
        ])
        _log(order, 'reserved', f'{lines_label(order)} · {source}', created_by)
        if method is not None:
            record_payment(order, split['charge'], method, note=note, registered_by=created_by,
                           shipping_amount=split['shipping_cost'] or ZERO)
            order.refresh_from_db()
    return order


def record_payment(order, amount, method, *, note='', registered_by=None, stripe_session_id=None,
                   shipping_amount=ZERO):
    """Add a ledger line and re-derive the status. Stripe lines are idempotent per session."""
    if stripe_session_id:
        existing = PreSalePayment.objects.filter(stripe_checkout_session_id=stripe_session_id).first()
        if existing:
            return existing
    amount = _money(amount)
    if amount <= 0:
        raise PresaleError('invalid_amount')
    if method != 'stripe' and amount > balance(order):
        raise PresaleError('amount_exceeds_balance')
    payment = PreSalePayment.objects.create(
        order=order,
        amount=amount,
        method=method,
        note=(note or '')[:120],
        shipping_amount=shipping_amount or ZERO,
        stripe_checkout_session_id=stripe_session_id,
        registered_by=registered_by,
    )
    _log(order, 'payment', f'${amount} · {payment.get_method_display()}', registered_by)
    refresh_status(order, registered_by)
    hand_out([item.option for item in order.items.select_related('option') if not item.arrived_at],
             by=registered_by)
    return payment


def void_payment(payment, *, by, reason):
    reason = (reason or '').strip()
    if not reason:
        raise PresaleError('reason_required')
    if payment.voided_at:
        return
    if payment.method == 'stripe':
        # Money paid online goes back through Stripe first; the line is voided only once refunded.
        reason = f"{reason} · reembolso Stripe {_stripe_refund(payment)}"
    payment.voided_at = timezone.now()
    payment.voided_by = by
    payment.void_reason = reason[:200]
    payment.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
    order = payment.order
    _log(order, 'void', f'${payment.amount}: {reason}', by)
    refresh_status(order, by)


def _arrival_priority(item):
    """Paid in full first (by when they finished paying), then by first payment."""
    order = item.order
    if order.paid_at:
        return (0, order.paid_at, item.pk)
    first = min((p.created_at for p in order.payments.all() if p.voided_at is None), default=order.reserved_at)
    return (1, first, item.pk)


def allocate_arrivals(option):
    """Hand the version's unassigned arrived copies to waiting lines by priority.

    A line gets all its copies or waits (copies of one line are handed over
    together); a smaller line further down can take what a bigger one couldn't.
    Returns the orders that got copies.
    """
    with transaction.atomic():
        option = PreSaleOption.objects.select_for_update().get(pk=option.pk)
        items = list(option.items.select_related('order').prefetch_related('order__payments')
                     .exclude(order__status__in=('pending_payment', 'vencido', 'cancelado')))
        free = option.arrived_copies - sum(i.quantity for i in items if i.arrived_at)
        now, orders = timezone.now(), {}
        for item in sorted((i for i in items if not i.arrived_at), key=_arrival_priority):
            if item.quantity <= free:
                item.arrived_at = now
                item.save(update_fields=['arrived_at'])
                free -= item.quantity
                orders[item.order_id] = item.order
    return list(orders.values())


def _stripe_refund(payment):
    """Refund ``payment`` in full through Stripe; returns the refund id. Idempotent per payment."""
    import stripe
    from django.conf import settings
    if not payment.stripe_checkout_session_id:
        raise PresaleError('refund_failed', 'Este pago no tiene sesión de Stripe; reembólsalo desde Stripe.')
    try:
        session = stripe.checkout.Session.retrieve(payment.stripe_checkout_session_id,
                                                   api_key=settings.STRIPE_SECRET_KEY)
        refund = stripe.Refund.create(
            payment_intent=session.get('payment_intent'),
            amount=int((payment.amount * 100).quantize(Decimal('1'))),
            metadata={'kind': 'presale', 'order_code': payment.order.code, 'payment_id': str(payment.pk)},
            idempotency_key=f'presale-payment-{payment.pk}-refund',
            api_key=settings.STRIPE_SECRET_KEY,
        )
    except stripe.StripeError as exc:
        logger.warning("Stripe refund for presale payment %s failed: %s", payment.pk, exc)
        raise PresaleError('refund_failed')
    return refund.get('id')


def mark_arrived(option, *, by, copies=None):
    """``copies`` more copies of the version reached the store (``None``: all of them).

    They go to buyers by priority (allocate_arrivals); every order that got
    copies is re-derived, emailed and returned.
    """
    count_arrival(option, copies)
    return hand_out([option], by=by)


def count_arrival(option, copies=None):
    """Add ``copies`` (``None``: all) to the version's arrived count; nobody is assigned yet."""
    if copies is None:
        option.arrived_copies = option.copies
    else:
        if copies <= 0:
            raise PresaleError('invalid_amount')
        option.arrived_copies = min(option.arrived_copies + copies, option.copies)
    if option.arrived_copies >= option.copies:
        option.arrived_at = option.arrived_at or timezone.now()
    option.save(update_fields=['arrived_copies', 'arrived_at'])


def hand_out(options, *, by):
    """Give spare arrived copies of ``options`` to waiting buyers; each order that
    got copies is re-derived, re-quoted for shipping once complete, and emailed once."""
    from .emailing import send_presale_email
    orders = {}
    for option in options:
        if not option.arrived_copies:
            continue
        for order in allocate_arrivals(option):
            _log(order, 'arrived', option.name, by)
            orders[order.pk] = order
    for order in orders.values():
        order.refresh_from_db()
        refresh_status(order, by)
        if order.delivery == 'shipping' and order.shipping_cost is None and all_arrived(order):
            try:
                requote_shipping(order, by=by)
            except (PresaleError, shipping.ShippingQuoteError) as exc:
                logger.warning("Could not re-quote shipping for %s: %s", order.code, exc)
        send_presale_email(order, 'arrived')
    return list(orders.values())


def _release_copies(order, by):
    """A cancelled/expired order gives its set-aside (not handed over) copies to the next buyers."""
    options = [item.option for item in order.items.select_related('option')
               if item.arrived_at and not item.delivered_at]
    order.items.filter(delivered_at__isnull=True).update(arrived_at=None)
    hand_out(options, by=by)


def deliver(order, *, by, item_ids=None):
    """Hand over arrived lines at the store (``None``: every arrived line not delivered yet).

    What's paid must cover every line delivered so far, so nobody leaves with
    copies they haven't paid for. The order is entregado once every line is.
    """
    if order.status == 'entregado':
        raise PresaleError('already_delivered')
    if order.delivery != 'pickup':
        raise PresaleError('not_pickup')
    if order.status in ('cancelado', 'vencido', 'pending_payment'):
        raise PresaleError('invalid_status')
    items = list(order.items.select_related('option'))
    pending = [item for item in items if item.delivered_at is None]
    if item_ids is None:
        chosen = [item for item in pending if item.arrived_at]
    else:
        wanted = {int(pk) for pk in item_ids}
        chosen = [item for item in pending if item.pk in wanted]
        if len(chosen) != len(wanted) or any(not item.arrived_at for item in chosen):
            raise PresaleError('invalid_items', 'Solo se entregan discos que ya llegaron y no se han entregado.')
    if not chosen:
        raise PresaleError('invalid_items', 'No hay discos por entregar.')
    covered = sum((item.line_total for item in items if item.delivered_at or item in chosen), ZERO)
    paid_for_records = paid_amount(order) - shipping_paid(order)
    if paid_for_records < covered:
        raise PresaleError('paid_not_enough', f'Faltan ${covered - paid_for_records:,.2f} para entregar esto.')
    now = timezone.now()
    for item in chosen:
        item.delivered_at, item.delivered_by = now, by
        item.save(update_fields=['delivered_at', 'delivered_by'])
    _log(order, 'delivered', ', '.join(f'{item.option.name} ×{item.quantity}' for item in chosen), by)
    if len(chosen) == len(pending):
        order.status = 'entregado'
        order.delivered_at = now
        order.delivered_by = by
        order.save(update_fields=['status', 'delivered_at', 'delivered_by', 'updated_at'])


def mark_shipped(order, *, tracking, by):
    if order.delivery != 'shipping':
        raise PresaleError('not_shipping')
    if not all_arrived(order):
        raise PresaleError('invalid_status', 'Se envía cuando llegan todas las versiones.')
    if order.status not in ('pagado',) or balance(order) > 0 or shipping_pending(order):
        raise PresaleError('balance_due' if order.status == 'pagado' else 'invalid_status')
    order.status = 'enviado'
    order.shipping_link = (tracking or '').strip()[:255]
    order.delivered_at = timezone.now()
    order.delivered_by = by
    order.save(update_fields=['status', 'shipping_link', 'delivered_at', 'delivered_by', 'updated_at'])
    _log(order, 'status', f'enviado · {order.shipping_link}', by)


def cancel(order, *, by, reason):
    reason = (reason or '').strip()
    if not reason:
        raise PresaleError('reason_required')
    if order.status in ('entregado', 'enviado', 'cancelado'):
        raise PresaleError('invalid_status')
    previous = order.status
    order.status = 'cancelado'
    order.save(update_fields=['status', 'updated_at'])
    _log(order, 'status', f'{previous} → cancelado: {reason}', by)
    _release_copies(order, by)


def expire(order, *, by):
    if not is_expirable(order):
        raise PresaleError('not_expirable')
    previous = order.status
    order.status = 'vencido'
    order.save(update_fields=['status', 'updated_at'])
    _log(order, 'status', f'{previous} → vencido', by)
    _release_copies(order, by)


def reopen(order, *, by):
    if order.status not in ('vencido', 'cancelado'):
        raise PresaleError('invalid_status')
    with transaction.atomic():
        items = list(order.items.filter(delivered_at__isnull=True))
        options = PreSaleOption.objects.select_for_update().in_bulk([item.option_id for item in items])
        for item in items:
            if copies_left(options[item.option_id]) < item.quantity:
                raise PresaleError('sold_out', f'Ya no quedan copias de {options[item.option_id].name}.')
        order.status = 'apartado'
        order.save(update_fields=['status', 'updated_at'])
        _log(order, 'status', 'reabierto', by)
        refresh_status(order, by)
    hand_out(list(options.values()), by=by)


def quote_shipping(lines, zip_code):
    """Cheapest preferred-courier quote for ``lines`` [(option, qty)] in one box to ``zip_code``.

    Returns {'cost', 'courier', 'service', 'title'}. Raises shipping.ShippingQuoteError
    on upstream failures and PresaleError('shipping_unavailable') when nothing fits.
    """
    zip_code = shipping.normalize_zip_code(zip_code)
    if not zip_code:
        raise PresaleError('invalid_zip', 'Escribe un código postal válido de 5 dígitos.')
    quotes = shipping.get_shipping_quotes(zip_code, shipping.build_package_for_items(lines))
    quote = shipping.select_cheapest_quote(quotes)
    if quote is None:
        raise PresaleError('shipping_unavailable', f'No hay opciones de envío para el código postal {zip_code}.')
    return {
        'cost': Decimal(str(quote.get('total', '0'))).quantize(CENT),
        'courier': str(quote.get('courier') or ''),
        'service': str(quote.get('serviceType') or ''),
        'title': str(quote.get('title') or ''),
    }


def shipping_paid(order):
    total = order.payments.filter(voided_at__isnull=True).aggregate(s=Sum('shipping_amount'))['s']
    return (total or ZERO).quantize(CENT)


ABONO_MIN = Decimal('100')
ABONO_STEP = Decimal('50')


def settle_split(order, amount=None):
    """(record part, shipping part) to charge online. No ``amount`` or the whole
    balance settles everything; otherwise an abono: >= $100, a multiple of $50,
    records only (shipping waits for the final payment)."""
    due, shipping = balance(order), shipping_due(order)
    if amount in (None, ''):
        return due - shipping, shipping
    amount = _money(amount)
    if amount == due:
        return due - shipping, shipping
    if amount < ABONO_MIN or amount % ABONO_STEP or amount > due - shipping:
        raise PresaleError('invalid_partial')
    return amount, ZERO


def shipping_due(order):
    """Part of the balance that pays shipping (only once shipping_cost is known)."""
    if order.shipping_cost is None:
        return ZERO
    return min(max(order.shipping_cost - shipping_paid(order), ZERO), balance(order))


def fulfil_presale_session(session):
    """Record a paid Stripe Checkout session for a presale. Safe to call twice."""
    if session.get('payment_status') != 'paid':
        return None
    metadata = session.get('metadata') or {}
    order = PreSaleOrder.objects.filter(pk=metadata.get('order_id')).first()
    if order is None:
        logger.error("Presale session %s references missing order %r", session.get('id'), metadata.get('order_id'))
        return None
    if PreSalePayment.objects.filter(stripe_checkout_session_id=session['id']).exists():
        return order
    amount = Decimal(session.get('amount_total') or 0) / Decimal(100)
    shipping_part = _money(metadata.get('shipping') or '0')
    with transaction.atomic():
        items = list(order.items.all())
        options = PreSaleOption.objects.select_for_update().in_bulk([item.option_id for item in items])
        order.refresh_from_db()
        hold_gone = order.hold_expires_at is None or order.hold_expires_at <= timezone.now()
        # A live hold already counts this order's own copies; only an expired one can oversell.
        if order.status == 'pending_payment' and hold_gone and any(
                copies_left(options[item.option_id]) < item.quantity for item in items):
            # The hold expired and the copy went to someone else: the money is
            # already taken, so keep the order and let the store sort it out.
            order.oversold = True
            order.save(update_fields=['oversold', 'updated_at'])
            logger.warning("Presale order %s paid after its hold expired: oversold", order.code)
        record_payment(order, amount, 'stripe', stripe_session_id=session['id'], shipping_amount=shipping_part)
        order.refresh_from_db()
    from .emailing import send_presale_email, send_presale_store_notice
    if paid_amount(order) > total_due(order):
        # Two Stripe tabs, or online + cash for the same balance: refund by hand.
        send_presale_store_notice(order, 'overpaid')
    if metadata.get('purpose') == 'reserve':
        send_presale_email(order, 'reserved')
        send_presale_store_notice(order, 'oversold' if order.oversold else 'new')
    else:
        send_presale_email(order, 'payment')
    return order


def requote_shipping(order, *, by=None):
    """Final Envíos Perros quote for a shipping order (when the record arrives)."""
    if order.delivery != 'shipping':
        raise PresaleError('not_shipping')
    if order.status in LOCKED_STATUSES:
        raise PresaleError('invalid_status')
    if shipping_paid(order) > 0:
        # Shipping already charged (paid in full at reservation): that amount is final.
        return order
    quote = quote_shipping(order_lines(order), (order.shipping_details or {}).get('zip'))
    order.shipping_cost = quote['cost']
    order.shipping_courier, order.shipping_service = quote['courier'], quote['service']
    order.save(update_fields=['shipping_cost', 'shipping_courier', 'shipping_service', 'updated_at'])
    _log(order, 'shipping_quote', f"${quote['cost']} · {quote['title']}", by)
    refresh_status(order, by)
    return order


def link_orders_to_user(user):
    """Attach admin-registered orders (Instagram / store) to the account that owns that email."""
    if not user.email:
        return 0
    return PreSaleOrder.objects.filter(user__isnull=True, buyer_email__iexact=user.email).update(user=user)
