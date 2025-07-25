from rest_framework import serializers
from .models import Record, Category, User

class RecordDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Record
        fields = '__all__'
        # fields = ['id', 'title', 'artist', 'description', 'genre', 'cover_image_url', 'price', 'discount_porcentage', 'stock', 'slug', 'release_date', 'category']
        # extra_kwargs = {
        #     'slug': {'required': False},
        #     'category': {'required': False},  
        #     'user': {'required': False},

class RecordListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Record
        fields = ['id', 'title', 'artist', 'genre', 'cover_image_url', 'price', 'discount_porcentage', 'stock', 'slug', 'category']
        # extra_kwargs = {
        #     'slug': {'required': False},
        #     'category': {'required': False},  
        #     'user': {'required': False},

class CategorySerializer(serializers.ModelSerializer):
    products = RecordListSerializer(many=True, read_only=True)
    class Meta:
        model = Category
        fields = '__all__'

class CategoryListSerializer(serializers.ModelSerializer):
    products = RecordListSerializer(many=True, read_only=True)
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

