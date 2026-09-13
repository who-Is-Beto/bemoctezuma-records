"""User-related serializers (public profile + auth + admin user mgmt).

Note: the original single-file ``serilizers.py`` declared ``UserSerializer``
twice (once without ``role``, once with). The second definition always won in
the module namespace, so the first was dead code. It is removed here — there
is exactly one ``UserSerializer`` (with ``role``), which is what all views and
the nested ``ReviewSerializer`` actually used.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers


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
        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'email_verified', 'role']


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
