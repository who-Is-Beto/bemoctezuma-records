"""Regression tests for the prod incident where POST /records/create/ 500'd.

Root cause: Record.slug is a SlugField without an explicit max_length, so the
column is varchar(50) (Django default). The title "La Nube En El Jardín En
Vivo Desde Sala Nezahualcóyotl" slugifies to 54 chars; the slug is not in
RecordCreateSerializer's fields so DRF never validates it, and Postgres raised
DataError -> {"code": "server_error"} 500. The collision check was also an
`if` instead of a `while`, so a third record with the same title would have
raised IntegrityError. Same pattern existed on Category/Artist/Genere.
"""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apiApp.models import Artist, Category, Genere, Record


LONG_TITLE = "La Nube En El JardÍn En Vivo Desde Sala NezahualcÓYotl"


@pytest.mark.django_db
def test_long_title_slug_fits_column():
    """The exact prod payload title must save without a DataError."""
    record = Record.objects.create(
        title=LONG_TITLE,
        price=Decimal("870.00"),
        cost_price=Decimal("680.00"),
        stock=2,
    )
    max_len = Record._meta.get_field("slug").max_length
    assert len(record.slug) <= max_len
    assert record.slug.startswith("la-nube-en-el-jardin")


@pytest.mark.django_db
def test_repeated_titles_get_unique_slugs():
    """Collision handling must survive more than one duplicate (while, not if)."""
    slugs = set()
    for _ in range(3):
        record = Record.objects.create(title=LONG_TITLE, price=Decimal("870"), stock=2)
        slugs.add(record.slug)
        assert len(record.slug) <= Record._meta.get_field("slug").max_length
    assert len(slugs) == 3


@pytest.mark.django_db
def test_explicit_slug_is_not_overwritten():
    """Auto-generation only applies when slug is empty (admin prepopulate)."""
    record = Record.objects.create(
        title=LONG_TITLE,
        slug="mi-slug-personalizado",
        price=Decimal("870"),
        stock=2,
    )
    assert record.slug == "mi-slug-personalizado"


@pytest.mark.django_db
def test_other_slugged_models_truncate_long_names():
    """Category/Artist/Genere had the same overflow + single-collision bugs."""
    artist = Artist.objects.create(name="Una Banda Con Un Nombre Verdaderamente Excesivamente Largo")
    category = Category.objects.create(name="Una Categoría Con Un Nombre Verdaderamente Excesivo")
    genere = Genere.objects.create(name="Un Género Con Un Nombre Verdaderamente Excesivo Largo")
    for obj in (artist, category, genere):
        max_len = obj._meta.get_field("slug").max_length
        assert len(obj.slug) <= max_len


@pytest.mark.django_db
def test_other_slugged_models_unique_on_repeats():
    names = "Banda Repetida Con Nombre Largo Suficiente Para Truncar El Slug"
    artist_slugs = {Artist.objects.create(name=names).slug for _ in range(3)}
    assert len(artist_slugs) == 3


@pytest.mark.django_db
def test_record_create_endpoint_accepts_long_title(api_client):
    """End-to-end: the exact prod payload shape must return 201, not 500."""
    User = get_user_model()
    admin = User.objects.create_user(
        username="admin_slug",
        email="admin.slug@example.com",
        password="StrongPass123!",
        role="ADMIN",
    )
    artist = Artist.objects.create(name="Peso Pluma")
    payload = {
        "title": LONG_TITLE,
        "artist": artist.id,
        "category": None,
        "condition": "M",
        "cost_price": 680,
        "cover_image_url": "https://i.discogs.com/example.jpeg",
        "description": "Contenido (lista de canciones)",
        "discount_porcentage": 0,
        "featured": True,
        "genere": None,
        "images": [],
        "items_inside": 2,
        "price": 870,
        "release_date": 2026,
        "stock": 2,
    }
    api_client.force_authenticate(user=admin)
    resp = api_client.post(reverse("records-create"), payload, format="json")
    assert resp.status_code == 201, resp.content
    assert len(resp.data["slug"]) <= Record._meta.get_field("slug").max_length
    assert resp.data["sell_price"] == "870.00"
