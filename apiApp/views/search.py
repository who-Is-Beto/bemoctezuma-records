"""Record search view (tokenized, accent- and punctuation-insensitive)."""
from rest_framework.decorators import api_view

from .common import error_response
from ..pagination import StandardResultsSetPagination
from ..serilizers import RecordListSerializer
from ..services import search_records


@api_view(['GET'])
def record_search(request):
    query = request.query_params.get('query')
    if not query:
        return error_response("query parameter is required", status_code=400, code="query_required")

    # ?available=true -> only records with at least 1 item in stock
    available = request.query_params.get('available')
    if available is not None and available.lower() in ('true', '1', 'yes'):
        available = True
    else:
        available = False

    # ?category=lp,7,cd,... -> filter by category slug
    records = search_records(
        query,
        category=request.query_params.get('category'),
        available=available,
    )

    # Same paginated envelope as /records/ (count/next/previous/results).
    # "No matches" is a valid, empty page — not an error.
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(records, request)
    serializer = RecordListSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)