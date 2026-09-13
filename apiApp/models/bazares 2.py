from django.db import models
from django.utils.text import slugify


class Bazar(models.Model):
    """A bazaar / flea-market event where Moctezuma Records sets up a stand.

    Shown publicly on /bazares (upcoming only, ordered by soonest date) and
    managed from the admin panel ("Manejo de bazares").
    """
    name = models.CharField(max_length=200)
    slug = models.SlugField(default="", blank=True, null=False)
    image = models.ImageField(upload_to='bazares/%Y/%m/', blank=True, null=True)
    date = models.DateField()
    schedule = models.CharField(max_length=200, blank=True, default="",
                                help_text="e.g. '10:00 am - 6:00 pm'")
    address = models.CharField(max_length=300)
    google_maps_url = models.URLField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.name} ({self.date})"

    def save(self, *args, **kwargs):
        if not self.slug:
            max_len = self._meta.get_field('slug').max_length
            base_slug = slugify(self.name)[:max_len].rstrip('-')
            unique_slug = base_slug
            counter = 1
            while Bazar.objects.filter(slug=unique_slug).exists():
                suffix = f"-{counter}"
                unique_slug = f"{base_slug[:max_len - len(suffix)]}{suffix}"
                counter += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)