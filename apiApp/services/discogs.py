import logging

import requests
from django.conf import settings

from .shipping import (
    CD_UNIT_WEIGHT_GRAMS,
    DEFAULT_UNIT_WEIGHT_GRAMS,
    SEVEN_INCH_UNIT_WEIGHT_GRAMS,
)

logger = logging.getLogger(__name__)

DISCOGS_API_URL = 'https://api.discogs.com'
DISCOGS_USER_AGENT = 'MoctezumaRecords/1.0 +https://moctezumarecords.com'
DISCOGS_TIMEOUT = 10


class DiscogsServiceError(Exception):
    """Raised when the Discogs API cannot be reached or answers badly."""

    def __init__(self, message="Error contacting Discogs", code="discogs_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _discogs_headers():
    headers = {'User-Agent': DISCOGS_USER_AGENT}
    discogs_token = getattr(settings, 'DISCOGS_TOKEN', '')
    if discogs_token:
        headers['Authorization'] = f'Discogs token={discogs_token}'
    return headers


def discogs_search(query, *, page=1, per_page=25):
    """Proxy Discogs database search for releases.

    Returns a dict with ``results`` and ``pagination`` ready to serialize.
    """
    try:
        resp = requests.get(
            f'{DISCOGS_API_URL}/database/search',
            params={
                'q': query,
                'type': 'release',
                'page': page,
                'per_page': per_page,
            },
            headers=_discogs_headers(),
            timeout=DISCOGS_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Discogs search failed: %s", exc)
        raise DiscogsServiceError() from exc

    data = resp.json()
    results = []
    for item in data.get('results', []):
        raw_title = item.get('title', '')
        artist_name = ''
        record_title = raw_title
        if ' - ' in raw_title:
            artist_name, record_title = raw_title.split(' - ', 1)

        genres = item.get('genre', []) or []
        styles = item.get('style', []) or []
        genre_str = ', '.join(genres + styles) if genres or styles else ''

        formats = item.get('format', []) or []
        format_str = ', '.join(formats) if formats else ''

        # Use smaller thumbnail for search results (faster loading)
        thumb = item.get('thumb', '') or item.get('cover_image', '')

        results.append({
            'discogs_id': item.get('id'),
            'title': record_title,
            'artist': artist_name,
            'year': item.get('year'),
            'cover_image': thumb,
            'genre': genre_str,
            'style': ', '.join(item.get('style', []) or []),
            'format': format_str,
            'formats': item.get('format', []),
            'resource_url': item.get('resource_url', ''),
            'uri': item.get('uri', ''),
        })

    return {
        'results': results,
        'pagination': data.get('pagination', {}),
    }


def discogs_release_detail(release_id):
    """Fetch full release details from Discogs.

    Combines the master release tracklist with version-specific notes,
    images, labels, and country info. Returns the payload dict ready to
    serialize.
    """
    # 1. Fetch the specific release (version)
    try:
        resp = requests.get(
            f'{DISCOGS_API_URL}/releases/{release_id}',
            headers=_discogs_headers(),
            timeout=DISCOGS_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Discogs release detail failed: %s", exc)
        raise DiscogsServiceError() from exc

    release = resp.json()

    # 2. Try to fetch the master release for the canonical tracklist
    master_tracklist = []
    master_year = None
    master_id = release.get('master_id')
    if master_id:
        try:
            master_resp = requests.get(
                f'{DISCOGS_API_URL}/masters/{master_id}',
                headers=_discogs_headers(),
                timeout=DISCOGS_TIMEOUT,
            )
            if master_resp.ok:
                master = master_resp.json()
                master_tracklist = master.get('tracklist', [])
                master_year = master.get('year')
        except requests.RequestException:
            pass  # Fall back to release-level tracklist

    # 3. Build the tracklist: prefer master, fallback to release
    tracklist_source = master_tracklist or release.get('tracklist', [])
    tracklist = []
    for t in tracklist_source:
        pos = t.get('position', '')
        title = t.get('title', '')
        duration = t.get('duration', '')
        parts = [f"{pos} - {title}" if pos else title]
        if duration:
            parts.append(f"({duration})")
        tracklist.append(' '.join(parts))

    # 4. Build description: combine tracklist with version-specific notes
    notes = (release.get('notes', '') or '').strip()
    description_parts = []
    if tracklist:
        description_parts.append("Contenido (_lista de canciones_):")
        for t in tracklist:
            description_parts.append(f"  {t}")
    if notes:
        description_parts.append("")
        description_parts.append(f"Notas de la versión: {notes}")
    description = '\n'.join(description_parts) if description_parts else ''

    # 5. Extract all images from the release
    images = [img.get('uri', '') for img in release.get('images', [])]

    # 6. Also get master images if we have them and release has none
    if not images and master_id:
        try:
            if master_resp and master_resp.ok:
                master_images = master.get('images', [])
                images = [img.get('uri', '') for img in master_images]
        except Exception:
            pass

    # Extract format details with descriptions for category matching
    format_details = []
    for fmt in release.get('formats', []):
        entry = {'name': fmt.get('name', ''), 'descriptions': fmt.get('descriptions', [])}
        format_details.append(entry)

    # Weight suggestion (grams) for the Record.weight_grams field: derive it
    # from the formats using the store's per-unit defaults, falling back to
    # Discogs' own estimated_weight when the format list is not parseable.
    def _format_qty(fmt):
        try:
            return int(fmt.get('qty') or 1)
        except (TypeError, ValueError):
            return 1

    def _is_seven_inch(fmt):
        haystack = ' '.join([fmt.get('text', '')] + list(fmt.get('descriptions', [])))
        return "7\"" in haystack or '7"' in haystack or ' 7 ' in f' {haystack} '

    weight_suggestion = 0
    for fmt in release.get('formats', []):
        qty = _format_qty(fmt)
        name = (fmt.get('name') or '').lower()
        if 'cd' in name:
            weight_suggestion += CD_UNIT_WEIGHT_GRAMS * qty
        elif 'vinyl' in name or 'record' in name:
            weight_suggestion += (SEVEN_INCH_UNIT_WEIGHT_GRAMS if _is_seven_inch(fmt) else DEFAULT_UNIT_WEIGHT_GRAMS) * qty
    if not weight_suggestion:
        estimated = release.get('estimated_weight')
        try:
            weight_suggestion = int(estimated)
        except (TypeError, ValueError):
            weight_suggestion = None

    return {
        'discogs_id': release.get('id'),
        'title': release.get('title', ''),
        'description': description,
        'images': images,
        'tracklist': tracklist,
        'year': release.get('year') or master_year,
        'genres': release.get('genres', []),
        'styles': release.get('styles', []),
        'formats': [f.get('name', '') for f in release.get('formats', [])],
        'format_details': format_details,
        'estimated_weight': release.get('estimated_weight'),
        'weight_grams_suggestion': weight_suggestion,
        'country': release.get('country', ''),
        'labels': [l.get('name', '') for l in release.get('labels', [])],
        'master_id': master_id,
    }