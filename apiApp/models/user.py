from django.contrib.auth.models import AbstractUser
from django.db import models

from ..admin_panel import CUSTOM_PERMISSIONS


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

    class Meta:
        # Administración access + per-tab visibility, granted through custom roles.
        permissions = CUSTOM_PERMISSIONS

    def __str__(self):
        return self.username