"""Encargar un disco: visitors ask the store to find a record for them.

Nothing is stored: the request goes straight to the store's inbox
(SELLER_NOTIFY_EMAILS) with Reply-To set to the visitor, so answering the
email answers them.
"""
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response

from .common import error_response
from ..serilizers import RecordRequestSerializer
from ..services import DiscogsServiceError, discogs_album_formats, discogs_album_search
from ..services.emailing import send_record_request_email
from ..throttling import LiveScopedRateThrottle


@api_view(['POST'])
@throttle_classes([LiveScopedRateThrottle])
def record_request(request):
    serializer = RecordRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return error_response(
            "Revisa los campos del formulario.", status_code=400,
            code="invalid_request", details=serializer.errors,
        )
    data = serializer.validated_data
    if data.pop('website'):
        # Bot: pretend it worked so it doesn't retry.
        return Response({'sent': True}, status=201)
    if not send_record_request_email(data):
        return error_response(
            "No pudimos enviar tu encargo. Intenta de nuevo en unos minutos.",
            status_code=503, code="email_failed",
        )
    return Response({'sent': True}, status=201)


record_request.view_class.throttle_scope = 'record_request'


@api_view(['GET'])
@throttle_classes([LiveScopedRateThrottle])
def record_request_discogs_search(request):
    """Public Discogs lookup for the form: one result per album."""
    query = request.query_params.get('q', '').strip()[:100]
    if len(query) < 3:
        return Response({'results': []})
    try:
        results = discogs_album_search(query)
    except DiscogsServiceError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)
    return Response({'results': results})


record_request_discogs_search.view_class.throttle_scope = 'record_request_discogs'


@api_view(['GET'])
@throttle_classes([LiveScopedRateThrottle])
def record_request_discogs_formats(request, kind, discogs_id):
    """Formats the picked album exists in: the form offers only those."""
    if kind not in ('master', 'release'):
        return error_response("Tipo de disco desconocido.", status_code=404, code="not_found")
    try:
        formats = discogs_album_formats(kind, discogs_id)
    except DiscogsServiceError as exc:
        return error_response(exc.message, status_code=502, code=exc.code)
    return Response({'formats': formats})


record_request_discogs_formats.view_class.throttle_scope = 'record_request_discogs'
