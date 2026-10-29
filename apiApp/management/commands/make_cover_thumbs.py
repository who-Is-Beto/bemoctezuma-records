"""Generate the WebP cover thumbnails of every record that lacks them (or
whose cover changed since). New and edited records get theirs on save; this
backfills the rest:

    python manage.py make_cover_thumbs            # all stale records
    python manage.py make_cover_thumbs --limit 50

Writes to the configured storage (R2 in prod) and to Record.cover_thumbs.
Safe to re-run: records with current thumbnails are skipped.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apiApp.models import Record
from apiApp.services import refresh_cover_thumbs, thumbs_are_stale


class Command(BaseCommand):
    help = "Generate missing/stale WebP cover thumbnails."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help='process at most N records')

    def handle(self, *args, limit=None, **options):
        if not settings.COVER_THUMBNAILS:
            raise CommandError('COVER_THUMBNAILS is off (no STORAGE_BUCKET): thumbnails need cloud storage.')
        records = [r for r in Record.objects.exclude(cover_image_url='').exclude(cover_image_url=None).order_by('id')
                   if thumbs_are_stale(r)][:limit]
        done = failed = 0
        for record in records:
            if refresh_cover_thumbs(record):
                done += 1
            else:
                failed += 1
                self.stderr.write(f'  ✗ #{record.pk} {record.title}')
        self.stdout.write(self.style.SUCCESS(f'{done} generated, {failed} failed, of {len(records)} stale.'))
