"""Admin-only views: user, record and order management."""
import logging
from django.contrib.auth.models import Group
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
    RecordAdminSerializer,
    RecordUpdateSerializer,
    RoleSerializer,
)
from ..admin_panel import ACCESS, ACCESS_LABEL, TABS
from ..services import send_order_shipped_email

logger = logging.getLogger(__name__)


# ── Admin: user management ──────────────────────────────────────────────


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_list_users(request):
    """List all users. Admin, or a role with the Gestionar usuarios tab."""
    admin_err = _require_admin(request, 'apiApp.tab_manage_users')
    if admin_err:
        return admin_err

    users = User.objects.all().order_by('-date_joined').prefetch_related('groups')
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
    """Delete a user. Admin, or a role with delete_user (non-admin targets only).
    Cannot delete yourself."""
    admin_err = _require_admin(request, 'apiApp.delete_user')
    if admin_err:
        return admin_err

    try:
        target_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return error_response("Usuario no encontrado", status_code=404, code="user_not_found")

    if target_user.role == 'ADMIN' and request.user.role != 'ADMIN':
        return error_response(
            "Solo un administrador puede eliminar a otro administrador.",
            status_code=403,
            code="forbidden",
        )

    if target_user.id == request.user.id:
        return error_response(
            "No puedes eliminar tu propia cuenta desde aquí.",
            status_code=400,
            code="self_delete_forbidden",
        )

    target_user.delete()
    return Response({"message": "Usuario eliminado correctamente"}, status=200)


# ── Admin: custom roles (Django Groups) ─────────────────────────────────


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_roles_catalog(request):
    """What a role can grant: Administración access → tabs → per-tab actions. Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    return Response({
        "access": {"codename": ACCESS, "label": ACCESS_LABEL},
        "tabs": [
            {
                "id": tab_id,
                "label": label,
                "codename": tab_perm,
                "actions": [{"codename": code, "label": text} for code, text in actions],
            }
            for tab_id, label, tab_perm, actions in TABS
        ],
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def admin_roles(request):
    """GET lists roles, POST creates one ({name, permissions: [codenames]}). Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    if request.method == 'POST':
        serializer = RoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=201)

    roles = Group.objects.all().order_by('name').prefetch_related('permissions')
    return Response(RoleSerializer(roles, many=True).data)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def admin_role_detail(request, role_id):
    """PATCH renames / sets permissions of a role, DELETE removes it. Admin only."""
    admin_err = _require_admin(request)
    if admin_err:
        return admin_err

    role = get_object_or_404(Group, pk=role_id)
    if request.method == 'DELETE':
        role.delete()
        return Response({"message": "Rol eliminado correctamente"}, status=200)

    serializer = RoleSerializer(role, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


# ── Admin: record management ────────────────────────────────────────────


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def admin_update_record(request, record_id):
    """GET: the full record for the edit form. PATCH: update a record (stock,
    final_sale_price, all fields). Admin, or a role with change_record.

    The edit form must load from here: list rows lack description / weight /
    release year / featured / items_inside (and the private fields), and
    saving a form prefilled from them overwrote those with blanks.
    """
    admin_err = _require_admin(request, 'apiApp.change_record')
    if admin_err:
        return admin_err

    try:
        record = Record.objects.get(pk=record_id)
    except Record.DoesNotExist:
        return error_response("Disco no encontrado", status_code=404, code="record_not_found")

    if request.method == 'PATCH':
        serializer = RecordUpdateSerializer(record, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

    # Full detail: nested artist/category/genere plus the private fields
    return Response(RecordAdminSerializer(record).data)


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
    admin_err = _require_admin(request, 'apiApp.delete_record')
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
    admin_err = _require_admin(request, 'apiApp.tab_manage_orders')
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
    admin_err = _require_admin(request, 'apiApp.change_order')
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