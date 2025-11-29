import uuid

from django.db import models
from django.utils.text import slugify
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.conf import settings

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
    image = models.ImageField(upload_to='category_img', blank=True, null=True)
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
    title = models.CharField(max_length=255)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='records', blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    genere = models.ForeignKey(Genere, on_delete=models.CASCADE, related_name='records', blank=True, null=True)
    cover_image_url = models.URLField(max_length=200, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_porcentage = models.PositiveBigIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    stock = models.PositiveIntegerField()
    slug = models.SlugField(default="", blank=True, null=False)
    release_date = models.DateField()
    featured = models.BooleanField(default=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='records', blank=True, null=True)
    # user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='records')

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
    cart_code = models.CharField(max_length=100, unique=True)
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