"""Discogs proxy views: search releases and fetch release details."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .common import error_response
from ..services import DiscogsServiceError
from ..services import discogs as discogs_service


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def discogs_search(request):
    """Proxy Discogs database search for releases."""
    query = request.query_params.get('q', '').strip()
    if not query:
        return error_response("q parameter is required", status_code=400, code="query_required")

    try:
        payload = discogs_service.discogs_search(
            query,
            page=request.query_params.get('page', 1),
            per_page=request.query_params.get('per_page', 25),
        )
    except DiscogsServiceError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)

    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def discogs_release_detail(request, release_id):
    """Fetch full release details from Discogs.

    Combines the master release tracklist with version-specific notes,
    images, labels, and country info.
    """
    try:
        payload = discogs_service.discogs_release_detail(release_id)
    except DiscogsServiceError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)

    return Response(payload)