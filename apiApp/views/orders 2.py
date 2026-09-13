"""Customer order history view."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_email_verified, error_response
from ..models import Order
from ..serilizers import OrderSerializer


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