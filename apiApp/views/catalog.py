"""Catalog views: records, artists, genres, categories."""
from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import _require_admin, error_response
from ..models import Artist, Category, Genere, Record
from ..pagination import StandardResultsSetPagination
from ..serilizers import (
    ArtistSerializer,
    CategoryListSerializer,
    CategorySerializer,
    GenereSerializer,
    RecordAdminSerializer,
    RecordCreateSerializer,
    RecordDetailSerializer,
    RecordListSerializer,
)
from ..services import apply_record_filters, most_similar_artist, search_artists


@api_view(['GET'])
def record_list(request):
    records = Record.objects.all()
    # "featured" only curates the main listing (non-featured records show up
    # in search). An artist page lists everything by that artist.
    if not request.query_params.get('artist'):
        records = records.filter(featured=True)

    # ?category=lp,7,cd,... -> filter by category slug
    category = request.query_params.get('category')
    if category:
        records = records.filter(category__slug=category)

    # ?available=true -> only records with at least 1 item in stock
    available = request.query_params.get('available')
    if available is not None and available.lower() in ('true', '1', 'yes'):
        records = records.filter(stock__gt=0)

    # genere / artist / condition / price range / ordering (+ FK joins)
    records = apply_record_filters(records, request.query_params)

    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(records, request)
    serializer = RecordListSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def record_create(request):
    """Create a new record. Admin only."""
    admin_err = _require_admin(request, 'apiApp.add_record')
    if admin_err:
        return admin_err
    serializer = RecordCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    record = serializer.save()
    # Return the full detail (admin view: private fields included)
    return Response(RecordAdminSerializer(record).data, status=201)


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


def _get_artist_or_404(artist_id):
    try:
        return Artist.objects.get(pk=artist_id), None
    except Artist.DoesNotExist:
        return None, error_response("Artista no encontrado", status_code=404, code="artist_not_found")


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def artist_usage(request, artist_id):
    """How many records reference an artist + the most similar other artist
    (reassignment suggestion) before deleting it. Admin / delete_artist."""
    admin_err = _require_admin(request, 'apiApp.delete_artist')
    if admin_err:
        return admin_err
    artist, err = _get_artist_or_404(artist_id)
    if err:
        return err
    count = artist.records.count()
    suggestion = most_similar_artist(artist) if count else None
    return Response({
        'records_count': count,
        'suggestion': ArtistSerializer(suggestion).data if suggestion else None,
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def artist_delete(request, artist_id):
    """Delete an artist. Admin / delete_artist.

    When records reference it, the body must say where they go:
    ``{"reassign_to": <artist id>}`` or ``{"new_artist_name": "..."}``
    (returns the existing artist on a case-insensitive name match, like
    /artists/create/). Reassignment + delete run in one transaction, so no
    record is ever left without an artist. Without a target -> 409.
    """
    admin_err = _require_admin(request, 'apiApp.delete_artist')
    if admin_err:
        return admin_err

    reassign_to = request.data.get('reassign_to')
    new_name = (request.data.get('new_artist_name') or '').strip()

    with transaction.atomic():
        try:
            # Lock the row so a concurrent record save can't slip in between
            # the count and the delete.
            artist = Artist.objects.select_for_update().get(pk=artist_id)
        except Artist.DoesNotExist:
            return error_response("Artista no encontrado", status_code=404, code="artist_not_found")

        records = artist.records.all()
        count = records.count()
        target = None
        if count:
            if reassign_to not in (None, ''):
                try:
                    target = Artist.objects.get(pk=int(reassign_to))
                except (Artist.DoesNotExist, TypeError, ValueError):
                    return error_response("Artista destino no encontrado", status_code=400, code="invalid_reassign_target")
            elif new_name:
                target = Artist.objects.filter(name__iexact=new_name).first() or Artist.objects.create(name=new_name)
            else:
                return error_response(
                    f"{count} disco(s) usan este artista; elige a quién reasignarlos.",
                    status_code=409, code="artist_in_use",
                )
            if target.pk == artist.pk:
                return error_response("No puedes reasignar al mismo artista", status_code=400, code="invalid_reassign_target")
            records.update(artist=target)
        artist.delete()

    return Response({
        'deleted': artist_id,
        'reassigned': count,
        'reassigned_to': ArtistSerializer(target).data if target else None,
    })


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