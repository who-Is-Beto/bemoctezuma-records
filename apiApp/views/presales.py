"""Preventas for customers: browse, quote shipping, reserve, settle, own orders,
and the announcement list. Money rules live in services/presales.py."""
from django.db.models import Prefetch
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response

from .common import error_response
from ..admin_panel import ACCESS
from ..models import PreSale, PreSaleOption
from ..serilizers import PreSalePublicSerializer
from ..services import presales as svc
from ..services.shipping import ShippingQuoteError
from ..throttling import LiveScopedRateThrottle


def _presales_qs():
    return PreSale.objects.prefetch_related(
        Prefetch('options', queryset=PreSaleOption.objects.select_related('category'))
    )


def can_manage_presales(user):
    return bool(
        user and user.is_authenticated
        and (user.role == 'ADMIN' or (user.has_perm(f'apiApp.{ACCESS}') and user.has_perm('apiApp.tab_presales')))
    )


def presale_error(exc, status_code=400):
    return error_response(exc.message, status_code=status_code, code=exc.code)


@api_view(['GET'])
def presale_list(request):
    presales = _presales_qs().filter(status='published').order_by('purchase_deadline', 'id')
    return Response(PreSalePublicSerializer(presales, many=True, context={'request': request}).data)


@api_view(['GET'])
def presale_detail(request, slug):
    qs = _presales_qs()
    if not can_manage_presales(request.user):
        qs = qs.exclude(status='draft')
    presale = qs.filter(slug=slug).first()
    if presale is None:
        return error_response("Esta preventa no existe.", status_code=404, code="not_found")
    return Response(PreSalePublicSerializer(presale, context={'request': request}).data)


@api_view(['POST'])
@throttle_classes([LiveScopedRateThrottle])
def presale_shipping_estimate(request):
    option = PreSaleOption.objects.select_related('category').filter(pk=request.data.get('option_id')).first() \
        if str(request.data.get('option_id', '')).isdigit() else None
    if option is None:
        return error_response("Elige una opción de la preventa.", status_code=400, code="invalid_option")
    try:
        quote = svc.quote_shipping(option, request.data.get('zip'))
    except svc.PresaleError as exc:
        return presale_error(exc)
    except ShippingQuoteError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)
    return Response({
        'shipping_estimate': str(quote['cost']),
        'courier': quote['courier'],
        'service': quote['service'],
    })


presale_shipping_estimate.view_class.throttle_scope = 'presale_quote'
