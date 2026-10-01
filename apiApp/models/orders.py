from decimal import Decimal

from django.db import models

from .catalog import Owner, Record


class Order(models.Model):
    status_choices = [
        ('pending', 'Pendiente'),
        ('paid', 'Pagado'),
        ('shipped', 'Enviado'),
        ('delivered', 'Entregado'),
        ('canceled', 'Cancelado'),
    ]
    stripe_checkout_session_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10)
    user_email = models.EmailField()
    shipped_to = models.CharField(max_length=255)
    shipping_details = models.JSONField(null=True, blank=True, default=dict)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shipping_courier = models.CharField(max_length=50, blank=True, default="")
    shipping_service = models.CharField(max_length=50, blank=True, default="")
    shipping_link = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=50, choices=status_choices, default='pending')
    # Set when shipped_to == 'bazar': the bazar where the customer picks up.
    # SET_NULL keeps order history intact if the bazar is later deleted.
    # Lazy string reference because Bazar is defined below Order in this file.
    pickup_bazar = models.ForeignKey(
        'apiApp.Bazar', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Orden {self.id} - {self.status}"

class OrderItem(models.Model):
    # record is nullable (SET_NULL): permanently deleting a record must keep
    # historical orders intact (quantity + snapshotted price survive).
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='order_items')
    record = models.ForeignKey(Record, on_delete=models.SET_NULL, null=True, blank=True)
    # Whose copies were sold (like SaleItem.owner); null = store stock.
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, null=True, blank=True, related_name='order_items')
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        title = self.record.title if self.record else "(disco eliminado)"
        return f"{self.quantity} x {title} en la orden {self.order.id}"

class Sale(models.Model):
    """A sale registered by hand in Punto de venta: one ticket, one or more records."""
    PAYMENT_METHODS = (
        ('cash', 'Efectivo'),
        ('card', 'Tarjeta'),
        ('transfer', 'Transferencia'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Blank only on sales registered before the payment method was recorded.
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, blank=True, default='')
    # Card commission, % of the whole ticket (0 for cash/transfer).
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    # Items subtotal minus the commission: what the store actually takes in.
    final_sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    def __str__(self):
        return f"Venta {self.id}"

class SaleItem(models.Model):
    # record is SET_NULL (like OrderItem) so deleting a record keeps the history.
    # owner is who owned the record when it sold; PROTECT so history never loses it.
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    record = models.ForeignKey(Record, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_items')
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, null=True, blank=True, related_name='sale_items')
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # This line's share of the ticket commission (proportional to its subtotal).
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    email_sent = models.BooleanField(default=False)
    # Snapshot at sale time so receipts reprint the same after the record changes or is deleted.
    title = models.CharField(max_length=255, blank=True, default='')
    artist = models.CharField(max_length=255, blank=True, default='')
    cover_image_url = models.URLField(max_length=200, blank=True, null=True)

    def __str__(self):
        return f"{self.quantity} x {self.title or '(disco eliminado)'} en la venta {self.sale_id}"