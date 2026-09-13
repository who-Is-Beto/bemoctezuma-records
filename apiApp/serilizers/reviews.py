"""Review serializers."""
from rest_framework import serializers

from ..models import Review
from .user import UserSerializer


class ReviewSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = Review
        fields = ['id', 'user', 'rating', 'review', 'created_at', 'updated_at']
