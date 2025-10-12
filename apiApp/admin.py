from django.contrib.auth.admin import UserAdmin
from django.contrib import admin
from .models import User, Category, Record, Artist, Genere, Cart, CartItem

class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name')
admin.site.register(User, CustomUserAdmin)

class GenereAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'slug')
    prepopulated_fields = {'slug': ('name',)}
admin.site.register(Genere, GenereAdmin)

class ArtistAdmin(admin.ModelAdmin):
    list_display = ('name', 'bio', 'slug', 'profile_picture_url')
    prepopulated_fields = {'slug': ('name',)}
admin.site.register(Artist, ArtistAdmin)

class RecordAdmin(admin.ModelAdmin):
    list_display = ("title", "artist", "genere", "price", "stock", "release_date", "featured")
admin.site.register(Record, RecordAdmin)

class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'slug')
    prepopulated_fields = {'slug': ('name',)}
admin.site.register(Category, CategoryAdmin)

admin.site.register([Cart, CartItem])

# Register your models here.
