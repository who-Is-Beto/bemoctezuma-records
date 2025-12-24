from rest_framework import serializers
from .models import Record, Category, CartItem, Cart, Wishlist, WishlistItem, Review, Artist, Genere
from django.contrib.auth import get_user_model

class UserRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ['id', 'username', 'email', 'password', 'first_name', 'last_name', 'adress']
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        user = get_user_model().objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            adress=validated_data.get('adress', '')
        )
        user.set_password(validated_data['password'])
        user.save()
        return user

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ['id', 'username', 'email', 'first_name', 'last_name']

class ArtistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artist
        fields = ['id', 'name', 'slug']

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class CategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]

class GenereSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genere
        fields = ["id", "name", "slug", "description"]

class RecordDetailSerializer(serializers.ModelSerializer):
    artist = ArtistSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    genere = GenereSerializer(read_only=True)
    class Meta:
        model = Record
        fields = '__all__'
        
class RecordListSerializer(serializers.ModelSerializer):
    artist = ArtistSerializer(read_only=True)
    category = CategoryListSerializer(read_only=True)
    genere = GenereSerializer(read_only=True)
    class Meta:
        model = Record
        fields = ['id', 'title', 'condition', 'category', 'artist', 'genere', 'cover_image_url', 'price', 'discount_porcentage', 'stock', 'slug', 'category']


class  UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ['id', 'username', 'email', 'first_name', 'last_name']

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
        fields = ['id', 'user', 'cart_code', 'created_at', 'updated_at', 'cart_items', 'total_price']

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

class ReviewSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = Review
        fields = ['id', 'user', 'rating', 'review', 'created_at', 'updated_at']
