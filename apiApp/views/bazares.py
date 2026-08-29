"""Bazar (in-store pop-up market) views: public list plus admin CRUD."""
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_admin, error_response
from ..models import Bazar
from ..serilizers import BazarSerializer


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