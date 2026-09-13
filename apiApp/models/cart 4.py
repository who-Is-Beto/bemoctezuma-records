from django.conf import settings
from django.db import models

from .catalog import Record
from .common import generate_cart_code


class Cart(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='carts', blank=True, null=True)
    cart_code = models.CharField(max_length=100, unique=True, default=generate_cart_code)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.cart_code

class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='cart_items')
    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='records')
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.quantity} x {self.record.title} in cart {self.cart.cart_code}"