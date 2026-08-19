from django.db import migrations


def populate_images(apps, schema_editor):
    """Move existing cover_image_url into the images JSON array."""
    Record = apps.get_model("apiApp", "Record")
    for record in Record.objects.all():
        if record.cover_image_url and not record.images:
            record.images = [record.cover_image_url]
            record.save(update_fields=["images"])


def reverse_populate(apps, schema_editor):
    """If reversed, clear images (can't guarantee single URL)."""
    Record = apps.get_model("apiApp", "Record")
    Record.objects.update(images=[])


class Migration(migrations.Migration):

    dependencies = [
        ("apiApp", "0039_record_cost_price_record_images_record_sell_price"),
    ]

    operations = [
        migrations.RunPython(populate_images, reverse_populate),
    ]
