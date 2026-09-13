from rest_framework import serializers
from .models import Record, Category, CartItem, Cart, Wishlist, WishlistItem, Review, Artist, Genere, Order, OrderItem, Bazar
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode


def _normalize_decimal_string(value):
    """Accept price strings with comma decimals ("500,00") or dots ("500.00").

    Front-end forms in es-MX locale commonly submit "1234,56" which
    Python's Decimal() rejects.  This normalises to "1234.56" before
    DRF's DecimalField runs its own validation.
    """
    if isinstance(value, str):
        value = value.strip().replace(',', '.')
    return value


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
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'email_verified']

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
    sell_price = serializers.SerializerMethodField()

    class Meta:
        model = Record
        fields = '__all__'

    def get_sell_price(self, obj):
        """Always return the computed price so stale DB values never leak."""
        return str(obj.effective_price)
        
class RecordListSerializer(serializers.ModelSerializer):
    artist = ArtistSerializer(read_only=True)
    category = CategoryListSerializer(read_only=True)
    genere = GenereSerializer(read_only=True)
    sell_price = serializers.SerializerMethodField()

    class Meta:
        model = Record
        fields = ['id', 'title', 'condition', 'category', 'artist', 'genere', 'cover_image_url', 'price', 'cost_price', 'sell_price', 'final_sale_price', 'discount_porcentage', 'stock', 'slug', 'images']

    def get_sell_price(self, obj):
        """Always return the computed price so stale DB values never leak."""
        return str(obj.effective_price)



class RecordCreateSerializer(serializers.ModelSerializer):
    """Write-only serializer for creating a record via the admin inventory form.

    FKs are accepted as plain IDs (not nested objects).
    sell_price is auto-calculated from price + discount_porcentage in Record.save().
    """

    class Meta:
        model = Record
        fields = [
            'title', 'artist', 'description', 'condition', 'genere',
            'cover_image_url', 'price', 'cost_price', 'sell_price',
            'discount_porcentage', 'stock', 'images', 'release_date',
            'featured', 'items_inside', 'weight_grams', 'category',
        ]
        extra_kwargs = {
            'price': {'required': True},
            'sell_price': {'read_only': True},
        }

    def create(self, validated_data):
        """sell_price is auto-calculated in Record.save() from price + discount."""
        return super().create(validated_data)

    def to_internal_value(self, data):
        # Normalise comma-decimal strings before DRF field validation
        for field in ('price', 'cost_price', 'final_sale_price'):
            if field in data:
                data[field] = _normalize_decimal_string(data[field])
        return super().to_internal_value(data)

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("El precio debe ser mayor a 0.")
        return value

    def validate_sell_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("El precio de venta debe ser mayor a 0.")
        return value

    def validate_stock(self, value):
        if value < 0:
            raise serializers.ValidationError("El stock no puede ser negativo.")
        return value

    def validate_cost_price(self, value):
        if value < 0:
            raise serializers.ValidationError("El precio de costo no puede ser negativo.")
        return value


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'email_verified', 'role']

class CartItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)
    subtotal = serializers.SerializerMethodField()
    class Meta:
        model = CartItem
        fields = ['id', 'record', 'quantity', 'subtotal']

    def get_subtotal(self, cart_item):
        return cart_item.quantity * cart_item.record.effective_price
    
class CartSerializer(serializers.ModelSerializer):
    cart_items = CartItemSerializer(many=True, read_only=True)
    total_price = serializers.SerializerMethodField()
    class Meta:
        model = Cart
        fields = ['id', 'user', 'cart_code', 'created_at', 'updated_at', 'cart_items', 'total_price']

    def get_total_price(self, cart):
        total = sum(item.quantity * item.record.effective_price for item in cart.cart_items.all())
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

class OrderItemSerializer(serializers.ModelSerializer):
    record = RecordListSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'record', 'quantity', 'price']

class OrderSerializer(serializers.ModelSerializer):
    order_items = OrderItemSerializer(many=True, read_only=True)
    pickup_bazar = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id',
            'stripe_checkout_session_id',
            'amount',
            'currency',
            'user_email',
            'shipped_to',
            'shipping_details',
            'shipping_cost',
            'shipping_courier',
            'shipping_service',
            'shipping_link',
            'pickup_bazar',
            'status',
            'created_at',
            'updated_at',
            'order_items',
        ]

    def get_pickup_bazar(self, obj):
        bazar = obj.pickup_bazar
        if bazar is None:
            return None
        return {
            'id': bazar.id,
            'name': bazar.name,
            'date': bazar.date.isoformat(),
            'schedule': bazar.schedule,
            'address': bazar.address,
            'google_maps_url': bazar.google_maps_url,
        }

class AdminUserSerializer(serializers.ModelSerializer):
    """Read-only serializer for admin user list."""
    class Meta:
        model = get_user_model()
        fields = ['id', 'username', 'email', 'role', 'is_active', 'email_verified', 'date_joined']


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    """Writable serializer for admin user updates (PATCH)."""
    class Meta:
        model = get_user_model()
        fields = ['username', 'email', 'role', 'is_active', 'email_verified']

    def validate_role(self, value):
        valid_roles = [r[0] for r in get_user_model().ROLES]
        if value not in valid_roles:
            raise serializers.ValidationError(f"Rol inválido. Opciones: {', '.join(valid_roles)}")
        return value


class RecordUpdateSerializer(serializers.ModelSerializer):
    """Writable serializer for admin record updates (PATCH).

    FKs are accepted as plain IDs (not nested objects).
    All fields optional since this is used for partial updates.
    sell_price is auto-calculated from price + discount_porcentage in Record.save().
    """
    class Meta:
        model = Record
        fields = [
            'title', 'artist', 'description', 'condition', 'genere',
            'cover_image_url', 'price', 'cost_price', 'sell_price',
            'final_sale_price', 'discount_porcentage', 'stock', 'images',
            'release_date', 'featured', 'items_inside', 'weight_grams', 'category',
        ]
        extra_kwargs = {
            field: {'required': False}
            for field in fields
        } | {
            'sell_price': {'read_only': True},
        }

    def to_internal_value(self, data):
        # Normalise comma-decimal strings before DRF field validation
        for field in ('price', 'cost_price', 'final_sale_price'):
            if field in data:
                data[field] = _normalize_decimal_string(data[field])
        return super().to_internal_value(data)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

class VerifyEmailSerializer(serializers.Serializer):
    uid = serializers.CharField(required=True)
    token = serializers.CharField(required=True)

    def validate(self, attrs):
        user_model = get_user_model()
        try:
            uid = urlsafe_base64_decode(force_str(attrs.get('uid')))
            user = user_model.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, user_model.DoesNotExist):
            raise serializers.ValidationError({"token": "Invalid or expired verification link"})

        if not user.is_active:
            raise serializers.ValidationError({"token": "Invalid or expired verification link"})

        if not default_token_generator.check_token(user, attrs.get('token')):
            raise serializers.ValidationError({"token": "Invalid or expired verification link"})

        self.context['user'] = user
        return attrs

class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(required=True)
    token = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, trim_whitespace=False)
    confirm_password = serializers.CharField(required=True, trim_whitespace=False)

    def validate(self, attrs):
        new_password = attrs.get('new_password')
        confirm_password = attrs.get('confirm_password')

        if new_password != confirm_password:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match"})

        user_model = get_user_model()
        try:
            uid = urlsafe_base64_decode(force_str(attrs.get('uid')))
            user = user_model.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, user_model.DoesNotExist):
            raise serializers.ValidationError({"token": "Invalid or expired reset token"})

        if not user.is_active:
            raise serializers.ValidationError({"token": "Invalid or expired reset token"})

        if not default_token_generator.check_token(user, attrs.get('token')):
            raise serializers.ValidationError({"token": "Invalid or expired reset token"})

        validate_password(new_password, user)

        self.context['user'] = user
        return attrs


class BazarSerializer(serializers.ModelSerializer):
    """Serializer for Bazar CRUD (admin write, public read).

    `image` accepts an uploaded file on create/update; `image_url` is the
    absolute URL the frontend should use to display it.
    """
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Bazar
        fields = [
            'id', 'name', 'slug', 'image', 'image_url', 'date',
            'schedule', 'address', 'google_maps_url', 'created_at',
        ]
        read_only_fields = ['slug']

    def get_image_url(self, obj):
        if not obj.image:
            return None
        try:
            url = obj.image.url
        except ValueError:
            return None
        # S3 backends already return an absolute https:// URL -- only
        # build_absolute_uri for relative paths (local FileSystemStorage).
        if url.startswith(('http://', 'https://')):
            return url
        request = self.context.get('request')
        return request.build_absolute_uri(url) if request else url

    def validate_date(self, value):
        # Past dates are allowed: admins can backfill bazares that already
        # happened and manage them from the admin list.
        return value
