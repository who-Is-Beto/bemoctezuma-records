"""Preventas: upcoming records sold ahead with a deposit (apartado) or paid in full.

A PreSale has one or more options (colors, formats, editions); a PreSaleOrder
holds lines (PreSaleOrderItem: an option × quantity) under one code and balance. Money paid is never stored on the order: it is the sum
of its PreSalePayment rows (the ledger), so voiding a payment is the only fix.
Rules live in services/presales.py.
"""
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from .catalog import Category, Owner

CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # no 0/O, 1/I/L


def generate_presale_code():
    return 'PV-' + ''.join(secrets.choice(CODE_ALPHABET) for _ in range(4))


class PreSale(models.Model):
    STATUSES = (
        ('draft', 'Borrador'),
        ('published', 'Publicada'),
        ('closed', 'Cerrada'),
    )
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255)
    slug = models.SlugField(default="", null=False, unique=True, blank=True)
    description = models.TextField(blank=True, default='')
    cover = models.ImageField(upload_to='presales/%Y/%m/', blank=True, null=True)
    # Store day; after it nobody new can reserve.
    purchase_deadline = models.DateField()
    # Free text: "Enero 2027", "Finales de marzo".
    estimated_arrival = models.CharField(max_length=60, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUSES, default='draft')
    # Set once the announcement email went out: a presale is announced only once.
    announced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        # Publishing emails every subscriber, so it's granted apart from editing.
        permissions = [('publish_presale', 'Publicar preventas y avisar a suscriptores')]

    def __str__(self):
        return f"{self.title} — {self.artist}"

    def save(self, *args, **kwargs):
        if not self.slug:
            max_len = self._meta.get_field('slug').max_length
            base_slug = slugify(f"{self.title}")[:max_len].rstrip('-') or 'preventa'
            unique_slug = base_slug
            counter = 1
            while PreSale.objects.filter(slug=unique_slug).exists():
                suffix = f"-{counter}"
                unique_slug = f"{base_slug[:max_len - len(suffix)]}{suffix}"
                counter += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)


class PreSaleOption(models.Model):
    """One version a buyer can pick: "Vinilo rojo", "CD", "Edición 2LP"."""
    presale = models.ForeignKey(PreSale, on_delete=models.CASCADE, related_name='options')
    name = models.CharField(max_length=120)
    # What the buyer gets: "2 LP + póster", shown under the name.
    description = models.CharField(max_length=200, blank=True, default='')
    # Pieces in the package (records, CDs, extras), like Record.items_inside.
    items_inside = models.PositiveSmallIntegerField(default=1)
    image = models.ImageField(upload_to='presales/%Y/%m/', blank=True, null=True)
    # Format: drives the shipping weight like a record's category.
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='presale_options')
    weight_grams = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    min_deposit = models.DecimalField(max_digits=10, decimal_places=2)
    # Fixed MXN off when the whole price is paid at reservation.
    full_payment_discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    # Private, like Record.cost_price / owner: only admin payloads expose them.
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, null=True, blank=True,
                              related_name='presale_options')
    copies = models.PositiveIntegerField()
    # Most copies of this version one buyer can hold online; empty = no limit.
    max_per_buyer = models.PositiveIntegerField(null=True, blank=True)
    # Hidden from new reservations (an option with orders is never deleted).
    is_active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    # Set once every copy is at the store; copies can arrive in several batches.
    arrived_at = models.DateTimeField(null=True, blank=True)
    # Copies at the store so far, handed to order lines by priority (services.presales.allocate_arrivals).
    arrived_copies = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('position', 'id')

    def __str__(self):
        return f"{self.presale.title} · {self.name}"

    def clean(self):
        errors = {}
        if self.price is not None and self.min_deposit is not None:
            if not (Decimal('0') < self.min_deposit < self.price):
                errors['min_deposit'] = 'El apartado mínimo debe ser mayor a 0 y menor al precio.'
        discount = self.full_payment_discount or Decimal('0')
        if self.price is not None and not (Decimal('0') <= discount < self.price):
            errors['full_payment_discount'] = 'El descuento debe ser menor al precio.'
        if errors:
            raise ValidationError(errors)


class PreSaleOrder(models.Model):
    """One buyer's reservation: one or more lines, one code, one payment ledger."""
    SOURCES = (
        ('online', 'Sitio'),
        ('instagram', 'Instagram'),
        ('store', 'Tienda'),
        ('other', 'Otro'),
    )
    STATUSES = (
        ('pending_payment', 'Pago pendiente'),
        ('apartado', 'Apartado'),
        ('pagado', 'Pagado'),
        ('listo', 'Listo para recoger'),
        ('enviado', 'Enviado'),
        ('entregado', 'Entregado'),
        ('vencido', 'Apartado vencido'),
        ('cancelado', 'Cancelado'),
    )
    DELIVERY = (
        ('pickup', 'Recoger en tienda'),
        ('shipping', 'Envío a domicilio'),
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='presale_orders')
    buyer_name = models.CharField(max_length=120)
    buyer_email = models.EmailField(blank=True, default='')
    buyer_phone = models.CharField(max_length=30, blank=True, default='')
    source = models.CharField(max_length=20, choices=SOURCES, default='online')
    code = models.CharField(max_length=12, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default='pending_payment')

    delivery = models.CharField(max_length=20, choices=DELIVERY, default='pickup')
    shipping_details = models.JSONField(default=dict, blank=True)
    # Quote shown at reservation; the final one is shipping_cost (set when paid
    # in full at reservation, or re-quoted when the record arrives).
    shipping_estimate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shipping_courier = models.CharField(max_length=50, blank=True, default='')
    shipping_service = models.CharField(max_length=50, blank=True, default='')
    shipping_link = models.CharField(max_length=255, blank=True, default='')

    # Snapshot: sum of the lines, minus their discounts when paid in full at reservation.
    item_price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_applied = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    reserved_at = models.DateTimeField()
    # Admin extension; the deadline is never earlier than the computed one.
    deadline_override = models.DateField(null=True, blank=True)
    # Online reservations hold the copy while the customer is in Stripe.
    hold_expires_at = models.DateTimeField(null=True, blank=True)
    # When the record price got fully covered: the day it counts in Ventas.
    paid_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.JSONField(default=list, blank=True)
    delivered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='presales_delivered')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='presales_created')
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_version = models.CharField(max_length=20, blank=True, default='')
    # Paid after its hold expired while the last copy went to someone else.
    oversold = models.BooleanField(default=False)
    # Consignor owner already got the sale email for this order.
    owner_notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-reserved_at']

    def __str__(self):
        return f"{self.code} · {self.buyer_name}"

    def save(self, *args, **kwargs):
        if not self.code:
            code = generate_presale_code()
            while PreSaleOrder.objects.filter(code=code).exists():
                code = generate_presale_code()
            self.code = code
        super().save(*args, **kwargs)


class PreSaleOrderItem(models.Model):
    """One version in an order: how many copies and the price they were reserved at."""
    order = models.ForeignKey(PreSaleOrder, on_delete=models.CASCADE, related_name='items')
    option = models.ForeignKey(PreSaleOption, on_delete=models.PROTECT, related_name='items')
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    # Full-payment discount per copy (0 unless paid in full at reservation).
    unit_discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    # This line's copies are at the store and set aside for this buyer.
    arrived_at = models.DateTimeField(null=True, blank=True)
    # Pickup can hand over the lines that arrived before the rest.
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='presale_items_delivered')

    class Meta:
        ordering = ('id',)

    def __str__(self):
        return f"{self.option.name} ×{self.quantity}"

    @property
    def line_total(self):
        return (self.unit_price - self.unit_discount) * self.quantity


class PreSalePayment(models.Model):
    """One ledger line. Never edited or deleted: a mistake is voided."""
    METHODS = (
        ('stripe', 'Tarjeta en línea'),
        ('cash', 'Efectivo'),
        ('card', 'Tarjeta en tienda'),
        ('transfer', 'Transferencia'),
    )
    order = models.ForeignKey(PreSaleOrder, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHODS)
    note = models.CharField(max_length=120, blank=True, default='')
    # Part of `amount` that paid shipping (pass-through, outside Ventas gross).
    shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    stripe_checkout_session_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='presale_payments')
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='presale_payments_voided')
    void_reason = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f"{self.amount} ({self.method}) · {self.order.code}"


class PreSaleOrderEvent(models.Model):
    """History line: who did what to an order, and when."""
    order = models.ForeignKey(PreSaleOrder, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=40)
    note = models.CharField(max_length=255, blank=True, default='')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='presale_events')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']


class PreSaleSubscriber(models.Model):
    """Someone who asked to hear about new presales (double opt-in)."""
    email = models.EmailField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='presale_subscriptions')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        super().save(*args, **kwargs)
