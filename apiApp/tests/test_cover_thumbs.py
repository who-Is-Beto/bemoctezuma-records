"""Cover thumbnails: WebP copies generated after a record's cover is saved,
served only while they match the current cover. Downloads are mocked."""
import io
from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import call_command
from PIL import Image

from apiApp.models import Record
from apiApp.serilizers import RecordListSerializer

COVER = 'https://i.discogs.com/cover.jpeg'


def _jpeg(size=(900, 900)):
    buffer = io.BytesIO()
    Image.new('RGB', size, (20, 80, 122)).save(buffer, 'JPEG')
    return mock.Mock(content=buffer.getvalue(), raise_for_status=mock.Mock())


@pytest.fixture
def thumbs_on(settings, tmp_path):
    settings.COVER_THUMBNAILS = True
    settings.MEDIA_ROOT = tmp_path
    settings.MEDIA_URL = 'https://media.example.com/'
    return tmp_path


def _record(cover=COVER):
    return Record.objects.create(title='Kind of Blue', price=Decimal('100.00'), stock=1, cover_image_url=cover)


@pytest.mark.django_db
def test_saving_a_cover_makes_webp_thumbnails(thumbs_on, django_capture_on_commit_callbacks):
    with mock.patch('apiApp.services.thumbnails.requests.get', return_value=_jpeg()) as get, \
            django_capture_on_commit_callbacks(execute=True):
        record = _record()
    get.assert_called_once()
    record.refresh_from_db()
    assert record.cover_thumbs['src'] == COVER
    for width in (320, 640):
        url = record.cover_thumbs[str(width)]
        assert url.startswith('https://media.example.com/covers/') and url.endswith(f'-{width}.webp')
        image = Image.open(thumbs_on / url.removeprefix('https://media.example.com/'))
        assert (image.format, image.width) == ('WEBP', width)
    assert RecordListSerializer(record).data['cover_thumbs'].keys() == {'320', '640'}


@pytest.mark.django_db
def test_small_covers_are_not_upscaled(thumbs_on, django_capture_on_commit_callbacks):
    with mock.patch('apiApp.services.thumbnails.requests.get', return_value=_jpeg((300, 300))), \
            django_capture_on_commit_callbacks(execute=True):
        record = _record()
    record.refresh_from_db()
    url = record.cover_thumbs['640'].removeprefix('https://media.example.com/')
    assert Image.open(thumbs_on / url).width == 300


@pytest.mark.django_db
def test_failed_download_keeps_the_record_and_original_cover(thumbs_on, django_capture_on_commit_callbacks, caplog):
    with mock.patch('apiApp.services.thumbnails.requests.get', side_effect=OSError('boom')), \
            django_capture_on_commit_callbacks(execute=True):
        record = _record()
    record.refresh_from_db()
    assert record.cover_thumbs == {}
    assert RecordListSerializer(record).data['cover_thumbs'] == {}
    assert 'cover thumbnails failed' in caplog.text


@pytest.mark.django_db
def test_thumbnails_of_a_previous_cover_are_not_served(db):
    record = _record()
    Record.objects.filter(pk=record.pk).update(cover_thumbs={'src': 'https://old.jpg', '320': 'https://x/old.webp'})
    record.refresh_from_db()
    assert RecordListSerializer(record).data['cover_thumbs'] == {}


@pytest.mark.django_db
def test_unchanged_cover_is_not_downloaded_again(thumbs_on, django_capture_on_commit_callbacks):
    record = _record()
    Record.objects.filter(pk=record.pk).update(cover_thumbs={'src': COVER, '320': 'https://x/a.webp'})
    record.refresh_from_db()
    with mock.patch('apiApp.services.thumbnails.requests.get') as get, \
            django_capture_on_commit_callbacks(execute=True):
        record.stock = 2
        record.save()
    get.assert_not_called()


@pytest.mark.django_db
def test_command_backfills_only_stale_records(thumbs_on):
    fresh = _record()
    Record.objects.filter(pk=fresh.pk).update(cover_thumbs={'src': COVER, '320': 'https://x/a.webp'})
    stale = _record('https://i.discogs.com/other.jpeg')
    Record.objects.create(title='No cover', price=Decimal('1.00'), stock=1)
    with mock.patch('apiApp.services.thumbnails.requests.get', return_value=_jpeg()) as get:
        call_command('make_cover_thumbs')
    assert get.call_count == 1
    stale.refresh_from_db()
    assert stale.cover_thumbs['src'] == 'https://i.discogs.com/other.jpeg'
