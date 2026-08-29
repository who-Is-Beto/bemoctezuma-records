from django.conf import settings
from django.db import models

from .catalog import Record


class Review(models.Model):

    RATING_CHOICES = [
        (1, "Pa' la basura"),
        (2, '2 - Mas o menos'),
        (3, '3 - Promedio'),
        (4, "4 - Ta' bueno"),
        (5, '5 - Maravilloso'),
    ]

    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveIntegerField(choices=RATING_CHOICES)
    review = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"Review of {self.record.title} by {self.user.username}"
    class Meta:
        unique_together = ('record', 'user')
        ordering = ['-created_at']

class RecordRatingSummary(models.Model):
    record = models.OneToOneField(Record, on_delete=models.CASCADE, related_name='rating_summary')
    average_rating = models.FloatField(default=0.0)
    total_reviews = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Rating Summary for {self.record.title}"