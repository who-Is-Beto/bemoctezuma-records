"""Catalog views: records, artists, genres, categories."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import error_response
from ..models import Artist, Category, Genere, Record
from ..pagination import StandardResultsSetPagination
from ..serilizers import (
    ArtistSerializer,
    CategoryListSerializer,
    CategorySerializer,
    GenereSerializer,
    RecordCreateSerializer,
    RecordDetailSerializer,
    RecordListSerializer,
)
from ..services import search_artists


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
    artists = search_artists(q)
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