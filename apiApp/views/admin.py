"""Admin-only views: user, record and order management."""
import logging
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_admin, error_response
from ..models import Order, Record, User
from ..serilizers import (
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    OrderSerializer,
    RecordDetailSerializer,
    RecordUpdateSerializer,
)
from ..services import send_order_shipped_email

logger = logging.getLogger(__name__)


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


# ── Admin: order management ─────────────────────────────────────────────


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