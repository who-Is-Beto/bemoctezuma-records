import uuid

from django.conf import settings
from django.db import models

from .catalog import Record


class Wishlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlists', blank=True, null=True)
    wishlist_code = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wishlist {self.wishlist_code}"

class WishlistItem(models.Model):
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name='wishlist_items')
    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='wishlist_records')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('wishlist', 'record')

    def __str__(self):
        return f"{self.record.title} in wishlist {self.wishlist.wishlist_code}"