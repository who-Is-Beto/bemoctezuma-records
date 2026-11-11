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

from ..models import PreSaleOption, PreSaleOrder, PreSaleOrderEvent, PreSalePayment

logger = logging.getLogger(__name__)

DEPOSIT_DAYS = 120
AFTER_ARRIVAL_DAYS = 15
HOLD_MINUTES = 30
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

def copies_left(option):
    taken = option.orders.filter(
        Q(status__in=HOLDING_STATUSES)
        | Q(status='pending_payment', hold_expires_at__gt=timezone.now())
    ).count()
    return option.copies - taken


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
    """Reservation + 120 days, never before arrival + 15 days, extended by the admin."""
    dates = [timezone.localdate(order.reserved_at) + timedelta(days=DEPOSIT_DAYS)]
    if order.option.arrived_at:
        dates.append(timezone.localdate(order.option.arrived_at) + timedelta(days=AFTER_ARRIVAL_DAYS))
    if order.deadline_override:
        dates.append(order.deadline_override)
    return max(dates)


def days_left(order):
    return (deadline(order) - timezone.localdate()).days


def is_expirable(order):
    return (
        order.status in ('apartado', 'pagado')
        and order.option.arrived_at is not None
        and timezone.localdate() > deadline(order)
        and (balance(order) > 0 or shipping_pending(order))
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
    if paid >= order.item_price:
        status = 'pagado'
        if order.paid_at is None:
            order.paid_at = timezone.now()
    else:
        status = 'apartado'
        order.paid_at = None
    if (status == 'pagado' and order.delivery == 'pickup' and order.option.arrived_at
            and balance(order) == 0):
        status = 'listo'
    order.status = status
    if status != 'pending_payment':
        order.hold_expires_at = None
    order.save(update_fields=['status', 'paid_at', 'hold_expires_at', 'updated_at'])
    if status != previous:
        _log(order, 'status', f'{previous} → {status}', actor)


def split_amount(option, amount, delivery, shipping_quote):
    """What a reservation of ``amount`` means: deposit or full payment (with discount)."""
    amount = _money(amount)
    price = option.price
    discount = option.full_payment_discount or ZERO
    full = price - discount
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
            'discount': discount,
            'shipping_cost': shipping,
            'charge': full + (shipping or ZERO),
        }
    if amount < option.min_deposit:
        raise PresaleError('amount_too_low')
    return {'item_price': price, 'discount': ZERO, 'shipping_cost': None, 'charge': amount}


def _check_open(option):
    presale = option.presale
    if presale.status != 'published':
        raise PresaleError('not_published')
    if not option.is_active:
        raise PresaleError('inactive')
    if timezone.localdate() > presale.purchase_deadline:
        raise PresaleError('deadline_passed')


def create_order(option, *, buyer, source, delivery, shipping_details, amount, method=None,
                 shipping_quote=None, created_by=None, accept_terms, note=''):
    """Reserve one copy. ``buyer`` is a User (online) or {'name','email','phone'} (admin).

    Online (method=None): the order waits in pending_payment with a 30-minute hold
    while the customer pays in Stripe. Admin: the payment is recorded right away.
    """
    if not accept_terms:
        raise PresaleError('terms_required')
    split = split_amount(option, amount, delivery, shipping_quote)
    with transaction.atomic():
        option = PreSaleOption.objects.select_for_update().select_related('presale').get(pk=option.pk)
        if method is not None or source != 'online':
            # Admins may register buyers after the public deadline (Instagram DMs).
            if not option.is_active:
                raise PresaleError('inactive')
        else:
            _check_open(option)
        if copies_left(option) <= 0:
            raise PresaleError('sold_out')

        now = timezone.now()
        if isinstance(buyer, dict):
            user = None
            name, email, phone = buyer.get('name', ''), buyer.get('email', ''), buyer.get('phone', '')
        else:
            user = buyer
            name = buyer.get_full_name() or buyer.username
            email, phone = buyer.email, ''
        details = shipping_details or {}
        if details.get('phone') and not phone:
            phone = details['phone']
        order = PreSaleOrder.objects.create(
            option=option,
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
        _log(order, 'reserved', f'{option.name} · {source}', created_by)
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
    return payment


def void_payment(payment, *, by, reason):
    reason = (reason or '').strip()
    if not reason:
        raise PresaleError('reason_required')
    if payment.voided_at:
        return
    payment.voided_at = timezone.now()
    payment.voided_by = by
    payment.void_reason = reason[:200]
    payment.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
    order = payment.order
    _log(order, 'void', f'${payment.amount}: {reason}', by)
    refresh_status(order, by)


def mark_arrived(option, *, by):
    option.arrived_at = option.arrived_at or timezone.now()
    option.save(update_fields=['arrived_at'])
    orders = list(option.orders.exclude(status__in=('pending_payment',) + LOCKED_STATUSES))
    for order in orders:
        order.option = option
        _log(order, 'arrived', option.name, by)
        refresh_status(order, by)
    presale = option.presale
    if not presale.options.filter(arrived_at__isnull=True, is_active=True).exists():
        logger.info("All options of presale %s arrived", presale.pk)
    return orders


def deliver(order, *, by):
    if order.status == 'entregado':
        raise PresaleError('already_delivered')
    if order.delivery != 'pickup':
        raise PresaleError('not_pickup')
    if order.status in ('cancelado', 'vencido', 'pending_payment'):
        raise PresaleError('invalid_status')
    if balance(order) > 0 or order.status != 'listo':
        raise PresaleError('balance_due' if balance(order) > 0 else 'invalid_status')
    order.status = 'entregado'
    order.delivered_at = timezone.now()
    order.delivered_by = by
    order.save(update_fields=['status', 'delivered_at', 'delivered_by', 'updated_at'])
    _log(order, 'delivered', '', by)


def mark_shipped(order, *, tracking, by):
    if order.delivery != 'shipping':
        raise PresaleError('not_shipping')
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


def expire(order, *, by):
    if not is_expirable(order):
        raise PresaleError('not_expirable')
    previous = order.status
    order.status = 'vencido'
    order.save(update_fields=['status', 'updated_at'])
    _log(order, 'status', f'{previous} → vencido', by)


def reopen(order, *, by):
    if order.status not in ('vencido', 'cancelado'):
        raise PresaleError('invalid_status')
    with transaction.atomic():
        option = PreSaleOption.objects.select_for_update().get(pk=order.option_id)
        if copies_left(option) <= 0:
            raise PresaleError('sold_out')
        order.status = 'apartado'
        order.save(update_fields=['status', 'updated_at'])
        _log(order, 'status', 'reabierto', by)
        refresh_status(order, by)
