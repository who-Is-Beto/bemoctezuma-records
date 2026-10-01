"""Stock per owner: the one place sales take records out of inventory."""
from ..models import Record


def take_stock(record_id, quantity, owner_id=None):
    """Take ``quantity`` units of a record out of stock. Call inside ``transaction.atomic()``.

    With ``owner_id`` (Punto de venta) every unit comes from that owner.
    Without it (online orders) units come from the owner added first (FIFO);
    what owners can't cover is store stock (owner ``None``).

    Locks the record row, so two sales of the last copy never both succeed.
    Returns ``[(owner_id, quantity), ...]``, or ``None`` when there isn't
    enough stock (nothing is changed then).
    """
    record = Record.objects.select_for_update().filter(pk=record_id).first()
    if record is None or record.stock < quantity:
        return None
    rows = record.owner_stock.filter(quantity__gt=0).order_by('created_at', 'id')
    if owner_id is not None:
        rows = rows.filter(owner_id=owner_id)
        if sum(row.quantity for row in rows) < quantity:
            return None

    splits, remaining = [], quantity
    for row in rows:
        taken = min(row.quantity, remaining)
        row.quantity -= taken
        row.save(update_fields=['quantity'])
        splits.append((row.owner_id, taken))
        remaining -= taken
        if not remaining:
            break
    if remaining:
        splits.append((None, remaining))

    Record.objects.filter(pk=record_id).update(stock=record.stock - quantity)
    return splits
