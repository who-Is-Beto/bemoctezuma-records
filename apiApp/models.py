import uuid
from decimal import Decimal

from django.db import models
from django.utils.text import slugify
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.conf import settings

def generate_cart_code():
    return uuid.uuid4().hex


class User(AbstractUser):
    ROLES = (
        ('ADMIN', 'Admin'),
        ('CUSTOMER', 'Customer'),
    )

    username = models.CharField(max_length=150, unique=True)
    first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150, blank=True, null=True)
    email = models.EmailField(unique=True)
    adress = models.TextField(blank=True, null=True)
    profile_picture_url = models.ImageField(blank=True, null=True)
    email_verified = models.BooleanField(default=False)
    role = models.CharField(max_length=10, choices=ROLES, default='CUSTOMER')

    def __str__(self):
        return self.username
    
class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    slug = models.SlugField(default="", null=False, unique=True, blank=True)
    image_url = models.URLField(max_length=200, blank=True, null=True)
    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            max_len = self._meta.get_field('slug').max_length
            base_slug = slugify(self.name)[:max_len].rstrip('-')
            unique_slug = base_slug
            counter = 1
            while Category.objects.filter(slug=unique_slug).exists():
                suffix = f"-{counter}"
                unique_slug = f"{base_slug[:max_len - len(suffix)]}{suffix}"
                counter += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)

class Artist(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(default="", null=False, unique=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            max_len = self._meta.get_field('slug').max_length
            base_slug = slugify(self.name)[:max_len].rstrip('-')
            unique_slug = base_slug
            counter = 1
            while Artist.objects.filter(slug=unique_slug).exists():
                suffix = f"-{counter}"
                unique_slug = f"{base_slug[:max_len - len(suffix)]}{suffix}"
                counter += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)

class Genere(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    slug = models.SlugField(default="", null=False, unique=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            max_len = self._meta.get_field('slug').max_length
            base_slug = slugify(self.name)[:max_len].rstrip('-')
            unique_slug = base_slug
            counter = 1
            while Genere.objects.filter(slug=unique_slug).exists():
                suffix = f"-{counter}"
                unique_slug = f"{base_slug[:max_len - len(suffix)]}{suffix}"
                counter += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)
    
class Record(models.Model):
    CONDITIONS = (
        ('M', 'Mint'),
        ('NM', 'Near Mint'),
        ('NM-', 'Near Mint Minus'),
        ('VG+', 'Very Good Plus'),
        ('VG', 'Very Good'),
        ('G', 'Good'),
        ('F', 'Fair'),
        ('P', 'Poor'),
    )
    title = models.CharField(max_length=255)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='records', blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    condition = models.CharField(max_length=4, choices=CONDITIONS, default='M')
    genere = models.ForeignKey(Genere, on_delete=models.CASCADE, related_name='records', blank=True, null=True)
    cover_image_url = models.URLField(max_length=200, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sell_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_porcentage = models.PositiveBigIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    stock = models.PositiveIntegerField()
    images = models.JSONField(default=list, blank=True)
    slug = models.SlugField(default="", blank=True, null=False)
    release_date = models.PositiveIntegerField(blank=True, null=True, default=2025)
    featured = models.BooleanField(default=True)
    items_inside = models.PositiveIntegerField(default=1)
    # Weight in grams of a single unit (one items_inside set). When null,
    # shipping weight falls back to the category-based default in services.py
    # (LP 300g, 7" 100g, CD 85g).
    weight_grams = models.PositiveIntegerField(blank=True, null=True, validators=[MinValueValidator(0)])
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='records', blank=True, null=True)

    def __str__(self):
        return f"{self.title} by {self.artist}"
    
    def save(self, *args, **kwargs):
        # Auto-calculate sell_price from price and discount_porcentage
        price = Decimal(str(self.price)) if self.price is not None else Decimal('0')
        discount = Decimal(str(self.discount_porcentage or 0))
        self.sell_price = (price * (1 - discount / 100)).quantize(Decimal('0.01'))

        if not self.slug:
            max_len = self._meta.get_field('slug').max_length
            base_slug = slugify(self.title)[:max_len].rstrip('-')
            unique_slug = base_slug
            counter = 1
            while Record.objects.filter(slug=unique_slug).exists():
                suffix = f"-{counter}"
                unique_slug = f"{base_slug[:max_len - len(suffix)]}{suffix}"
                counter += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)

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

class Review(models.Model):

    RATING_CHOICES = [
        (1, "Pa' la basura"),
        (2, '2 - Mas o menos'),
        (3, '3 - Promedio'),
        (4, "4 - Ta' bueno"),
        (5, '5 - Maravilloso'),
    ]

    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveIntegerField(choices=RATING_CHOICES)
    review = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"Review of {self.record.title} by {self.user.username}"
    class Meta:
        unique_together = ('record', 'user')
        ordering = ['-created_at']

class RecordRatingSummary(models.Model):
    record = models.OneToOneField(Record, on_delete=models.CASCADE, related_name='rating_summary')
    average_rating = models.FloatField(default=0.0)
    total_reviews = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Rating Summary for {self.record.title}"
    
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
