"""Owners (who a record belongs to) and the sales registered in Punto de venta."""
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Prefetch
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_admin, error_response
from ..models import Owner, Record, Sale, SaleItem
from ..serilizers import (
    OwnerSerializer,
    SaleCreateSerializer,
    SaleSerializer,
    SalesFilterSerializer,
)
from ..services import commission_rate_for, notify_sale_owners, sales_metrics, split_commission


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def owner_list(request):
    """All owners by name, for the record form and the Ventas filter.
    Anyone with Administración access."""
    admin_err = _require_admin(request, 'apiApp.access_admin_panel')
    if admin_err:
        return admin_err
    return Response(OwnerSerializer(Owner.objects.order_by('name'), many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def owner_create(request):
    """Create an owner ({name, email}) from the record form."""
    # The picker lives in the record form, so creating OR editing records grants it.
    admin_err = _require_admin(request, 'apiApp.add_record') and _require_admin(request, 'apiApp.change_record')
    if admin_err:
        return admin_err
    serializer = OwnerSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sale_create(request):
    """Register a sale from Punto de venta:
    {"items": [{record, quantity, price?}], "payment_method", "commission_rate"?}.

    Card pays ``commission_rate`` % (default 4.06) of the whole ticket; cash and
    transfer pay none. Each line keeps its proportional share of the commission.
    All-or-nothing: if any record lacks stock nothing is saved. After commit,
    each owner gets one email with only their records; records without owner
    and failed emails never undo the sale, they come back in ``warnings``.
    Admin, or a role that can edit and sell records (change_record).
    """
    admin_err = _require_admin(request, 'apiApp.change_record')
    if admin_err:
        return admin_err

    serializer = SaleCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    lines = [
        (line['record'], line['quantity'], line.get('price', line['record'].effective_price))
        for line in data['items']
    ]
    rate = commission_rate_for(data['payment_method'], data.get('commission_rate'))
    subtotals = [price * quantity for _, quantity, price in lines]
    commission, shares = split_commission(subtotals, rate)

    with transaction.atomic():
        sale = Sale.objects.create(
            payment_method=data['payment_method'],
            commission_rate=rate,
            commission_amount=commission,
            final_sale_price=sum(subtotals, Decimal('0.00')) - commission,
        )
        for (record, quantity, price), share in zip(lines, shares):
            # Same guard as fulfill_checkout: atomic and never below zero.
            sold = Record.objects.filter(pk=record.pk, stock__gte=quantity).update(
                stock=F('stock') - quantity, final_sale_price=price,
            )
            if not sold:
                record.refresh_from_db(fields=['stock'])
                transaction.set_rollback(True)
                return error_response(
                    f'No hay suficiente stock de "{record.title}" (disponible: {record.stock})',
                    status_code=400,
                    code="insufficient_stock",
                )
            SaleItem.objects.create(
                sale=sale, record=record, owner_id=record.owner_id, quantity=quantity, price=price,
                commission_amount=share, title=record.title,
                artist=record.artist.name if record.artist else '',
                cover_image_url=record.cover_image_url,
            )

    warnings = notify_sale_owners(sale)
    return Response({
        'sale_id': sale.id,
        'sale': SaleSerializer(_sales_with_items().get(pk=sale.pk)).data,
        'warnings': warnings,
    }, status=201)


def _sales_with_items():
    return Sale.objects.prefetch_related(
        Prefetch('items', queryset=SaleItem.objects.select_related('owner').order_by('id'))
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_list(request):
    """Ventas history: tickets newest first, each with all its items, plus totals.

    Optional filters: ?date_from= / ?date_to= (YYYY-MM-DD, inclusive, store
    time) and ?owner=<id> (tickets with at least one of that owner's records;
    ``totals`` then count only that owner's lines). Admin, or a role with the
    Ventas tab.
    """
    admin_err = _require_admin(request, 'apiApp.tab_sales')
    if admin_err:
        return admin_err

    filters = SalesFilterSerializer(data=request.query_params)
    filters.is_valid(raise_exception=True)
    date_from = filters.validated_data.get('date_from')
    date_to = filters.validated_data.get('date_to')
    owner = filters.validated_data.get('owner')

    items = SaleItem.objects.all()
    # __date converts to TIME_ZONE (store time), so a 7 pm sale stays on its day.
    if date_from:
        items = items.filter(sale__created_at__date__gte=date_from)
    if date_to:
        items = items.filter(sale__created_at__date__lte=date_to)
    if owner:
        items = items.filter(owner_id=owner)
    sales = _sales_with_items().filter(pk__in=items.values('sale_id')).order_by('-created_at', '-id')

    # ponytail: unpaginated like /orders/all/; the date filter bounds it. Paginate if it gets slow.
    subtotal = sum((item.price * item.quantity for item in items), Decimal('0.00'))
    commission = sum((item.commission_amount for item in items), Decimal('0.00'))
    return Response({
        'results': SaleSerializer(sales, many=True).data,
        'totals': {
            'subtotal': str(subtotal),
            'commission': str(commission),
            'net': str(subtotal - commission),
        },
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_metrics_view(request):
    """Ventas → Métricas: point of sale + online orders, ?date_from=&date_to=
    (YYYY-MM-DD, inclusive, store time). Admin, or a role with the Ventas tab."""
    admin_err = _require_admin(request, 'apiApp.tab_sales')
    if admin_err:
        return admin_err

    filters = SalesFilterSerializer(data=request.query_params)
    filters.is_valid(raise_exception=True)
    return Response(sales_metrics(
        filters.validated_data.get('date_from'), filters.validated_data.get('date_to'),
    ))
