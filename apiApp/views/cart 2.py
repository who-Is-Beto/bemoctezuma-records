"""Cart views: read, add, update and delete cart items."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _optimized_cart, _require_email_verified, error_response
from ..models import Cart, CartItem, Record
from ..serilizers import CartItemSerializer, CartSerializer


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

    cart = _optimized_cart(cart)
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
    carts = _optimized_cart(carts)
    serializer = CartSerializer(carts, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_cart_items(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    cart_items = CartItem.objects.select_related(
        'record__artist', 'record__category', 'record__genere',
    ).all()
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

    cart = _optimized_cart(cart)
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
    cartitem.save()

    cart = _optimized_cart(cartitem.cart)
    serializer = CartSerializer(cart)
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
        cart = _optimized_cart(cart)
        serializer = CartSerializer(cart)
        return Response(serializer.data, status=200)
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
        serializer = CartSerializer(cart)
        return Response(serializer.data, status=200)
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