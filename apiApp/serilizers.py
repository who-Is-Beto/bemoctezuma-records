from rest_framework import serializers
from .models import Record, Category, User, CartItem, Cart, Wishlist, WishlistItem

class RecordDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Record
        fields = '__all__'
        # fields = ['id', 'title', 'artist', 'description', 'genere', 'cover_image_url', 'price', 'discount_porcentage', 'stock', 'slug', 'release_date', 'category']
        # extra_kwargs = {
        #     'slug': {'required': False},
        #     'category': {'required': False},  
        #     'user': {'required': False},

class RecordListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Record
        fields = ['id', 'title', 'artist', 'genere', 'cover_image_url', 'price', 'discount_porcentage', 'stock', 'slug', 'category']
        # extra_kwargs = {
        #     'slug': {'required': False},
        #     'category': {'required': False},  
        #     'user': {'required': False},

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class CategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'profile_picture_url']
        extra_kwargs = {
            'password': {'write_only': True},
            'profile_picture_url': {'required': False},
        }

class CartItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)
    subtotal = serializers.SerializerMethodField()
    class Meta:
        model = CartItem
        fields = ['id', 'record', 'quantity', 'subtotal']

    def get_subtotal(self, cart_item):
        return cart_item.quantity * cart_item.record.price
    
class CartSerializer(serializers.ModelSerializer):
    cart_items = CartItemSerializer(many=True, read_only=True)
    total_price = serializers.SerializerMethodField()
    class Meta:
        model = Cart
        fields = ['id', 'cart_code', 'created_at', 'updated_at', 'cart_items', 'total_price']

    def get_total_price(self, cart):
        total = sum(item.quantity * item.record.price for item in cart.cart_items.all())
        return total

class CartStatSerializer(serializers.ModelSerializer):
    total_quantity = serializers.SerializerMethodField()
    class Meta:
        model = Cart
        fields = ['id', 'cart_code', 'total_quantity']

    def get_total_quantity(self, cart):
        total_quantity = sum(item.quantity for item in cart.cart_items.all())
        return total_quantity

class WishlistItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ['id', 'record', 'added_at']


class WishlistSerializer(serializers.ModelSerializer):
    wishlist_items = WishlistItemSerializer(many=True, read_only=True)

    class Meta:
        model = Wishlist
        fields = ['id', 'wishlist_code', 'created_at', 'updated_at', 'wishlist_items']
