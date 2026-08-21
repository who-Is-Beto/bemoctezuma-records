from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from django.db.models import Avg
from .models import Record, Review, RecordRatingSummary

# Records currently being deleted. When a record goes away its reviews are
# cascade-deleted and each fires post_delete; recomputing the rating summary
# inside those signals would re-INSERT a summary row for a record that is
# mid-deletion (FK violation at commit / orphaned row). Skip in that case.
_records_being_deleted = set()


@receiver(pre_delete, sender=Record)
def _mark_record_deletion(sender, instance, **kwargs):
    _records_being_deleted.add(instance.pk)


@receiver(post_delete, sender=Record)
def _unmark_record_deletion(sender, instance, **kwargs):
    _records_being_deleted.discard(instance.pk)


@receiver(post_save, sender=Review)
def update_rating_on_save(sender, instance, created, **kwargs):
    record = instance.record
    reviews = record.reviews.all()
    total_reviews = reviews.count()

    average_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0.0

    rating_summary, _ = RecordRatingSummary.objects.get_or_create(record=record)
    rating_summary.average_rating = average_rating
    rating_summary.total_reviews = total_reviews
    rating_summary.save()

@receiver(post_delete, sender=Review)
def update_rating_on_delete(sender, instance, **kwargs):
    if instance.record_id in _records_being_deleted:
        return  # The record itself is being deleted; its summary dies with it.
    record = instance.record
    reviews = record.reviews.all()
    total_reviews = reviews.count()

    average_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0.0

    rating_summary, _ = RecordRatingSummary.objects.get_or_create(record=record)
    rating_summary.average_rating = average_rating
    rating_summary.total_reviews = total_reviews
    rating_summary.save()