from django.db import models

from .catalog import Record


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
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        title = self.record.title if self.record else "(disco eliminado)"
        return f"{self.quantity} x {title} en la orden {self.order.id}"