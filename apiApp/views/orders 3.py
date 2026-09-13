"""Order views: admin order management + the customer's own orders."""
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..models import Order
from ..serilizers import OrderSerializer
from ..services import send_order_shipped_email
from .common import _require_admin, _require_email_verified, error_response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_list_orders(request):
    """List every order (newest first). Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    orders = (
        Order.objects.all()
        .order_by('-created_at')
        .select_related('pickup_bazar')
        .prefetch_related(
            'order_items__record__artist',
            'order_items__record__category',
            'order_items__record__genere',
        )
    )
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
def get_user_orders(request):
    blocked = _require_email_verified(request)
    if blocked:
        return blocked
    email = getattr(request.user, 'email', None)

    if not email:
        return error_response("User email not found", status_code=400, code="user_email_missing")

    orders = (
        Order.objects.filter(user_email=email)
        .order_by('-created_at')
        .select_related('pickup_bazar')
        .prefetch_related(
            'order_items__record__artist',
            'order_items__record__category',
            'order_items__record__genere',
        )
    )
    serializer = OrderSerializer(orders, many=True)
    return Response(serializer.data, status=200)
