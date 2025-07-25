from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator

# Create your models here.

class User(AbstractUser):
    # profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    profile_picture_url = models.ImageField(blank=True, null=True)

    def __str__(self):
        return self.username
    
class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    slug = models.SlugField(default="", null=False)
    image_url = models.ImageField(upload_to='category_img', blank=True, null=True)
    def __str__(self):
        return self.name
    
class Record(models.Model):
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    genre = models.CharField(max_length=100)
    cover_image_url = models.ImageField(upload_to='product_img', blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_porcentage = models.PositiveBigIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    stock = models.PositiveIntegerField()
    slug = models.SlugField(default="", blank=True, null=False)
    release_date = models.DateField()
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='records', blank=True, null=True)
    featured = models.BooleanField(default=True)
    # user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='records')

    def __str__(self):
        return f"{self.title} by {self.artist}"