from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Avg
from .models import Review, RecordRatingSummary

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
    record = instance.record
    reviews = record.reviews.all()
    total_reviews = reviews.count()

    average_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0.0

    rating_summary, _ = RecordRatingSummary.objects.get_or_create(record=record)
    rating_summary.average_rating = average_rating
    rating_summary.total_reviews = total_reviews
    rating_summary.save()