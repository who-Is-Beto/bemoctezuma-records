"""Cover thumbnails: small WebP copies of each record's cover in our storage
(R2 in prod), so the storefront doesn't hotlink full-size Discogs images.

``Record.cover_thumbs`` = {"src": <cover they were made from>, "320": url, "640": url}.
A cover change leaves ``src`` stale and the next save regenerates them.
"""
import hashlib
import io
import logging

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image

logger = logging.getLogger(__name__)

THUMB_WIDTHS = (320, 640)
WEBP_QUALITY = 78
MAX_SOURCE_BYTES = 10 * 1024 * 1024
USER_AGENT = 'MoctezumaRecords/1.0 +https://moctezumarecords.com'


def thumbs_are_stale(record):
    return bool(record.cover_image_url) and (record.cover_thumbs or {}).get('src') != record.cover_image_url


def make_cover_thumbs(record):
    """Download the cover, save one WebP per THUMB_WIDTHS and return the new
    ``cover_thumbs``. Raises on a failed download or an unreadable image."""
    src = record.cover_image_url
    resp = requests.get(src, headers={'User-Agent': USER_AGENT}, timeout=10)
    resp.raise_for_status()
    if len(resp.content) > MAX_SOURCE_BYTES:
        raise ValueError(f'cover too large ({len(resp.content)} bytes)')
    image = Image.open(io.BytesIO(resp.content))
    image = image.convert('RGB')

    # The source hash in the name: a new cover never reuses an old cached file.
    key = hashlib.sha1(src.encode()).hexdigest()[:10]
    thumbs = {'src': src}
    for width in THUMB_WIDTHS:
        copy = image.copy()
        # Never upscale: a 300px source stays 300px under both widths.
        copy.thumbnail((width, width * 2), Image.LANCZOS)
        buffer = io.BytesIO()
        copy.save(buffer, 'WEBP', quality=WEBP_QUALITY, method=6)
        name = default_storage.save(f'covers/{record.pk}-{key}-{width}.webp', ContentFile(buffer.getvalue()))
        thumbs[str(width)] = default_storage.url(name)
    return thumbs


def refresh_cover_thumbs(record):
    """Regenerate ``record``'s thumbnails if its cover changed. Never raises:
    a failure is logged and the storefront keeps using the original cover."""
    if not settings.COVER_THUMBNAILS or not thumbs_are_stale(record):
        return False
    try:
        thumbs = make_cover_thumbs(record)
    except Exception:
        logger.exception('Record %s: cover thumbnails failed for %s', record.pk, record.cover_image_url)
        return False
    # update(): no second post_save, no clobbering fields saved meanwhile.
    type(record).objects.filter(pk=record.pk).update(cover_thumbs=thumbs)
    record.cover_thumbs = thumbs
    return True
