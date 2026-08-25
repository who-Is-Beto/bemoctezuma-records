import json
import logging
import operator
import re
from decimal import Decimal
from functools import reduce

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Replace
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.encoding import force_bytes
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Artist, Bazar, Cart, CartItem, Category, Genere, Order, OrderItem, Record, Review, Wishlist, WishlistItem
from .emails import send_password_recovery_email, send_verification_email, send_welcome_email
from .services import (
    CD_UNIT_WEIGHT_GRAMS,
    DEFAULT_UNIT_WEIGHT_GRAMS,
    PREFERRED_COURIER,
    SEVEN_INCH_UNIT_WEIGHT_GRAMS,
    ShippingQuoteError,
    build_package_from_cart,
    get_shipping_quotes,
    get_zip_locations,
    normalize_zip_code,
    select_cheapest_quote,
    send_order_created_email,
    send_order_shipped_email,
    send_order_notification_email,
)
from .pagination import StandardResultsSetPagination
from .serilizers import (
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    ArtistSerializer,
    BazarSerializer,
    GenereSerializer,
    CartItemSerializer,
    CartSerializer,
    CategoryListSerializer,
    CategorySerializer,
    OrderSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RecordCreateSerializer,
    RecordDetailSerializer,
    RecordListSerializer,
    RecordUpdateSerializer,
    ReviewSerializer,
    UserRegistrationSerializer,
    UserSerializer,
    VerifyEmailSerializer,
    WishlistSerializer,
)
import requests
import stripe

User = get_user_model()
stripe.api_key = settings.STRIPE_SECRET_KEY
endpoint_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None) or getattr(settings, "WEBHOOK_SECRET", None)
logger = logging.getLogger(__name__)

def _build_token_response(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}

def _build_verification_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.FRONTEND_URL.rstrip('/')}/verificar-correo?uid={uid}&token={token}"

def error_response(message, status_code=400, code="error", details=None):
    payload = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return Response(payload, status=status_code)

def _require_email_verified(request):
    """Block authenticated-but-unverified users from purchasing flows.

    Only fires when REQUIRE_EMAIL_VERIFICATION is enabled. Returns an error
    Response when the user must verify their email first, otherwise None.
    """
    if (
        settings.REQUIRE_EMAIL_VERIFICATION
        and request.user.is_authenticated
        and not request.user.email_verified
    ):
        return error_response(
            "Verifica tu correo para continuar. Revisa tu bandeja de entrada o pide un nuevo enlace desde tu perfil.",
            status_code=403,
            code="email_not_verified",
        )
    return None

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
@throttle_classes([ScopedRateThrottle])
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
@throttle_classes([ScopedRateThrottle])
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
@throttle_classes([ScopedRateThrottle])
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


def _require_admin(request):
    """Return an error Response if the user is not an admin, else None."""
    if request.user.role != "ADMIN":
        return error_response(
            "No tienes permiso para realizar esta acción.",
            status_code=403,
            code="forbidden",
        )
    return None


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


# ── Admin: record management ────────────────────────────────────────────


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def admin_update_record(request, record_id):
    """Update a record (stock, final_sale_price, all fields). Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    try:
        record = Record.objects.get(pk=record_id)
    except Record.DoesNotExist:
        return error_response("Disco no encontrado", status_code=404, code="record_not_found")

    serializer = RecordUpdateSerializer(record, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()

    # Return full detail so the frontend gets nested artist/category/genere
    detail = RecordDetailSerializer(record)
    return Response(detail.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def admin_delete_record(request, record_id):
    """
    Permanently delete a record. Admin only.

    Order history is preserved: OrderItem.record is SET_NULL, so past orders
    keep their quantity and snapshotted price. Cart items, wishlist entries,
    reviews and the rating summary are removed with the record (CASCADE).
    The cover image file on disk is intentionally left in place.
    """
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    try:
        record = Record.objects.get(pk=record_id)
    except Record.DoesNotExist:
        return error_response("Disco no encontrado", status_code=404, code="record_not_found")

    title = record.title
    record.delete()
    return Response(
        {"message": f"Disco '{title}' eliminado permanentemente"},
        status=200,
    )


@api_view(['GET'])
def record_list(request):
    records = Record.objects.filter(featured=True).order_by('-id')

    # ?category=lp,7,cd,... -> filter by category slug
    category = request.query_params.get('category')
    if category:
        records = records.filter(category__slug=category)

    # ?available=true -> only records with at least 1 item in stock
    available = request.query_params.get('available')
    if available is not None and available.lower() in ('true', '1', 'yes'):
        records = records.filter(stock__gt=0)

    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(records, request)
    serializer = RecordListSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def record_create(request):
    """Create a new record. Admin only."""
    if request.user.role != "ADMIN":
        return error_response("No tienes permiso para realizar esta acción.", status_code=403, code="forbidden")
    serializer = RecordCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    record = serializer.save()
    # Return the full detail so the frontend gets nested artist/category/genere
    detail = RecordDetailSerializer(record)
    return Response(detail.data, status=201)

@api_view(['GET'])
def artist_list(request):
    artists = Artist.objects.all().order_by('name')
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(artists, request)
    serializer = ArtistSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)
@api_view(['GET'])
def artist_search(request):
    """Search artists by name. Used for autocomplete.

    Accent/punctuation-insensitive: 'zoe' finds 'Zoé', 'trex' finds 'T. Rex'.
    """
    q = request.query_params.get('q', '').strip()
    if not q:
        return Response([])
    tokens = _query_tokens(q)
    if tokens:
        # Every word must match the artist's normalized slug, in any order.
        # Q objects (not queryset &) avoid duplicate-annotation collisions.
        artists = Artist.objects.filter(
            reduce(
                operator.and_,
                (Q(id__in=_slug_contains(Artist, t)) for t in tokens),
            )
        ).order_by('name')[:20]
    else:
        artists = Artist.objects.filter(name__icontains=q).order_by('name')[:20]
    serializer = ArtistSerializer(artists, many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def artist_create(request):
    """Create a new artist. Returns existing if name matches exactly."""
    name = request.data.get('name', '').strip()
    if not name:
        return error_response("name is required", status_code=400, code="name_required")
    
    # Case-insensitive exact match
    existing = Artist.objects.filter(name__iexact=name).first()
    if existing:
        serializer = ArtistSerializer(existing)
        return Response(serializer.data, status=200)
    
    artist = Artist.objects.create(name=name)
    serializer = ArtistSerializer(artist)
    return Response(serializer.data, status=201)


@api_view(['GET'])
def genere_list(request):
    generes = Genere.objects.all().order_by('name')
    serializer = GenereSerializer(generes, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def record_detail(_, slug):
    try:
        record = Record.objects.get(slug=slug)
    except Record.DoesNotExist:  
        return error_response("Product not found", status_code=404, code="product_not_found")
    
    serializer = RecordDetailSerializer(record)
    return Response(serializer.data)

@api_view(['GET'])
def get_category_list(_):
    categories = Category.objects.all()
    serializer = CategoryListSerializer(categories, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def get_category_detail(_, slug):
    try:
        category = Category.objects.get(slug=slug)
    except Category.DoesNotExist:
        return error_response("Category not found", status_code=404, code="category_not_found")
    
    serializer = CategorySerializer(category)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_list_orders(request):
    """List every order (newest first). Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    orders = Order.objects.all().order_by('-created_at').prefetch_related('order_items__record')
    serializer = OrderSerializer(orders, many=True)
    return Response(serializer.data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def admin_update_order(request, order_id):
    """Update an order's status and/or shipping_link. Admin only.

    status must be one of Order.status_choices; shipping_link is a tracking
    URL/code (max 255 chars, empty string clears it).
    """
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    order = get_object_or_404(Order, pk=order_id)

    previous_status = order.status
    valid_statuses = dict(Order.status_choices)
    if 'status' in request.data:
        new_status = request.data.get('status')
        if new_status not in valid_statuses:
            return error_response(
                f"status debe ser uno de: {', '.join(valid_statuses)}.",
                status_code=400,
                code="invalid_status",
            )
        order.status = new_status

    if 'shipping_link' in request.data:
        new_link = str(request.data.get('shipping_link') or '').strip()
        if len(new_link) > 255:
            return error_response(
                "shipping_link no puede exceder 255 caracteres.",
                status_code=400,
                code="invalid_shipping_link",
            )
        order.shipping_link = new_link

    order.save()

    # First time an order moves into 'shipped', tell the customer their
    # package is on the way (includes the tracking link saved in this same
    # request, if any). Link-only edits on already-shipped orders don't re-send.
    if order.status == 'shipped' and previous_status != 'shipped':
        send_order_shipped_email(order)

    serializer = OrderSerializer(order)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_cart(request, cart_code):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart = Cart.objects.filter(cart_code=cart_code).first()
    # Someone else's cart is invisible; anonymous (user=None) legacy carts
    # stay reachable until claimed via add_to_cart. A foreign cart is treated
    # exactly like a missing one — never 404 the client into a retry loop.
    if cart is not None and cart.user_id not in (None, request.user.id):
        cart = None
    if cart is None:
        # Stale/foreign/unknown cart_code (e.g. localStorage kept a code from
        # another account on this browser): hand back the user's most recent
        # own cart, or a brand-new empty one owned by them.
        cart = (
            Cart.objects.filter(user=request.user)
            .order_by('-updated_at')
            .first()
        ) or Cart.objects.create(user=request.user)

    serializer = CartSerializer(cart)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_carts(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    # Only the requester's carts. This used to be Cart.objects.all(), which
    # handed every client the first cart in the DB — all users shared a cart.
    carts = Cart.objects.filter(user=request.user).order_by('-updated_at')
    serializer = CartSerializer(carts, many=True)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_cart_items(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_items = CartItem.objects.all()
    serializer = CartItemSerializer(cart_items, many=True)
    return Response(serializer.data)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_to_cart(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_code = request.data.get('cart_code')
    try:
        record_id = int(request.data.get('record_id'))
    except (TypeError, ValueError):
        return error_response("record_id is required", status_code=400, code="record_id_required")
    try:
        quantity = int(request.data.get('quantity', 1))
    except (TypeError, ValueError):
        return error_response("quantity must be a number", status_code=400, code="quantity_invalid")
    if quantity < 1:
        return error_response("quantity must be at least 1", status_code=400, code="quantity_invalid")

    # Ownership is derived from the authenticated user, never from the
    # request body. Carts created before this fix have user=None and get
    # claimed by whoever legitimately uses their code first.
    cart = None
    if cart_code:
        candidate = Cart.objects.filter(cart_code=cart_code).first()
        if candidate is not None and candidate.user_id in (None, request.user.id):
            cart = candidate
            if cart.user_id is None:
                cart.user = request.user
                cart.save(update_fields=['user', 'updated_at'])
    if cart is None:
        # Stale/foreign/absent cart_code: self-heal by adding into the user's
        # most recent own cart (or a new one) instead of failing the request.
        cart = (
            Cart.objects.filter(user=request.user)
            .order_by('-updated_at')
            .first()
        ) or Cart.objects.create(user=request.user)
    try:
        record = Record.objects.get(id=str(record_id))
    except Record.DoesNotExist:
        return error_response("Record not found", status_code=404, code="product_not_found")
    existing = CartItem.objects.filter(cart=cart, record=record).first()
    current_quantity = existing.quantity if existing else 0
    new_quantity = current_quantity + quantity
    if new_quantity > record.stock:
        # Validate stock BEFORE creating/updating the CartItem so an
        # over-stock request never leaves a phantom item in the cart.
        return error_response(
            "No hay suficiente stock disponible. Intenta con una cantidad menor.",
            status_code=400,
            code="stock_insuficiente",
        )
    if existing:
        existing.quantity = new_quantity
        existing.save()
    else:
        CartItem.objects.create(cart=cart, record=record, quantity=new_quantity)

    serializer = CartSerializer(cart)
    return Response(serializer.data)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_cart_quantity(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_item_id = request.data.get('item_id')
    try:
        quantity = int(request.data.get('quantity'))
    except (TypeError, ValueError):
        return error_response("quantity must be a number", status_code=400, code="quantity_invalid")
    if quantity < 1:
        return error_response("quantity must be at least 1", status_code=400, code="quantity_invalid")

    try:
        cartitem = CartItem.objects.get(id=cart_item_id)
    except CartItem.DoesNotExist:
        return error_response("Cart item not found", status_code=404, code="cart_item_not_found")
    if quantity > cartitem.record.stock:
        return error_response(
            "No hay suficiente stock disponible. Intenta con una cantidad menor.",
            status_code=400,
            code="stock_insuficiente",
        )
    cartitem.quantity = quantity
    print('cart_item', cartitem.cart)
    cartitem.save()

    serializer = CartSerializer(cartitem.cart)
    return Response({"data": serializer.data, "message": "Cart updated successfully"})

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_cart_item(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_code = request.data.get('cart_code')
    record_id = request.data.get('record_id')

    if not cart_code or not record_id:
        return error_response("cart_code and record_id are required", status_code=400, code="missing_params")

    try:
        cart = Cart.objects.get(cart_code=cart_code)
        cart_item = CartItem.objects.get(record_id=record_id, cart=cart)
        cart_item.delete()
        return Response({"message": "Cart item removed successfully"}, status=200)
    except Cart.DoesNotExist:
        return error_response("Cart not found", status_code=404, code="cart_not_found")
    except CartItem.DoesNotExist:
        return error_response("Cart item not found", status_code=404, code="cart_item_not_found")
    
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_all_cart_items(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_code = request.data.get('cart_code')
    try:
        cart = Cart.objects.get(cart_code=cart_code)
        cart.cart_items.all().delete()
        return Response({"message": "All cart items removed successfully"}, status=200)
    except Cart.DoesNotExist:
        return error_response("Cart not found", status_code=404, code="cart_not_found")

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_cart(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_code = request.data.get('cart_code')
    try:
        cart = Cart.objects.get(cart_code=cart_code)
        cart.delete()
        return Response({"message": "Cart deleted successfully"}, status=200)
    except Cart.DoesNotExist:
        return error_response("Cart not found", status_code=404, code="cart_not_found")

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_to_wishlist(request):
    email = request.data.get('email')
    wishlist_code = request.data.get('wishlist_code')
    record_id = request.data.get('record_id')
    user = User.objects.get(email=email) if email else None
    
    if not record_id:
        return error_response("record_id is required", status_code=400, code="record_id_required")

    if wishlist_code:
        wishlist, _ = Wishlist.objects.get_or_create(wishlist_code=wishlist_code, defaults={'user': user})
    else:
        wishlist = Wishlist.objects.create(user=user)
    record = Record.objects.get(id=str(record_id))
    _, created = WishlistItem.objects.get_or_create(wishlist=wishlist, record=record)
    if not created:
        return Response({"message": "Record already in wishlist"}, status=200)
    serializer = WishlistSerializer(wishlist)
    return Response({"message": "Record added to wishlist", "wishlist": serializer.data}, status=201)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_wishlists(_):
    wishlists = Wishlist.objects.all()
    serializer = WishlistSerializer(wishlists, many=True)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_wishlist(request, wishlist_code):
    email = request.query_params.get('email')
    user = User.objects.get(email=email) if email else None
    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    serializer = WishlistSerializer(wishlist)
    return Response(serializer.data)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_from_wishlist(request):
    wishlist_code = request.data.get('wishlist_code')
    record_id = request.data.get('record_id')

    if not wishlist_code or not record_id:
        return error_response(
            "wishlist_code and record_id are required",
            status_code=400,
            code="wishlist_params_required",
        )

    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    try:
        wishlist_item = WishlistItem.objects.get(wishlist=wishlist, record_id=record_id)
    except WishlistItem.DoesNotExist:
        return error_response("Wishlist item not found", status_code=404, code="wishlist_item_not_found")

    wishlist_item.delete()
    wishlist.refresh_from_db()
    serializer = WishlistSerializer(wishlist)
    return Response({"message": "Record removed from wishlist", "wishlist": serializer.data}, status=200)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_wishlist_count(request):
    wishlist_code = request.query_params.get('wishlist_code') or request.data.get('wishlist_code')
    if not wishlist_code:
        return error_response("wishlist_code is required", status_code=400, code="wishlist_code_required")

    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    wishlist_count = wishlist.wishlist_items.count()
    return Response({"wishlist_count": wishlist_count}, status=200)

@api_view(['POST'])
def add_review(request):
    record_id = request.data.get('record_id')
    email = request.data.get('email')
    rating = request.data.get('rating')
    review = request.data.get('review')

    if(not record_id or not email or not rating or not review):
        return error_response(
            "record_id, email, rating, and review are required",
            status_code=400,
            code="review_params_required",
        )
    
    if(int(rating) < 1 or int(rating) > 5):
        return error_response("rating must be between 1 and 5", status_code=400, code="rating_invalid")

    if Review.objects.filter(record_id=record_id, email=email).exists():
        return error_response("User has already reviewed this record", status_code=400, code="review_duplicate")

    record = Record.objects.get(id=str(record_id))
    user = User.objects.get(email=email)

    new_review = Review.objects.create(
        record=record,
        user=user,
        rating=rating,
        review=review
    )
    serialized_review = ReviewSerializer(new_review)
    return Response({"message": "Review added successfully", "review": serialized_review.data}, status=201)

@api_view(['PUT'])
def update_review(request):
    review_id = request.data.get('review_id')
    rating = request.data.get('rating')
    review_text = request.data.get('review')

    if not review_id:
        return error_response("review_id is required", status_code=400, code="review_id_required")

    try:
        review = Review.objects.get(id=review_id)
    except Review.DoesNotExist:
        return error_response("Review not found", status_code=404, code="review_not_found")

    if rating:
        if int(rating) < 1 or int(rating) > 5:
            return error_response("rating must be between 1 and 5", status_code=400, code="rating_invalid")
        review.rating = rating

    if review_text:
        review.review = review_text

    review.save()
    serialized_review = ReviewSerializer(review)
    return Response({"message": "Review updated successfully", "review": serialized_review.data}, status=200)

@api_view(['DELETE'])
def delete_review(request):
    review_id = request.data.get('review_id')

    if not review_id:
        return error_response("review_id is required", status_code=400, code="review_id_required")

    try:
        review = Review.objects.get(id=review_id)
    except Review.DoesNotExist:
        return error_response("Review not found", status_code=404, code="review_not_found")

    review.delete()
    return Response({"message": "Review deleted successfully"}, status=200)

@api_view(['GET'])
def get_record_reviews(request, record_id):
    try:
        record = Record.objects.get(id=record_id)
    except Record.DoesNotExist:
        return error_response("Record not found", status_code=404, code="product_not_found")

    reviews = Review.objects.filter(record=record)
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(reviews, request)
    serializer = ReviewSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

@api_view(['GET'])
def get_all_reviews(request):
    reviews = Review.objects.all()
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(reviews, request)
    serializer = ReviewSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

def _normalized_search_term(term):
    """Normalize a search term the same way slugs are generated.

    'Zoé' -> 'zoe', 'T. Rex' -> 'trex' — lets 'zoe' match records by 'Zoé'
    and 'trex' match 'T. Rex' by comparing against hyphen-stripped slugs.
    """
    return slugify(term or '').replace('-', '')


def _slug_contains(model, term):
    """Queryset of `model` whose slug, with hyphens stripped, contains term."""
    return model.objects.annotate(
        _norm_slug=Replace('slug', Value('-'), Value('')),
    ).filter(_norm_slug__icontains=term)


def _query_tokens(query):
    """Split a raw query into normalized, comparable tokens.

    'Pink  Floyd!' -> ['pink', 'floyd']; 'Zoé' -> ['zoe'].
    """
    tokens = (_normalized_search_term(word) for word in query.split())
    return [tok for tok in tokens if tok]


def _record_token_q(token):
    """Q matching one token against any searchable field's normalized slug."""
    return (
        Q(id__in=_slug_contains(Record, token))
        | Q(artist__in=_slug_contains(Artist, token))
        | Q(genere__in=_slug_contains(Genere, token))
        | Q(category__in=_slug_contains(Category, token))
    )


@api_view(['GET'])
def record_search(request):
    query = request.query_params.get('query')
    if not query:
        return error_response("query parameter is required", status_code=400, code="query_required")

    tokens = _query_tokens(query)
    if tokens:
        # Every word must match somewhere (title/artist/genre/category),
        # in any order — 'floyd dark side' finds Pink Floyd's Dark Side.
        combined_q = Q()
        for token in tokens:
            combined_q &= _record_token_q(token)
        records = Record.objects.filter(combined_q).order_by('-id')
    else:
        # Term was pure punctuation; fall back to the legacy substring match.
        records = Record.objects.filter(
            Q(title__icontains=query)
            | Q(artist__name__icontains=query)
            | Q(genere__name__icontains=query)
            | Q(category__name__icontains=query)
        ).order_by('-id')

    # ?category=lp,7,cd,... -> filter by category slug
    category = request.query_params.get('category')
    if category:
        records = records.filter(category__slug=category)

    # ?available=true -> only records with at least 1 item in stock
    available = request.query_params.get('available')
    if available is not None and available.lower() in ('true', '1', 'yes'):
        records = records.filter(stock__gt=0)

    if not records.exists():
        return Response({"message": "No records found matching the query"}, status=404)
    serializer = RecordListSerializer(records, many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def shipping_quote(request):
    """Quote home-delivery cost for a cart via Envíos Perros.

    Body: {"cart_code": "...", "zip": "15400"}
    Returns the cheapest Estafeta option (store policy) plus the full quote
    list so the frontend can display alternatives later if needed.
    """
    blocked = _require_email_verified(request)
    if blocked:
        return blocked

    cart_code = request.data.get('cart_code')
    zip_code = normalize_zip_code(request.data.get('zip'))
    if not zip_code:
        return error_response(
            "zip debe ser un código postal válido de 5 dígitos.",
            status_code=400,
            code="invalid_zip_code",
        )

    cart = Cart.objects.filter(cart_code=cart_code).prefetch_related('cart_items__record__category').first()
    if not cart or cart.cart_items.count() == 0:
        return error_response("Cart is empty or not found", status_code=400, code="cart_empty")

    package = build_package_from_cart(cart)
    try:
        quotes = get_shipping_quotes(zip_code, package)
    except ShippingQuoteError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)

    selected = select_cheapest_quote(quotes)
    if selected is None:
        return error_response(
            f"No hay opciones de envío con {PREFERRED_COURIER} para el código postal {zip_code}.",
            status_code=404,
            code="shipping_unavailable",
        )

    subtotal = sum(
        (item.record.sell_price * item.quantity for item in cart.cart_items.all()),
        Decimal('0'),
    )
    return Response({
        'zip_code': zip_code,
        'package': package,
        'subtotal': subtotal,
        'currency': selected.get('currency', 'MXN'),
        'selected': {
            'title': selected.get('title'),
            'total': selected.get('total'),
            'currency': selected.get('currency', 'MXN'),
            'courier': selected.get('courier'),
            'serviceType': selected.get('serviceType'),
            'deliveryCommitment': selected.get('deliveryCommitment'),
        },
        'quotes': quotes,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def shipping_locations(request):
    """List the Sepomex colonias for a ZIP code (Envíos Perros /locations).

    One ZIP can cover several colonias and label generation requires the exact
    Sepomex name, so the checkout address form offers these as a dropdown.
    Query: ?zip=06700 (4-5 digits, per the upstream autocomplete contract).
    """
    blocked = _require_email_verified(request)
    if blocked:
        return blocked

    raw = (request.query_params.get('zip') or '').strip()
    if not re.fullmatch(r'\d{4,5}', raw):
        return error_response(
            "zip debe tener 4 o 5 dígitos.",
            status_code=400,
            code="invalid_zip_code",
        )
    zip_code = normalize_zip_code(raw) if len(raw) == 5 else raw

    try:
        locations = get_zip_locations(zip_code)
    except ShippingQuoteError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)

    return Response({'zip': zip_code, 'locations': locations})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_stripe_checkout_session(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_code = request.data.get('cart_code')
    email = getattr(request.user, 'email', None)
    shipped_to = request.data.get('shipped_to')
    shipping_details = request.data.get('shippingDetails') or request.data.get('shipping_details')
    zip_code = None

    if not shipped_to:
        return error_response("shipped_to is required", status_code=400, code="missing_shipped_to")

    if shipped_to == "home":
        if not shipping_details:
            return error_response("shippingDetails is required when shipped_to is 'home'", status_code=400, code="missing_shipping_details")
        if isinstance(shipping_details, str):
            try:
                shipping_details = json.loads(shipping_details)
            except json.JSONDecodeError:
                return error_response("shippingDetails must be valid JSON", status_code=400, code="invalid_shipping_details")

        # minimal shape validation
        required_keys = ["fullName", "phone", "street", "number", "neighborhood", "city", "state", "zip"]
        missing = [k for k in required_keys if not shipping_details.get(k)]
        if missing:
            return error_response(f"shippingDetails missing: {', '.join(missing)}", status_code=400, code="missing_shipping_fields")

        zip_code = normalize_zip_code(shipping_details.get('zip'))
        if not zip_code:
            return error_response(
                "zip debe ser un código postal válido de 5 dígitos.",
                status_code=400,
                code="invalid_zip_code",
            )
        # Store the normalized (zero-padded, digits-only) ZIP so fulfillment
        # and the future label generation always see a clean value.
        shipping_details['zip'] = zip_code

    pickup_bazar = None
    if shipped_to == "bazar":
        bazar_id = request.data.get('bazar_id')
        if not bazar_id:
            return error_response(
                "bazar_id is required when shipped_to is 'bazar'",
                status_code=400,
                code="missing_bazar",
            )
        try:
            pickup_bazar = Bazar.objects.get(pk=bazar_id)
        except (Bazar.DoesNotExist, ValueError, TypeError):
            return error_response(
                "El bazar seleccionado no existe.",
                status_code=400,
                code="invalid_bazar",
            )
        if pickup_bazar.date < timezone.localdate():
            return error_response(
                "Ese bazar ya pasó. Elige uno próximo.",
                status_code=400,
                code="bazar_in_past",
            )

    if not email:
        return error_response("User email not found", status_code=400, code="user_email_missing")

    cart = Cart.objects.filter(cart_code=cart_code).prefetch_related('cart_items__record').first()

    if not cart or cart.cart_items.count() == 0:
        return error_response("Cart is empty or not found", status_code=400, code="cart_empty")

    # Home deliveries: re-quote server-side so the customer is charged the
    # real courier price — never an amount sent from the browser.
    shipping_line_item = None
    shipping_meta = {}
    if shipped_to == "home":
        package = build_package_from_cart(cart)
        try:
            quotes = get_shipping_quotes(zip_code, package)
        except ShippingQuoteError as exc:
            return error_response(exc.message, status_code=502, code=exc.code)
        selected_quote = select_cheapest_quote(quotes)
        if selected_quote is None:
            return error_response(
                f"No hay opciones de envío con {PREFERRED_COURIER} para el código postal {zip_code}.",
                status_code=400,
                code="shipping_unavailable",
            )
        shipping_cost = Decimal(str(selected_quote.get('total', '0'))).quantize(Decimal('0.01'))
        shipping_meta = {
            'shipping_cost': str(shipping_cost),
            'shipping_courier': str(selected_quote.get('courier') or ''),
            'shipping_service': str(selected_quote.get('serviceType') or ''),
        }
        if shipping_cost > 0:
            shipping_line_item = {
                'price_data': {
                    'currency': 'mxn',
                    'product_data': {'name': f"Envío ({selected_quote.get('title')})"},
                    'unit_amount': int(shipping_cost * 100),
                },
                'quantity': 1,
            }

    try:
        line_items = []
        for item in cart.cart_items.all():
            if item.quantity > item.record.stock:
                return error_response(
                    f'No hay suficiente stock de "{item.record.title}" (disponible: {item.record.stock})',
                    status_code=400,
                    code="insufficient_stock",
                )
            line_items.append({
                'price_data': {
                    'currency': 'mxn',
                    'product_data': {
                        'name': item.record.title,
                        'metadata': {
                            'record_id': str(item.record.id),
                        },
                    },
                    'unit_amount': int(item.record.sell_price * 100),
                },
                'quantity': item.quantity,
            })

        if shipping_line_item:
            line_items.append(shipping_line_item)

        metadata = {'cart_code': cart_code, 'shipped_to': shipped_to}
        if shipping_details:
            metadata['shipping_details'] = json.dumps(shipping_details)
        metadata.update(shipping_meta)
        if pickup_bazar is not None:
            # Only the id travels in metadata (Stripe metadata values are
            # strings); fulfillment re-resolves the Bazar from the DB.
            metadata['bazar_id'] = str(pickup_bazar.id)

        success_url = f"{settings.FRONTEND_URL.rstrip('/')}/mis-ordenes?session_id={{CHECKOUT_SESSION_ID}}"

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            locale='es',
            success_url=success_url,
            cancel_url=f"{settings.FRONTEND_URL.rstrip('/')}/carrito",
            customer_email=email,
            # Keep cart reference in two places to make webhook processing resilient.
            client_reference_id=cart_code,
            metadata=metadata,
        )
        print(
            f">>> checkout session created | id={checkout_session.get('id')} "
            f"cart_code={cart_code} client_reference_id={checkout_session.get('client_reference_id')}"
        )
        return Response(
            {
                "checkout_url": checkout_session.get("url"),
                "session_id": checkout_session.get("id"),
                # Backward compatibility for clients reading nested fields.
                "checkout_session": checkout_session,
            },
            status=200,
        )
    except Exception as e:
        return error_response(str(e), status_code=500, code="checkout_error")
    

def _resolve_cart_code_from_session(session):
    metadata = session.get('metadata', {}) or {}
    cart_code = metadata.get('cart_code') or session.get('client_reference_id')
    if cart_code:
        return cart_code

    # Fallback for sessions created without metadata/client reference:
    # try matching the user's open cart by total amount.
    email = session.get('customer_email') or (session.get('customer_details') or {}).get('email')
    amount_total = session.get('amount_total')
    if not email:
        return None

    carts = Cart.objects.filter(user__email=email).prefetch_related('cart_items__record')
    # amount_total includes the shipping line item (when present), so subtract
    # it before comparing against product-only cart totals.
    metadata = session.get('metadata', {}) or {}
    try:
        shipping_cents = int(Decimal(metadata.get('shipping_cost') or '0') * 100)
    except Exception:
        shipping_cents = 0
    if amount_total is not None and shipping_cents:
        amount_total -= shipping_cents
    candidates = []

    for cart in carts:
        cents = 0
        for item in cart.cart_items.all():
            cents += int(item.record.sell_price * 100) * int(item.quantity)
        if amount_total is not None and cents == amount_total:
            candidates.append(cart.cart_code)

    if len(candidates) == 1:
        print(f">>> inferred cart_code from email+amount match: {candidates[0]}")
        return candidates[0]

    return None


def fulfill_checkout(session, cart_code=None):
    metadata = session.get('metadata', {}) or {}
    shipped_to = metadata.get('shipped_to', '')
    shipping_details = None
    raw_shipping = metadata.get('shipping_details')
    if raw_shipping:
        try:
            shipping_details = json.loads(raw_shipping) if isinstance(raw_shipping, str) else raw_shipping
        except Exception:
            shipping_details = None
    if isinstance(shipping_details, dict):
        # Address values are stored as plain strings for the admin orders view.
        shipping_details = {key: str(value) for key, value in shipping_details.items()}
    try:
        shipping_cost = Decimal(metadata.get('shipping_cost') or '0') or None
    except Exception:
        shipping_cost = None

    pickup_bazar = None
    raw_bazar_id = metadata.get('bazar_id')
    if raw_bazar_id:
        try:
            pickup_bazar = Bazar.objects.get(pk=raw_bazar_id)
        except (Bazar.DoesNotExist, ValueError, TypeError):
            pickup_bazar = None
            logger.warning("Checkout session %s referenced missing bazar %r", session.get('id'), raw_bazar_id)

    cart = None
    if cart_code:
        cart = Cart.objects.filter(cart_code=cart_code).prefetch_related('cart_items__record').first()

    customer_email = session.get('customer_email') or (session.get('customer_details') or {}).get('email')
    if not customer_email:
        customer_email = "unknown@checkout.local"

    with transaction.atomic():
        order = Order.objects.create(
            stripe_checkout_session_id=session['id'],
            amount=Decimal(session.get('amount_total') or 0) / Decimal(100),
            currency=session['currency'],
            user_email=customer_email,
            shipped_to=shipped_to,
            shipping_details=shipping_details,
            shipping_cost=shipping_cost,
            shipping_courier=metadata.get('shipping_courier', ''),
            shipping_service=metadata.get('shipping_service', ''),
            shipping_link="Preparando para envío" if shipped_to == "home" else "",
            pickup_bazar=pickup_bazar,
            status='paid',
        )

        created_items = False

        try:
            stripe_line_items = stripe.checkout.Session.list_line_items(
                session['id'], expand=['data.price.product']
            )

            for line_item in stripe_line_items.auto_paging_iter():
                product = line_item.get('price', {}).get('product')
                record_id = None
                if isinstance(product, dict):
                    record_id = product.get('metadata', {}).get('record_id')

                quantity = line_item.get('quantity') or 1
                price_cents = line_item.get('price', {}).get('unit_amount')
                if price_cents is None and quantity:
                    price_cents = (line_item.get('amount_total') or 0) // quantity
                price = Decimal(price_cents or 0) / Decimal(100)

                if record_id:
                    try:
                        record = Record.objects.get(id=record_id)
                    except Record.DoesNotExist:
                        continue

                    OrderItem.objects.create(
                        order=order,
                        record=record,
                        quantity=quantity,
                        price=price,
                    )
                    created_items = True
        except Exception:
            created_items = False

        if not created_items and cart:
            for item in cart.cart_items.all():
                OrderItem.objects.create(
                    order=order,
                    record=item.record,
                    quantity=item.quantity,
                    price=item.record.sell_price,
                )

        # Decrement stock per item, atomically and never below zero.
        for item in order.order_items.all():
            updated = Record.objects.filter(id=item.record_id, stock__gte=item.quantity).update(
                stock=F('stock') - item.quantity
            )
            if not updated:
                logger.warning(
                    "Insufficient stock for record %s (order %s): needed %s",
                    item.record_id,
                    order.id,
                    item.quantity,
                )

        if cart:
            cart.cart_items.all().delete()

    try:
        send_order_created_email(order)
    except Exception as exc:
        logger.warning("Order created but email notification failed for %s: %s", order.id, exc)

    try:
        send_order_notification_email(order)
    except Exception as exc:
        logger.warning("Order created but seller notification failed for %s: %s", order.id, exc)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def discogs_search(request):
    """Proxy Discogs database search for releases."""
    query = request.query_params.get('q', '').strip()
    if not query:
        return error_response("q parameter is required", status_code=400, code="query_required")

    page = request.query_params.get('page', 1)
    per_page = request.query_params.get('per_page', 25)

    discogs_token = getattr(settings, 'DISCOGS_TOKEN', '')
    headers = {
        'User-Agent': 'MoctezumaRecords/1.0 +https://moctezumarecords.com',
    }
    if discogs_token:
        headers['Authorization'] = f'Discogs token={discogs_token}'

    try:
        resp = requests.get(
            'https://api.discogs.com/database/search',
            params={
                'q': query,
                'type': 'release',
                'page': page,
                'per_page': per_page,
            },
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Discogs search failed: %s", exc)
        return error_response("Error contacting Discogs", status_code=502, code="discogs_error")

    data = resp.json()
    results = []
    for item in data.get('results', []):
        raw_title = item.get('title', '')
        artist_name = ''
        record_title = raw_title
        if ' - ' in raw_title:
            artist_name, record_title = raw_title.split(' - ', 1)

        genres = item.get('genre', []) or []
        styles = item.get('style', []) or []
        genre_str = ', '.join(genres + styles) if genres or styles else ''

        formats = item.get('format', []) or []
        format_str = ', '.join(formats) if formats else ''

        # Use smaller thumbnail for search results (faster loading)
        thumb = item.get('thumb', '') or item.get('cover_image', '')
        
        results.append({
            'discogs_id': item.get('id'),
            'title': record_title,
            'artist': artist_name,
            'year': item.get('year'),
            'cover_image': thumb,
            'genre': genre_str,
            'style': ', '.join(item.get('style', []) or []),
            'format': format_str,
            'formats': item.get('format', []),
            'resource_url': item.get('resource_url', ''),
            'uri': item.get('uri', ''),
        })

    return Response({
        'results': results,
        'pagination': data.get('pagination', {}),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def discogs_release_detail(request, release_id):
    """Fetch full release details from Discogs.

    Combines the master release tracklist with version-specific notes,
    images, labels, and country info.
    """
    discogs_token = getattr(settings, 'DISCOGS_TOKEN', '')
    headers = {
        'User-Agent': 'MoctezumaRecords/1.0 +https://moctezumarecords.com',
    }
    if discogs_token:
        headers['Authorization'] = f'Discogs token={discogs_token}'

    # 1. Fetch the specific release (version)
    try:
        resp = requests.get(
            f'https://api.discogs.com/releases/{release_id}',
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Discogs release detail failed: %s", exc)
        return error_response("Error contacting Discogs", status_code=502, code="discogs_error")

    release = resp.json()

    # 2. Try to fetch the master release for the canonical tracklist
    master_tracklist = []
    master_year = None
    master_id = release.get('master_id')
    if master_id:
        try:
            master_resp = requests.get(
                f'https://api.discogs.com/masters/{master_id}',
                headers=headers,
                timeout=10,
            )
            if master_resp.ok:
                master = master_resp.json()
                master_tracklist = master.get('tracklist', [])
                master_year = master.get('year')
        except requests.RequestException:
            pass  # Fall back to release-level tracklist

    # 3. Build the tracklist: prefer master, fallback to release
    tracklist_source = master_tracklist or release.get('tracklist', [])
    tracklist = []
    for t in tracklist_source:
        pos = t.get('position', '')
        title = t.get('title', '')
        duration = t.get('duration', '')
        parts = [f"{pos} - {title}" if pos else title]
        if duration:
            parts.append(f"({duration})")
        tracklist.append(' '.join(parts))

    # 4. Build description: combine tracklist with version-specific notes
    notes = (release.get('notes', '') or '').strip()
    description_parts = []
    if tracklist:
        description_parts.append("Contenido (_lista de canciones_):")
        for t in tracklist:
            description_parts.append(f"  {t}")
    if notes:
        description_parts.append("")
        description_parts.append(f"Notas de la versión: {notes}")
    description = '\n'.join(description_parts) if description_parts else ''

    # 5. Extract all images from the release
    images = [img.get('uri', '') for img in release.get('images', [])]

    # 6. Also get master images if we have them and release has none
    if not images and master_id:
        try:
            if master_resp and master_resp.ok:
                master_images = master.get('images', [])
                images = [img.get('uri', '') for img in master_images]
        except Exception:
            pass

    # Extract format details with descriptions for category matching
    format_details = []
    for fmt in release.get('formats', []):
        entry = {'name': fmt.get('name', ''), 'descriptions': fmt.get('descriptions', [])}
        format_details.append(entry)

    # Weight suggestion (grams) for the Record.weight_grams field: derive it
    # from the formats using the store's per-unit defaults, falling back to
    # Discogs' own estimated_weight when the format list is not parseable.
    def _format_qty(fmt):
        try:
            return int(fmt.get('qty') or 1)
        except (TypeError, ValueError):
            return 1

    def _is_seven_inch(fmt):
        haystack = ' '.join([fmt.get('text', '')] + list(fmt.get('descriptions', [])))
        return "7\"" in haystack or '7"' in haystack or ' 7 ' in f' {haystack} '

    weight_suggestion = 0
    for fmt in release.get('formats', []):
        qty = _format_qty(fmt)
        name = (fmt.get('name') or '').lower()
        if 'cd' in name:
            weight_suggestion += CD_UNIT_WEIGHT_GRAMS * qty
        elif 'vinyl' in name or 'record' in name:
            weight_suggestion += (SEVEN_INCH_UNIT_WEIGHT_GRAMS if _is_seven_inch(fmt) else DEFAULT_UNIT_WEIGHT_GRAMS) * qty
    if not weight_suggestion:
        estimated = release.get('estimated_weight')
        try:
            weight_suggestion = int(estimated)
        except (TypeError, ValueError):
            weight_suggestion = None

    return Response({
        'discogs_id': release.get('id'),
        'title': release.get('title', ''),
        'description': description,
        'images': images,
        'tracklist': tracklist,
        'year': release.get('year') or master_year,
        'genres': release.get('genres', []),
        'styles': release.get('styles', []),
        'formats': [f.get('name', '') for f in release.get('formats', [])],
        'format_details': format_details,
        'estimated_weight': release.get('estimated_weight'),
        'weight_grams_suggestion': weight_suggestion,
        'country': release.get('country', ''),
        'labels': [l.get('name', '') for l in release.get('labels', [])],
        'master_id': master_id,
    })

@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    print(
        f">>> stripe_webhook called | method={request.method} path={request.path} "
        f"content_length={len(payload) if payload is not None else 0} has_signature={bool(sig_header)}"
    )
    logger.info(
        "Stripe webhook hit",
        extra={
            "path": request.path,
            "content_length": len(payload) if payload is not None else 0,
            "has_signature": bool(sig_header),
        },
    )
    secrets_to_try = [
        getattr(settings, "STRIPE_WEBHOOK_SECRET", None),
        getattr(settings, "WEBHOOK_SECRET", None),
    ]
    secrets_to_try = [s for i, s in enumerate(secrets_to_try) if s and s not in secrets_to_try[:i]]

    event = None
    last_error = None

    for secret in secrets_to_try:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, secret
            )
            print(">>> webhook signature validated with configured secret")
            break
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            last_error = e
            logger.warning("Stripe webhook signature validation failed: %s", e)
            print(">>> webhook signature validation failed:", e)

    if event is None and settings.DEBUG:
        try:
            event = json.loads(payload.decode() if isinstance(payload, (bytes, bytearray)) else payload)
            logger.warning("Proceeding with unverified Stripe webhook payload because DEBUG=True.")
            print(">>> proceeding with unverified payload (DEBUG)")
        except Exception as e:
            last_error = e

    if event is None:
        if last_error:
            logger.error("Stripe webhook rejected: %s", last_error)
            print(">>> webhook rejected (400):", last_error)
        else:
            print(">>> webhook rejected (400): event could not be parsed")
        return HttpResponse(status=400)

    event_type = event.get('type')
    event_id = event.get('id')
    print(f">>> webhook event received | id={event_id} type={event_type}")
    if settings.DEBUG:
        logger.info("Stripe webhook event: %s | payload: %s", event_type, payload)

    if event_type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        session = event['data']['object']
        cart_code = _resolve_cart_code_from_session(session)
        session_id = session.get('id')
        print(f">>> checkout event | session_id={session_id} cart_code={cart_code}")
        logger.info(
            "Stripe checkout event",
            extra={
                "event_type": event_type,
                "session_id": session_id,
                "cart_code": cart_code,
            },
        )

        if session_id and Order.objects.filter(stripe_checkout_session_id=session_id).exists():
            logger.info("Order already created for this session.")
            print(f">>> order already exists for session {session_id} (200)")
            return HttpResponse(status=200)

        fulfill_checkout(session, cart_code)
        print(f">>> fulfill_checkout called for cart_code={cart_code} (200)")
    elif event_type == 'checkout.session.async_payment_failed':
        # TODO: notify the user or mark a pending order/cart as failed if needed.
        print(">>> async payment failed event received (200)")
        pass

    print(">>> webhook handled successfully (200)")
    return HttpResponse(status=200)



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_checkout_session(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    session_id = (request.data.get('session_id') or '').strip()

    if not session_id:
        return error_response("session_id is required", status_code=400, code="missing_session_id")

    if Order.objects.filter(stripe_checkout_session_id=session_id).exists():
        return Response({"message": "Order already created for this session"}, status=200)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        cart_code = _resolve_cart_code_from_session(session)
        fulfill_checkout(session, cart_code)
        return Response({"message": "Order created successfully"}, status=200)
    except Exception as e:
        return error_response(str(e), status_code=500, code="checkout_complete_error")

@api_view(['GET'])
def checkout_success(request):
    session_id = request.query_params.get('session_id', '').strip()

    if not session_id:
        return error_response("session_id is required", status_code=400, code="missing_session_id")

    order = Order.objects.filter(stripe_checkout_session_id=session_id).first()

    if order:
        return Response({"message": "Payment confirmed ✅ / order created", "status": order.status}, status=200)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.get("payment_status") == "paid":
            cart_code = _resolve_cart_code_from_session(session)
            fulfill_checkout(session, cart_code)
            order = Order.objects.filter(stripe_checkout_session_id=session_id).first()
            if order:
                return Response({"message": "Payment confirmed ✅ / order created", "status": order.status}, status=200)
    except Exception as exc:
        logger.warning("checkout_success fallback failed for session %s: %s", session_id, exc)

    return Response({"message": "We’re still confirming your payment… refresh in a moment", "status": "pending"}, status=200)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_orders(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    email = getattr(request.user, 'email', None)

    if not email:
        return error_response("User email not found", status_code=400, code="user_email_missing")

    orders = Order.objects.filter(user_email=email).order_by('-created_at').prefetch_related('order_items__record')
    serializer = OrderSerializer(orders, many=True)
    return Response(serializer.data, status=200)


@api_view(['GET'])
def bazar_list(request):
    """Public list of UPCOMING bazares, ordered by soonest date first."""
    today = timezone.localdate()
    bazares = Bazar.objects.filter(date__gte=today).order_by('date', 'id')
    serializer = BazarSerializer(bazares, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_list_bazares(request):
    """All bazares (past included), newest first. Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err
    bazares = Bazar.objects.order_by('-date', '-id')
    serializer = BazarSerializer(bazares, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bazar_create(request):
    """Create a bazar. Accepts multipart/form-data with an image file. Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    serializer = BazarSerializer(data=request.data, context={'request': request})
    serializer.is_valid(raise_exception=True)
    bazar = serializer.save()
    return Response(BazarSerializer(bazar, context={'request': request}).data, status=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def admin_update_bazar(request, bazar_id):
    """Update a bazar (partial). Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    try:
        bazar = Bazar.objects.get(pk=bazar_id)
    except Bazar.DoesNotExist:
        return error_response("Bazar no encontrado", status_code=404, code="bazar_not_found")

    serializer = BazarSerializer(bazar, data=request.data, partial=True, context={'request': request})
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(BazarSerializer(bazar, context={'request': request}).data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def admin_delete_bazar(request, bazar_id):
    """Permanently delete a bazar. The image file on disk is left in place,
    mirroring admin_delete_record's behavior. Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    try:
        bazar = Bazar.objects.get(pk=bazar_id)
    except Bazar.DoesNotExist:
        return error_response("Bazar no encontrado", status_code=404, code="bazar_not_found")

    name = bazar.name
    bazar.delete()
    return Response({"message": f"Bazar '{name}' eliminado permanentemente"}, status=200)
