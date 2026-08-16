import json
import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Artist, Cart, CartItem, Category, Order, OrderItem, Record, Review, Wishlist, WishlistItem
from .emails import send_password_recovery_email, send_verification_email, send_welcome_email
from .services import send_order_created_email, send_order_notification_email
from .pagination import StandardResultsSetPagination
from .serilizers import (
    ArtistSerializer,
    CartItemSerializer,
    CartSerializer,
    CategoryListSerializer,
    CategorySerializer,
    OrderSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RecordDetailSerializer,
    RecordListSerializer,
    ReviewSerializer,
    UserRegistrationSerializer,
    UserSerializer,
    VerifyEmailSerializer,
    WishlistSerializer,
)
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
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

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
            return error_response("Invalid credentials", status_code=401, code="invalid_credentials")

    user = authenticate(username=username, password=password)
    if not user:
        return error_response("Invalid credentials", status_code=401, code="invalid_credentials")
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
        },
        status=200,
    )

@api_view(['POST'])
@throttle_classes([ScopedRateThrottle])
def request_password_reset(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

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

request_password_reset.view_class.throttle_scope = 'password_reset'

@api_view(['POST'])
@throttle_classes([ScopedRateThrottle])
def confirm_password_reset(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    user = serializer.context['user']
    user.set_password(serializer.validated_data['new_password'])
    user.save(update_fields=['password'])
    logger.info("Password reset for user %s", user.id)
    return Response({"message": "Password reset successfully"}, status=200)

confirm_password_reset.view_class.throttle_scope = 'password_reset'

@api_view(['POST'])
def verify_email(request):
    serializer = VerifyEmailSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

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

@api_view(['GET'])
def record_list(request):
    records = Record.objects.filter(featured=True)
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(records, request)
    serializer = RecordListSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

@api_view(['GET'])
def artist_list(request):
    artists = Artist.objects.all().order_by('name')
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(artists, request)
    serializer = ArtistSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

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
def get_cart(request, cart_code):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    try:
        cart = Cart.objects.get(cart_code=cart_code)
    except Cart.DoesNotExist:
        return error_response("Cart not found", status_code=404, code="cart_not_found")
    
    serializer = CartSerializer(cart)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_carts(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    carts = Cart.objects.all()
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
    email = request.data.get('email')
    try:
        quantity = int(request.data.get('quantity', 1))
    except (TypeError, ValueError):
        return error_response("quantity must be a number", status_code=400, code="quantity_invalid")
    if quantity < 1:
        return error_response("quantity must be at least 1", status_code=400, code="quantity_invalid")
    user = User.objects.get(email=email) if email else None
    if cart_code:
        cart, _ = Cart.objects.get_or_create(cart_code=cart_code, defaults={'user': user})
    else:
        cart = Cart.objects.create(user=user)
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

@api_view(['GET'])
def record_search(request):
    query = request.query_params.get('query')
    if not query:
        return error_response("query parameter is required", status_code=400, code="query_required")
    
    records = Record.objects.filter(Q(title__icontains=query) |
                                    Q(artist__name__icontains=query) |
                                    Q(genere__name__icontains=query) | 
                                    Q(category__name__icontains=query))
    
    
    if not records.exists():
        return Response({"message": "No records found matching the query"}, status=404)
    serializer = RecordListSerializer(records, many=True)
    return Response(serializer.data)


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

    if not email:
        return error_response("User email not found", status_code=400, code="user_email_missing")

    cart = Cart.objects.filter(cart_code=cart_code).prefetch_related('cart_items__record').first()

    if not cart or cart.cart_items.count() == 0:
        return error_response("Cart is empty or not found", status_code=400, code="cart_empty")
    
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
                    'unit_amount': int(item.record.price * 100),
                },
                'quantity': item.quantity,
            })

        metadata = {'cart_code': cart_code, 'shipped_to': shipped_to}
        if shipping_details:
            metadata['shipping_details'] = json.dumps(shipping_details)

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
    candidates = []

    for cart in carts:
        cents = 0
        for item in cart.cart_items.all():
            cents += int(item.record.price * 100) * int(item.quantity)
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
            ship_link="Preparando para envío" if shipped_to == "home" else "",
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
                    price=item.record.price,
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
