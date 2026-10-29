"""Catalog serializers: artist, category, genere, owner and record (read/write)."""
from django.db import transaction
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from ..models import Artist, Category, Genere, Owner, Record, RecordOwner
from ..services.copies import copies_by_group, group_id


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


def _unique_name(model, label):
    # Same name ignoring case would be two "Rock" chips in the catalog filter.
    return UniqueValidator(
        queryset=model.objects.all(), lookup='iexact', message=f"Ya existe un {label} con ese nombre.",
    )


class CategorySerializer(serializers.ModelSerializer):
    """A format (LP, 7", CD...). Also the admin write serializer: the slug is
    set once from the first name and kept on rename, because catalog URLs and
    shipping weights (slug 'cd' / '7') depend on it."""
    name = serializers.CharField(max_length=100, validators=[_unique_name(Category, 'formato')])

    class Meta:
        model = Category
        fields = '__all__'
        read_only_fields = ['slug']

    def validate_name(self, value):
        return value.strip()


class CategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]


class GenereSerializer(serializers.ModelSerializer):
    """Read and admin write; like formats, the slug is kept on rename."""
    name = serializers.CharField(max_length=100, validators=[_unique_name(Genere, 'género')])

    class Meta:
        model = Genere
        fields = ["id", "name", "slug", "description"]
        read_only_fields = ['slug']

    def validate_name(self, value):
        return value.strip()


class CategoryCountSerializer(CategoryListSerializer):
    """/categories/ rows: how many records use each format."""
    records_count = serializers.IntegerField(read_only=True)

    class Meta(CategoryListSerializer.Meta):
        fields = CategoryListSerializer.Meta.fields + ['records_count']


class GenereCountSerializer(GenereSerializer):
    """/generes/ rows: how many records are in each genre."""
    records_count = serializers.IntegerField(read_only=True)

    class Meta(GenereSerializer.Meta):
        fields = GenereSerializer.Meta.fields + ['records_count']


class OwnerSerializer(serializers.ModelSerializer):
    # Unique regardless of case; stored lowercased (validate_email) so the
    # same person can't end up as two owners with split sales.
    email = serializers.EmailField(max_length=254, validators=[UniqueValidator(
        queryset=Owner.objects.all(), lookup='iexact', message="Ya existe un dueño con ese correo.",
    )])

    class Meta:
        model = Owner
        fields = ['id', 'name', 'email']

    def validate_email(self, value):
        return value.lower()


class RecordOwnerSerializer(serializers.ModelSerializer):
    """How many of the record's stock belong to one owner."""
    owner = serializers.PrimaryKeyRelatedField(queryset=Owner.objects.all())
    owner_name = serializers.CharField(source='owner.name', read_only=True)
    # Optional with a single owner: they get the whole stock.
    quantity = serializers.IntegerField(min_value=0, required=False)

    class Meta:
        model = RecordOwner
        fields = ['owner', 'owner_name', 'quantity']


class RecordOwnersMixin(serializers.Serializer):
    """Writable ``owners: [{owner, quantity}]`` for the record form.

    No owners = store stock. One owner gets the whole stock. Several owners
    must add up to the stock. Omitting ``owners`` keeps them as they are; a
    stock change then follows a single owner, and is rejected for several
    (the form has to say whose units changed).
    """
    owners = RecordOwnerSerializer(many=True, required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        stock = attrs.get('stock', self.instance.stock if self.instance else 0)
        owners = attrs.get('owners')
        if owners is None:
            if self.instance is None or 'stock' not in attrs:
                return attrs
            current = list(self.instance.owner_stock.select_related('owner'))
            if len(current) == 1:
                attrs['owners'] = [{'owner': current[0].owner, 'quantity': stock}]
            elif current and sum(row.quantity for row in current) != stock:
                raise serializers.ValidationError(
                    {'owners': 'El disco tiene varios dueños: indica cuántos son de cada uno.'}
                )
            return attrs

        owner_ids = [line['owner'].pk for line in owners]
        if len(set(owner_ids)) != len(owner_ids):
            raise serializers.ValidationError({'owners': 'Un dueño aparece más de una vez.'})
        if len(owners) == 1:
            owners[0]['quantity'] = stock
        elif owners:
            total = sum(line.get('quantity', 0) for line in owners)
            if total != stock:
                raise serializers.ValidationError(
                    {'owners': f'Las cantidades por dueño suman {total}, pero el stock es {stock}.'}
                )
        return attrs

    def _save_owners(self, record, owners):
        if owners is None:
            return
        record.owner_stock.exclude(owner__in=[line['owner'] for line in owners]).delete()
        for line in owners:
            # update_or_create keeps created_at, i.e. the owner's place in the FIFO.
            RecordOwner.objects.update_or_create(
                record=record, owner=line['owner'], defaults={'quantity': line.get('quantity', 0)},
            )

    def create(self, validated_data):
        owners = validated_data.pop('owners', None)
        with transaction.atomic():
            record = super().create(validated_data)
            self._save_owners(record, owners)
        return record

    def update(self, instance, validated_data):
        owners = validated_data.pop('owners', None)
        with transaction.atomic():
            record = super().update(instance, validated_data)
            self._save_owners(record, owners)
        return record


class RecordCopyOfMixin(serializers.Serializer):
    """Writable ``variant_of``: the record this one is another condition of."""

    def validate_variant_of(self, value):
        """Copies always hang from the group's first record (one level deep)."""
        if value is None:
            return None
        first = value.variant_of or value
        if self.instance is not None:
            if first.pk == self.instance.pk:
                raise serializers.ValidationError("Un disco no puede ser otra condición de sí mismo.")
            if self.instance.variants.exists():
                raise serializers.ValidationError(
                    "Este disco ya tiene otras condiciones: no puede ser copia de otro."
                )
        return first


# Business-internal (margin, last sale price): only admin responses carry
# them (RecordAdminSerializer, plus the owners), never the public catalog.
PRIVATE_RECORD_FIELDS = ['cost_price', 'final_sale_price']


class CoverThumbsField(serializers.Field):
    """{"320": url, "640": url}: the cover's WebP thumbnails, or {} while they
    don't exist or were made from a previous cover."""

    def __init__(self, **kwargs):
        super().__init__(source='*', read_only=True, **kwargs)

    def to_representation(self, record):
        thumbs = record.cover_thumbs or {}
        if not record.cover_image_url or thumbs.get('src') != record.cover_image_url:
            return {}
        return {width: url for width, url in thumbs.items() if width != 'src'}


class RecordCopySerializer(serializers.ModelSerializer):
    """One copy of a release in the condition picker (catalog card, detail page)."""
    sell_price = serializers.SerializerMethodField()
    cover_thumbs = CoverThumbsField()

    class Meta:
        model = Record
        fields = [
            'id', 'slug', 'condition', 'price', 'sell_price', 'discount_porcentage',
            'stock', 'cover_image_url', 'cover_thumbs', 'images',
        ]

    def get_sell_price(self, obj):
        return str(obj.effective_price)


def _copies(copies):
    """Every copy of the group, or [] when the record is the only one."""
    return RecordCopySerializer(copies, many=True).data if len(copies) > 1 else []


class RecordDetailSerializer(serializers.ModelSerializer):
    artist = ArtistSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    generes = GenereSerializer(many=True, read_only=True)
    sell_price = serializers.SerializerMethodField()
    cover_thumbs = CoverThumbsField()
    # This record and its copies in other conditions, best condition first.
    copies = serializers.SerializerMethodField()

    class Meta:
        model = Record
        exclude = PRIVATE_RECORD_FIELDS

    def get_sell_price(self, obj):
        """Always return the computed price so stale DB values never leak."""
        return str(obj.effective_price)

    def get_copies(self, obj):
        return _copies(copies_by_group([obj])[group_id(obj)])


class RecordAdminSerializer(RecordDetailSerializer):
    """Every field, private ones included: admin responses and the edit form."""
    owners = RecordOwnerSerializer(source='owner_stock', many=True, read_only=True)

    class Meta(RecordDetailSerializer.Meta):
        exclude = None
        fields = '__all__'


class RecordListSerializer(serializers.ModelSerializer):
    artist = ArtistSerializer(read_only=True)
    category = CategoryListSerializer(read_only=True)
    generes = GenereSerializer(many=True, read_only=True)
    sell_price = serializers.SerializerMethodField()
    cover_thumbs = CoverThumbsField()
    # Copies in other conditions, from context['copies'] (copies_by_group of
    # the page: one query). Nested in cart/wishlist/orders it's always [].
    copies = serializers.SerializerMethodField()

    class Meta:
        model = Record
        fields = ['id', 'title', 'condition', 'category', 'artist', 'generes', 'cover_image_url', 'cover_thumbs', 'price', 'sell_price', 'discount_porcentage', 'stock', 'slug', 'images', 'variant_of', 'copies']

    def get_sell_price(self, obj):
        """Always return the computed price so stale DB values never leak."""
        return str(obj.effective_price)

    def get_copies(self, obj):
        return _copies(self.context.get('copies', {}).get(group_id(obj), []))


class RecordCreateSerializer(RecordCopyOfMixin, RecordOwnersMixin, serializers.ModelSerializer):
    """Write-only serializer for creating a record via the admin inventory form.

    FKs are accepted as plain IDs (not nested objects).
    sell_price is auto-calculated from price + discount_porcentage in Record.save().
    """

    class Meta:
        model = Record
        fields = [
            'title', 'artist', 'description', 'condition', 'generes',
            'cover_image_url', 'price', 'cost_price', 'sell_price',
            'discount_porcentage', 'stock', 'images', 'release_date',
            'featured', 'items_inside', 'weight_grams', 'category', 'owners', 'variant_of',
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


class RecordUpdateSerializer(RecordCopyOfMixin, RecordOwnersMixin, serializers.ModelSerializer):
    """Writable serializer for admin record updates (PATCH).

    FKs are accepted as plain IDs (not nested objects).
    All fields optional since this is used for partial updates.
    sell_price is auto-calculated from price + discount_porcentage in Record.save().
    """
    class Meta:
        model = Record
        fields = [
            'title', 'artist', 'description', 'condition', 'generes',
            'cover_image_url', 'price', 'cost_price', 'sell_price',
            'final_sale_price', 'discount_porcentage', 'stock', 'images',
            'release_date', 'featured', 'items_inside', 'weight_grams', 'category',
            'owners', 'variant_of',
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
