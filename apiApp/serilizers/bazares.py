"""Bazar serializer."""
from rest_framework import serializers

from ..models import Bazar


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
