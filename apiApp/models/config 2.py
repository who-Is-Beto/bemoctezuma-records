from django.db import models


class SiteConfig(models.Model):
    """Singleton site-wide configuration (only ever one row, pk=1).

    Holds the maintenance window state used by MaintenanceModeMiddleware and
    the admin /config/maintenance/ endpoint. ``save()`` forces pk=1 so there
    is no lightweight-solo dependency and no way to drift into two rows.
    """

    maintenance_mode = models.BooleanField(default=False)
    maintenance_message = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Configuración del sitio"
        verbose_name_plural = "Configuración del sitio"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "Mantenimiento: ON" if self.maintenance_mode else "Mantenimiento: OFF"