from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify


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

    @property
    def effective_price(self):
        """Always-computed customer-facing price from price + discount.

        Falls back to the stored ``sell_price`` when price is missing, but
        otherwise recalculates so stale DB values (e.g. ``sell_price=0`` from
        a raw-SQL import) never leak into the cart / checkout.
        """
        price = Decimal(str(self.price)) if self.price is not None else Decimal('0')
        discount = Decimal(str(self.discount_porcentage or 0))
        return (price * (1 - discount / 100)).quantize(Decimal('0.01'))

    def save(self, *args, **kwargs):
        # Auto-calculate sell_price from price and discount_porcentage
        self.sell_price = self.effective_price

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