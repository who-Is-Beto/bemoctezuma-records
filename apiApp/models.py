import uuid

from django.db import models
from django.utils.text import slugify
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.conf import settings

def generate_cart_code():
    return uuid.uuid4().hex


class User(AbstractUser):
    profile_picture = models.URLField(blank=True, null=True)
    username = models.CharField(max_length=150, unique=True)
    first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150, blank=True, null=True)
    email = models.EmailField(unique=True)
    profile_picture_url = models.ImageField(blank=True, null=True)

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
            self.slug = slugify(self.title)
            unique_slug = self.slug
            counter = 1
            if Record.objects.filter(slug=unique_slug).exists():
                unique_slug = f"{self.slug}-{counter}"
                counter += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)

class Artist(models.Model):
    name = models.CharField(max_length=255)
    bio = models.TextField(blank=True, null=True)
    slug = models.SlugField(default="", null=False, unique=True, blank=True)
    profile_picture_url = models.ImageField(upload_to='artist_img', blank=True, null=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            unique_slug = self.slug
            counter = 1
            if Artist.objects.filter(slug=unique_slug).exists():
                unique_slug = f"{self.slug}-{counter}"
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
            self.slug = slugify(self.name)
            unique_slug = self.slug
            counter = 1
            if Genere.objects.filter(slug=unique_slug).exists():
                unique_slug = f"{self.slug}-{counter}"
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
    discount_porcentage = models.PositiveBigIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    stock = models.PositiveIntegerField()
    slug = models.SlugField(default="", blank=True, null=False)
    release_date = models.PositiveIntegerField(blank=True, null=True, default=2025)
    featured = models.BooleanField(default=True)
    items_inside = models.PositiveIntegerField(default=1)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='records', blank=True, null=True)

    def __str__(self):
        return f"{self.title} by {self.artist}"
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
            unique_slug = self.slug
            counter = 1
            if Record.objects.filter(slug=unique_slug).exists():
                unique_slug = f"{self.slug}-{counter}"
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
    status = models.CharField(max_length=50, choices=status_choices, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Orden {self.id} - {self.status}"
    
class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='order_items')
    record = models.ForeignKey(Record, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.record.title} en la orden {self.order.id}"