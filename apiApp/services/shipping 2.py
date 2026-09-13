import logging
import re
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


# ── Envíos Perros (shipping quotes) ─────────────────────────────────────
#
# API docs: https://enviosperrosv3.docs.apiary.io
# POST /rates → [{title, total, currency, courier, serviceType, ...}]

# Packaging defaults. Per-unit weights (grams) were provided by the store:
# LP 300g, 7" 100g, CD 85g — plus a fixed box tare.
PACKAGE_TARE_GRAMS = 300
DEFAULT_UNIT_WEIGHT_GRAMS = 300   # LP / 10" / 12" / boxsets / unknown
CD_UNIT_WEIGHT_GRAMS = 85
SEVEN_INCH_UNIT_WEIGHT_GRAMS = 100

# Vinyl-sized box; height grows with the number of units shipped.
BOX_DEPTH_CM = 33
BOX_WIDTH_CM = 33
BOX_HEIGHT_PER_UNIT_CM = 2
BOX_MAX_HEIGHT_CM = 30

# Upstream requirement: POST /rates rejects packages under 1 kg ("El campo
# peso del paquete debe ser de al menos 1"), so light carts must be padded.
MIN_PACKAGE_WEIGHT_KG = 1.0

# The store always ships home deliveries with the cheapest Estafeta option.
PREFERRED_COURIER = 'Estafeta'


class ShippingQuoteError(Exception):
    """Raised when the Envíos Perros API cannot produce a usable quote."""

    def __init__(self, message, code="shipping_quote_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def normalize_zip_code(value):
    """Return a 5-digit ZIP string (leading zero preserved) or None if invalid.

    ZIPs must be treated as strings end-to-end: JSON numbers and Excel-style
    inputs silently drop the leading zero ('04460' -> 4460).
    """
    digits = re.sub(r'\D', '', str(value or ''))
    if not digits or len(digits) > 5:
        return None
    return digits.zfill(5)


def record_unit_weight_grams(record):
    """Effective per-unit weight: explicit weight_grams wins, else category default."""
    if record.weight_grams:
        return record.weight_grams
    slug = (record.category.slug if record.category else '') or ''
    if 'cd' in slug:
        return CD_UNIT_WEIGHT_GRAMS
    if slug == '7':
        return SEVEN_INCH_UNIT_WEIGHT_GRAMS
    return DEFAULT_UNIT_WEIGHT_GRAMS


def build_package_from_cart(cart):
    """Build the Envíos Perros `package` object from the cart contents."""
    total_units = 0
    total_grams = PACKAGE_TARE_GRAMS
    for item in cart.cart_items.select_related('record', 'record__category'):
        units = item.quantity * (item.record.items_inside or 1)
        total_units += units
        total_grams += record_unit_weight_grams(item.record) * units
    return {
        'type': 'Box',
        'depth': BOX_DEPTH_CM,
        'width': BOX_WIDTH_CM,
        'height': min(BOX_HEIGHT_PER_UNIT_CM * max(total_units, 1), BOX_MAX_HEIGHT_CM),
        'weight': max(round(total_grams / 1000, 2), MIN_PACKAGE_WEIGHT_KG),
    }


def _normalize_quote(raw):
    """Map an Envíos Perros quote to our canonical flat shape.

    The API has two response variants in the wild:
    - staging / API blueprint: flat
      {"title", "total", "currency", "courier", "serviceType", "deliveryCommitment"}
    - prod: nested, with availability flag and null details for skipped couriers
      {"summary", "available", "comment",
       "details": {"courier", "service", "total", "currency", ...} | None}

    Returns a dict with keys title/total/currency/courier/serviceType/
    deliveryCommitment (total as Decimal), or None when the quote is
    unavailable or unusable.
    """
    if not isinstance(raw, dict):
        return None
    details = raw.get('details') if isinstance(raw.get('details'), dict) else {}
    available = raw.get('available')
    if available is False:
        return None
    courier = details.get('courier') or raw.get('courier')
    total = details.get('total', raw.get('total'))
    if not courier or total is None:
        return None
    service = details.get('service') or raw.get('serviceType') or ''
    title = raw.get('summary') or raw.get('title')
    if not title:
        title = f"{courier} - {service}" if service else courier
    try:
        total = Decimal(str(total)).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None
    return {
        'title': title,
        'total': total,
        'currency': details.get('currency') or raw.get('currency') or 'MXN',
        'courier': courier,
        'serviceType': service,
        'deliveryCommitment': details.get('deliveryCommitment') or raw.get('deliveryCommitment'),
    }


def get_zip_locations(zip_code):
    """Fetch the Sepomex colonias for a ZIP code from Envíos Perros.

    One ZIP can cover several colonias (e.g. 64000 → 'Monterrey Centro' and
    'La Finca'), and label generation later requires the EXACT Sepomex
    neighborhood name, so the checkout form must offer these as options
    instead of free text. Returns a list of dicts with zipCode/neighborhood/
    city/state. Results are cached (the API docs recommend it).

    Raises ShippingQuoteError on upstream failure; an unknown ZIP simply
    returns [].
    """
    if not settings.ENVIOS_PERROS_TOKEN:
        raise ShippingQuoteError(
            "El servicio de envíos no está configurado.",
            code="shipping_not_configured",
        )
    cache_key = f"ep_locations_{zip_code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{settings.ENVIOS_PERROS_API_URL}/locations"
    headers = {
        'Authorization': f"Bearer {settings.ENVIOS_PERROS_TOKEN}",
        'Accept': 'application/json',
    }
    try:
        resp = requests.get(
            url,
            params={'zipCode': zip_code},
            headers=headers,
            timeout=settings.ENVIOS_PERROS_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("Envíos Perros locations request failed: %s", exc)
        raise ShippingQuoteError("No se pudo contactar al servicio de envíos.") from exc

    if resp.status_code == 401:
        raise ShippingQuoteError(
            "El servicio de envíos rechazó las credenciales.",
            code="shipping_auth_error",
        )
    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Envíos Perros locations returned %s: %s", resp.status_code, exc)
        raise ShippingQuoteError("El servicio de envíos no está disponible.") from exc

    try:
        locations = resp.json()
    except ValueError as exc:
        raise ShippingQuoteError("Respuesta inválida del servicio de envíos.") from exc
    if not isinstance(locations, list):
        raise ShippingQuoteError("Respuesta inválida del servicio de envíos.")

    normalized = [
        {
            'zipCode': str(loc.get('zipCode', zip_code)),
            'neighborhood': str(loc.get('neighborhood', '')),
            'city': str(loc.get('city', '')),
            'state': str(loc.get('state', '')),
        }
        for loc in locations
        if isinstance(loc, dict) and loc.get('neighborhood')
    ]
    cache.set(cache_key, normalized, 60 * 60 * 24)  # Sepomex data rarely changes
    return normalized


def get_shipping_quotes(destination_zip_code, package):
    """Fetch quotes from Envíos Perros. Returns normalized quote dicts
    (see _normalize_quote); unavailable couriers are dropped.

    Raises ShippingQuoteError on any upstream failure (network, timeout,
    non-200). Never lets the raw exception escape into a 500.
    """
    if not settings.ENVIOS_PERROS_TOKEN:
        raise ShippingQuoteError(
            "El servicio de envíos no está configurado.",
            code="shipping_not_configured",
        )
    url = f"{settings.ENVIOS_PERROS_API_URL}/rates"
    payload = {
        'package': package,
        'originZipCode': settings.ORIGIN_ZIP_CODE,
        'destinationZipCode': destination_zip_code,
    }
    headers = {
        'Authorization': f"Bearer {settings.ENVIOS_PERROS_TOKEN}",
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=settings.ENVIOS_PERROS_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Envíos Perros rates request failed: %s", exc)
        raise ShippingQuoteError("No se pudo contactar al servicio de envíos.") from exc

    if resp.status_code == 422:
        try:
            message = resp.json().get('message') or 'Cotización inválida.'
        except ValueError:
            message = 'Cotización inválida.'
        raise ShippingQuoteError(message, code="shipping_invalid_request")
    if resp.status_code == 401:
        raise ShippingQuoteError(
            "El servicio de envíos rechazó las credenciales.",
            code="shipping_auth_error",
        )
    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Envíos Perros rates returned %s: %s", resp.status_code, exc)
        raise ShippingQuoteError("El servicio de envíos no está disponible.") from exc

    try:
        quotes = resp.json()
    except ValueError as exc:
        raise ShippingQuoteError("Respuesta inválida del servicio de envíos.") from exc
    if not isinstance(quotes, list):
        raise ShippingQuoteError("Respuesta inválida del servicio de envíos.")
    return [q for q in (_normalize_quote(item) for item in quotes) if q is not None]


def select_cheapest_quote(quotes, courier=PREFERRED_COURIER):
    """Pick the cheapest quote for the given courier (Decimal-safe).

    Returns None when the courier has no available option for this route.
    """
    candidates = [q for q in quotes if q.get('courier') == courier]
    if not candidates:
        return None
    return min(candidates, key=lambda q: Decimal(str(q.get('total', 0))))