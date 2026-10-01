import difflib
import operator
import re
from decimal import Decimal, InvalidOperation
from functools import reduce

from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.utils.text import slugify

from ..models import Artist, Category, Genere, Record


def _normalized_search_term(term):
    """Normalize a search term the same way slugs are generated.

    'Zoé' -> 'zoe', 'T. Rex' -> 'trex' — lets 'zoe' match records by 'Zoé'
    and 'trex' match 'T. Rex' by comparing against hyphen-stripped slugs.
    """
    return slugify(term or '').replace('-', '')


def _slug_contains(model, term):
    """Queryset of `model` whose slug, with hyphens stripped, contains term."""
    return model.objects.annotate(
        _norm_slug=Replace('slug', Value('-'), Value('')),
    ).filter(_norm_slug__icontains=term)


def _query_tokens(query):
    """Split a raw query into normalized, comparable tokens.

    'Pink  Floyd!' -> ['pink', 'floyd']; 'Zoé' -> ['zoe'].
    """
    tokens = (_normalized_search_term(word) for word in query.split())
    return [tok for tok in tokens if tok]


def _record_token_q(token):
    """Q matching one token against any searchable field's normalized slug."""
    return (
        Q(id__in=_slug_contains(Record, token))
        | Q(artist__in=_slug_contains(Artist, token))
        | Q(genere__in=_slug_contains(Genere, token))
        | Q(category__in=_slug_contains(Category, token))
    )


def search_artists(query, limit=20):
    """Artists whose name matches every normalized token, in any order.

    Called by the autocomplete endpoint; returns an already-ordered queryset.
    """
    q = (query or '').strip()
    tokens = _query_tokens(q)
    if tokens:
        # Every word must match the artist's normalized slug, in any order.
        # Q objects (not queryset &) avoid duplicate-annotation collisions.
        return Artist.objects.filter(
            reduce(
                operator.and_,
                (Q(id__in=_slug_contains(Artist, t)) for t in tokens),
            )
        ).order_by('name')[:limit]
    return Artist.objects.filter(name__icontains=q).order_by('name')[:limit]


def search_records(query, *, category=None, available=None):
    """Records matching every normalized token across title/artist/genre/category.

    Empty/punctuation-only queries fall back to the legacy substring match.
    Optional ``category`` (slug) and ``available`` (bool: stock > 0) filters
    are applied just like the catalog list endpoint does.
    """
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

    if category:
        records = records.filter(category__slug=category)
    if available:
        records = records.filter(stock__gt=0)
    return records


# ?ordering= values accepted by the catalog endpoints; anything else = newest.
RECORD_ORDERINGS = {
    'newest': '-id',
    'price_asc': 'sell_price',
    'price_desc': '-sell_price',
}
_CONDITION_CODES = {code for code, _ in Record.CONDITIONS}


def _decimal_param(value):
    """'250' / '250.5' / '250,5' -> Decimal; blank or garbage -> None."""
    try:
        return Decimal(str(value).strip().replace(',', '.')) if value not in (None, '') else None
    except InvalidOperation:
        return None


def apply_record_filters(records, params):
    """Catalog filters shared by /records/ and /search/ (query params).

    genere / artist: slug. condition: comma list of CONDITIONS codes.
    price_min / price_max: inclusive, on the customer price (sell_price, kept in
    sync by Record.save()). ordering: newest | price_asc | price_desc.
    Invalid values are ignored rather than 400ing a shared/bookmarked URL.
    Also joins the FKs the list serializer nests (1 query instead of 3 per row).
    """
    genere = (params.get('genere') or '').strip()
    if genere:
        records = records.filter(genere__slug=genere)
    artist = (params.get('artist') or '').strip()
    if artist:
        records = records.filter(artist__slug=artist)
    conditions = [c for c in (params.get('condition') or '').split(',') if c in _CONDITION_CODES]
    if conditions:
        records = records.filter(condition__in=conditions)
    price_min = _decimal_param(params.get('price_min'))
    if price_min is not None:
        records = records.filter(sell_price__gte=price_min)
    price_max = _decimal_param(params.get('price_max'))
    if price_max is not None:
        records = records.filter(sell_price__lte=price_max)
    ordering = RECORD_ORDERINGS.get(params.get('ordering'), '-id')
    # '-id' tiebreak keeps pagination stable when prices repeat.
    return records.select_related('artist', 'category', 'genere').order_by(ordering, '-id')


def _norm_name(name):
    return slugify(name or '').replace('-', '')


def most_similar_artist(artist):
    """The other artist whose normalized name is closest to ``artist``'s, or None.

    Stdlib difflib over every artist: fine for a record store's artist count
    and works on SQLite (CI) as well as Postgres.
    """
    target = _norm_name(artist.name)
    best, best_score = None, 0.0
    for candidate in Artist.objects.exclude(pk=artist.pk).only('id', 'name', 'slug'):
        score = difflib.SequenceMatcher(None, target, _norm_name(candidate.name)).ratio()
        if score > best_score:
            best, best_score = candidate, score
    return best

def find_record_matches(title, artist_name='', limit=5):
    """Records that may be the same release as one about to be added.

    Titles compare ignoring case, accents and punctuation ('Aztlan' = 'Aztlán');
    one containing the other also counts ('Kid A' ~ 'Kid A (Remastered)').
    With an artist (Discogs' ' (2)' disambiguation ignored), only that artist's
    records, plus exact titles by others. Exact + same artist come first. The
    add form offers them so stock/owners go to the existing record instead.
    """
    norm_title = _normalized_search_term(title)
    if not norm_title:
        return []
    norm_artist = _normalized_search_term(re.sub(r'\s*\(\d+\)$', '', artist_name or ''))

    def similar(other):
        # Containment only from 4 chars on, so 'Th' doesn't match half the catalog.
        return min(len(norm_title), len(other)) >= 4 and (norm_title in other or other in norm_title)

    ranked = []
    # ponytail: scans every title in Python (slugs aren't reliable: hand-made,
    # truncated, never updated on rename). Fine for thousands of records.
    for record_id, record_title, record_artist in Record.objects.values_list('id', 'title', 'artist__name'):
        other = _normalized_search_term(record_title)
        exact = other == norm_title
        if not exact and not similar(other):
            continue
        same_artist = bool(norm_artist) and _normalized_search_term(record_artist or '') == norm_artist
        if norm_artist and not same_artist and not exact:
            continue
        ranked.append((not (exact and same_artist), not same_artist, not exact, record_id))

    ids = [record_id for *_, record_id in sorted(ranked)[:limit]]
    records = Record.objects.filter(id__in=ids).select_related('artist', 'category', 'genere').prefetch_related(
        'owner_stock__owner'
    ).in_bulk()
    return [records[record_id] for record_id in ids]
