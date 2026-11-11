from django.contrib.auth.admin import UserAdmin
from django.contrib import admin
from .models import User, Category, Record, Artist, Genere, Cart, CartItem, Wishlist, WishlistItem, Review, RecordRatingSummary, Order, OrderItem, Owner, Sale, SaleItem

class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name')
admin.site.register(User, CustomUserAdmin)

class GenereAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'slug')
    prepopulated_fields = {'slug': ('name',)}
admin.site.register(Genere, GenereAdmin)

class ArtistAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
admin.site.register(Artist, ArtistAdmin)

class RecordAdmin(admin.ModelAdmin):
    list_display = ("title", "artist", "category", "price", "stock", "release_date", "featured")
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ('generes',)
admin.site.register(Record, RecordAdmin)

class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'slug')
    prepopulated_fields = {'slug': ('name',)}
admin.site.register(Category, CategoryAdmin)


admin.site.register([Wishlist, WishlistItem])
admin.site.register([Cart, CartItem, Review, RecordRatingSummary, Order, OrderItem])
# No owner edit UI in Administración: this is where a mistyped owner email gets fixed.
admin.site.register([Owner, Sale, SaleItem])


from .models import PreSale, PreSaleOption, PreSaleOrder, PreSalePayment, PreSaleSubscriber


class PreSaleOptionInline(admin.TabularInline):
    model = PreSaleOption
    extra = 0


class PreSaleAdmin(admin.ModelAdmin):
    list_display = ('title', 'artist', 'status', 'purchase_deadline')
    inlines = [PreSaleOptionInline]
admin.site.register(PreSale, PreSaleAdmin)


class PreSalePaymentInline(admin.TabularInline):
    model = PreSalePayment
    extra = 0


class PreSaleOrderAdmin(admin.ModelAdmin):
    list_display = ('code', 'buyer_name', 'option', 'status', 'reserved_at')
    search_fields = ('code', 'buyer_name', 'buyer_email')
    inlines = [PreSalePaymentInline]
admin.site.register(PreSaleOrder, PreSaleOrderAdmin)
admin.site.register(PreSaleSubscriber)
