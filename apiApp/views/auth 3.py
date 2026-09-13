"""Authentication & user-management views (public + admin)."""
import logging

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..emails import send_password_recovery_email, send_verification_email, send_welcome_email
from ..throttling import LiveScopedRateThrottle
from ..serilizers import (
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserRegistrationSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from .common import (
    _build_token_response,
    _build_verification_link,
    error_response,
    _require_admin,
)

logger = logging.getLogger(__name__)
User = get_user_model()


@api_view(['POST'])
def register_user(request):
    serializer = UserRegistrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = serializer.save()

    try:
        send_welcome_email(user)
    except Exception as exc:
        logger.warning("Welcome email failed for new user %s: %s", user.id, exc)

    try:
        send_verification_email(user, _build_verification_link(user))
    except Exception as exc:
        logger.warning("Verification email failed for new user %s: %s", user.id, exc)

    tokens = _build_token_response(user)
    return Response(
        {
            "message": f"User {user.username} registered successfully",
            "tokens": tokens,
            "email_verified": user.email_verified,
            "role": user.role,
        },
        status=201,
    )


@api_view(['POST'])
def login_user(request):
    """
    Authenticate user by username or email and return JWT token pair.
    """
    username = request.data.get("username")
    email = request.data.get("email")
    password = request.data.get("password")

    if not password or not (username or email):
        return Response(
            {"error": "Provide password and either username or email"},
            status=400,
        )

    # Allow login with email for convenience.
    if email and not username:
        try:
            user_obj = User.objects.get(email=email)
            username = user_obj.username
        except User.DoesNotExist:
            return error_response("Credenciales inválidas", status_code=401, code="invalid_credentials")

    user = authenticate(username=username, password=password)
    if not user:
        return error_response("Credenciales inválidas", status_code=401, code="invalid_credentials")
    if not user.is_active:
        return error_response("User is inactive", status_code=403, code="user_inactive")
    if settings.REQUIRE_EMAIL_VERIFICATION and not user.email_verified:
        return error_response(
            "Please verify your email before signing in",
            status_code=403,
            code="email_not_verified",
        )

    tokens = _build_token_response(user)
    return Response(
        {
            "message": "Login successful",
            "tokens": tokens,
            "email_verified": user.email_verified,
            "role": user.role,
        },
        status=200,
    )


@api_view(['POST'])
@throttle_classes([LiveScopedRateThrottle])
def request_password_reset(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data['email']
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        user = None

    if user is not None:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_link = f"{settings.FRONTEND_URL.rstrip('/')}/restablecer-contrasena?uid={uid}&token={token}"
        try:
            send_password_recovery_email(user, reset_link, expiry_hours=24)
        except Exception as exc:
            logger.warning("Password recovery email failed for user %s: %s", user.id, exc)

    return Response({"message": "If that email is registered, a reset link has been sent"}, status=200)


request_password_reset.view_class.throttle_scope = 'password_reset_request'


@api_view(['POST'])
@throttle_classes([LiveScopedRateThrottle])
def confirm_password_reset(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = serializer.context['user']
    user.set_password(serializer.validated_data['new_password'])
    user.save(update_fields=['password'])
    logger.info("Password reset for user %s", user.id)
    return Response({"message": "Password reset successfully"}, status=200)


confirm_password_reset.view_class.throttle_scope = 'password_reset_confirm'


@api_view(['POST'])
def verify_email(request):
    serializer = VerifyEmailSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = serializer.context['user']
    if user.email_verified:
        return Response({"message": "Email already verified", "email_verified": True}, status=200)

    user.email_verified = True
    user.save(update_fields=['email_verified'])
    logger.info("Email verified for user %s", user.id)
    return Response({"message": "Email verified successfully", "email_verified": True}, status=200)


@api_view(['POST'])
@throttle_classes([LiveScopedRateThrottle])
def resend_verification_email(request):
    email = request.data.get('email')
    if not email:
        return error_response("email is required", status_code=400, code="email_required")

    # Case-insensitive match: users often type the same email with different
    # casing than the one stored at registration.
    user = User.objects.filter(email__iexact=email).first()

    if user is not None and not user.email_verified:
        try:
            send_verification_email(user, _build_verification_link(user))
        except Exception as exc:
            logger.warning("Verification email resend failed for user %s: %s", user.id, exc)

    return Response({"message": "If that email is registered, a verification link has been sent"}, status=200)


resend_verification_email.view_class.throttle_scope = 'email_verify'


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_me(request):
    """Return the authenticated user's profile, including email_verified.

    The frontend uses this to re-sync the authoritative verification status
    on app load (e.g. after verifying in another tab or upgrading from a
    session stored before the email-verification feature existed).
    """
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_details(_, username):
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return error_response("User not found", status_code=404, code="user_not_found")

    serializer = UserSerializer(user)
    return Response(serializer.data)


# ── Admin: user management ──────────────────────────────────────────────


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_list_users(request):
    """List all users. Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    users = User.objects.all().order_by('-date_joined')
    serializer = AdminUserSerializer(users, many=True)
    return Response(serializer.data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def admin_update_user(request, user_id):
    """Update a user (role, username, email, is_active, email_verified). Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    try:
        target_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return error_response("Usuario no encontrado", status_code=404, code="user_not_found")

    serializer = AdminUserUpdateSerializer(target_user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    # Prevent admin from removing their own admin role
    if target_user.id == request.user.id and 'role' in serializer.validated_data:
        if serializer.validated_data['role'] != 'ADMIN':
            return error_response(
                "No puedes cambiar tu propio rol de administrador.",
                status_code=400,
                code="self_role_change_forbidden",
            )

    serializer.save()
    return Response(AdminUserSerializer(target_user).data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def admin_delete_user(request, user_id):
    """Delete a user. Admin only. Cannot delete yourself."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    try:
        target_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return error_response("Usuario no encontrado", status_code=404, code="user_not_found")

    if target_user.id == request.user.id:
        return error_response(
            "No puedes eliminar tu propia cuenta desde aquí.",
            status_code=400,
            code="self_delete_forbidden",
        )

    target_user.delete()
    return Response({"message": "Usuario eliminado correctamente"}, status=200)
