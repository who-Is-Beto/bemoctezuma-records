"""Copies of one release in different conditions: records grouped by ``variant_of``.

A group is its first record (``variant_of`` null) plus the records pointing at
it. Groups are one level deep: the serializers always link a new copy to the
group's first record, and ``promote_first_copy`` keeps it that way on delete.
"""
from collections import defaultdict

from django.db.models import Case, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce

from ..models import Record

# Best grade first (Mint ... Poor), the order the pickers list copies in.
CONDITION_RANK = {code: rank for rank, (code, _) in enumerate(Record.CONDITIONS)}


def group_id(record):
    return record.variant_of_id or record.id


def collapse_copies(records):
    """One row per group out of ``records`` (already filtered and ordered).

    The row shown is the group's copy in stock first, then the cheapest, then
    its first record. It's picked among ``records``, so a condition or price
    filter shows the copy that matched it.
    """
    records = records.annotate(_group=Coalesce('variant_of_id', 'id'))
    shown = (
        records.filter(_group=OuterRef('_group'))
        .order_by(
            Case(When(stock__gt=0, then=Value(0)), default=Value(1)),
            'sell_price',
            F('variant_of_id').asc(nulls_first=True),
            'id',
        )
        .values('id')[:1]
    )
    return records.filter(id=Subquery(shown))


def copies_by_group(records):
    """{group id: every copy of the group, best condition first} for the
    groups of ``records``, in one query."""
    ids = {group_id(record) for record in records}
    groups = defaultdict(list)
    for copy in Record.objects.filter(Q(id__in=ids) | Q(variant_of__in=ids)):
        groups[group_id(copy)].append(copy)
    for copies in groups.values():
        copies.sort(key=lambda c: (CONDITION_RANK.get(c.condition, len(CONDITION_RANK)), c.effective_price, c.id))
    return groups


def promote_first_copy(record):
    """Before ``record`` is deleted: its oldest copy becomes the group's first
    record, so the rest stay grouped instead of each showing up alone."""
    copies = list(record.variants.order_by('id').values_list('id', flat=True))
    if not copies:
        return
    Record.objects.filter(id__in=copies[1:]).update(variant_of=copies[0])
    Record.objects.filter(id=copies[0]).update(variant_of=None)
