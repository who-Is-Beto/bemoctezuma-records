"""Catalog serializers: artist, category, genere and record (read/write)."""
from rest_framework import serializers

from ..models import Artist, Category, Genere, Record


def _normalize_decimal_string(value):
    """Accept price strings with comma decimals ("500,00") or dots ("500.00").

    Front-end forms in es-MX locale commonly submit "1234,56" which
    Python's Decimal() rejects.  This normalises to "1234.56" before
    DRF's DecimalField runs its own validation.
    """
    if isinstance(value, str):
        value = value.strip().replace(',', '.')
    return value


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
