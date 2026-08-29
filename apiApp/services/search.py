import operator
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