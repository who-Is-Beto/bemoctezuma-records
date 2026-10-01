"""Deleting an artist from Agregar disco: usage count, similarity suggestion,
reassignment + delete in one transaction, permissions."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework.test import APIClient

from apiApp.models import Artist, Record


@pytest.fixture
def admin_client(db):
    admin = get_user_model().objects.create_user(
        username='artadmin', email='artadmin@example.com', password='Pass12345!', role='ADMIN',
    )
    client = APIClient()
    client.force_authenticate(admin)
    return client


@pytest.fixture
def artists(db):
    typo = Artist.objects.create(name='Pink Floid')
    right = Artist.objects.create(name='Pink Floyd')
    Artist.objects.create(name='Zoé')
    for title in ('Animals', 'Meddle'):
        Record.objects.create(title=title, price=Decimal('100'), stock=1, artist=typo)
    return typo, right


def test_usage_counts_records_and_suggests_similar(admin_client, artists):
    typo, right = artists
    resp = admin_client.get(reverse('artist-usage', args=[typo.id]))
    assert resp.status_code == 200
    assert resp.data['records_count'] == 2
    assert resp.data['suggestion']['id'] == right.id


def test_delete_in_use_without_target_is_409(admin_client, artists):
    typo, _ = artists
    resp = admin_client.delete(reverse('artist-delete', args=[typo.id]), {}, format='json')
    assert resp.status_code == 409
    assert Artist.objects.filter(pk=typo.id).exists()


def test_delete_reassigns_to_existing_artist(admin_client, artists):
    typo, right = artists
    resp = admin_client.delete(reverse('artist-delete', args=[typo.id]), {'reassign_to': right.id}, format='json')
    assert resp.status_code == 200
    assert resp.data['reassigned'] == 2
    assert not Artist.objects.filter(pk=typo.id).exists()
    assert Record.objects.filter(artist=right).count() == 2


def test_delete_reassigns_to_new_artist(admin_client, artists):
    typo, _ = artists
    resp = admin_client.delete(reverse('artist-delete', args=[typo.id]), {'new_artist_name': 'Pink Floyd (UK)'}, format='json')
    assert resp.status_code == 200
    new = Artist.objects.get(name='Pink Floyd (UK)')
    assert Record.objects.filter(artist=new).count() == 2


def test_invalid_target_rolls_back(admin_client, artists):
    typo, _ = artists
    for target in (999999, typo.id):
        resp = admin_client.delete(reverse('artist-delete', args=[typo.id]), {'reassign_to': target}, format='json')
        assert resp.status_code == 400
    assert Record.objects.filter(artist=typo).count() == 2


def test_unused_artist_deletes_directly(admin_client, db):
    lonely = Artist.objects.create(name='Nadie')
    assert admin_client.delete(reverse('artist-delete', args=[lonely.id])).status_code == 200
    assert not Artist.objects.filter(pk=lonely.id).exists()


def test_permissions(api_client, artists):
    typo, _ = artists
    user = get_user_model().objects.create_user(username='u', email='u@example.com', password='Pass12345!')
    client = APIClient()
    client.force_authenticate(user)
    assert client.get(reverse('artist-usage', args=[typo.id])).status_code == 403
    role = Group.objects.create(name='Catalogador')
    role.permissions.set(Permission.objects.filter(
        content_type__app_label='apiApp', codename__in=['access_admin_panel', 'tab_add_record', 'delete_artist'],
    ))
    user.groups.add(role)
    user = get_user_model().objects.get(pk=user.pk)  # drop the permission cache
    client.force_authenticate(user)
    assert client.get(reverse('artist-usage', args=[typo.id])).status_code == 200


def test_protect_blocks_raw_cascade(artists):
    typo, _ = artists
    with pytest.raises(ProtectedError):
        typo.delete()
