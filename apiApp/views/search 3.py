"""Record search view and its query-building helpers."""
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..models import Artist, Category, Genere, Record
from ..serilizers import RecordListSerializer
from .common import _query_tokens, _slug_contains, error_response


def _record_token_q(token):
    """Q matching one token against any searchable field's normalized slug."""
    return (
        Q(id__in=_slug_contains(Record, token))
        | Q(artist__in=_slug_contains(Artist, token))
        | Q(genere__in=_slug_contains(Genere, token))
        | Q(category__in=_slug_contains(Category, token))
    )


@api_view(['GET'])
def record_search(request):
    query = request.query_params.get('query')
    if not query:
        return error_response("query parameter is required", status_code=400, code="query_required")

    tokens = _query_tokens(query)
    if tokens:
        # Every word must match somewhere (title/artist/genre/category),
        # in any order — 'floyd dark side' finds Pink Floyd's Dark Side.
        combined_q = Q()
        for token in tokens:
            combined_q &= _record_token_q(token)
        records = Record.objects.filter(combined_q).order_by('-id')
    else:
        # Term was pure punctuation; fall back to the legacy substring match.
        records = Record.objects.filter(
            Q(title__icontains=query)
            | Q(artist__name__icontains=query)
            | Q(genere__name__icontains=query)
            | Q(category__name__icontains=query)
        ).order_by('-id')

    # ?category=lp,7,cd,... -> filter by category slug
    category = request.query_params.get('category')
    if category:
        records = records.filter(category__slug=category)

    # ?available=true -> only records with at least 1 item in stock
    available = request.query_params.get('available')
    if available is not None and available.lower() in ('true', '1', 'yes'):
        records = records.filter(stock__gt=0)

    if not records.exists():
        return Response({"message": "No records found matching the query"}, status=404)
    serializer = RecordListSerializer(records, many=True)
    return Response(serializer.data)
