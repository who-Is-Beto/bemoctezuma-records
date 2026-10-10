"""Formats (Category) and genres (Genere): admin create/edit/delete, and records
in several genres (filters and search must not return a record twice)."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models import ProtectedError
from rest_framework.test import APIClient

from apiApp.models import Category, Genere, Record
from apiApp.services import record_unit_weight_grams


def _client(username, *codenames, **extra):
    user = get_user_model().objects.create_user(
        username=username, email=f'{username}@example.com', password='Pass12345!', **extra
    )
    if codenames:
        role = Group.objects.create(name=username)
        role.permissions.set(Permission.objects.filter(content_type__app_label='apiApp', codename__in=codenames))
        user.groups.add(role)
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def admin(db):
    return _client('catadmin', role='ADMIN')


def _record(title, category=None, generes=()):
    record = Record.objects.create(title=title, price=Decimal('300.00'), stock=1, category=category)
    record.generes.set(generes)
    return record


# ── Genres ───────────────────────────────────────────────────────────────


def test_genere_create_rename_keeps_slug_and_rejects_duplicates(admin):
    resp = admin.post('/generes/create/', {'name': '  Rock Alternativo ', 'description': 'Indie y más'}, format='json')
    assert resp.status_code == 201
    genere = Genere.objects.get(pk=resp.json()['id'])
    assert (genere.name, genere.slug) == ('Rock Alternativo', 'rock-alternativo')

    # Same name in another case is the same genre (two "Rock" chips otherwise).
    dup = admin.post('/generes/create/', {'name': 'ROCK ALTERNATIVO'}, format='json')
    assert dup.status_code == 400 and 'Ya existe' in str(dup.json())
    assert admin.post('/generes/create/', {'name': '   '}, format='json').status_code == 400

    # Renaming keeps the slug: shared /catalogo?genere= links keep working.
    resp = admin.patch(f'/generes/{genere.id}/update/', {'name': 'Alt Rock', 'slug': 'hack'}, format='json')
    assert resp.status_code == 200
    genere.refresh_from_db()
    assert (genere.name, genere.slug) == ('Alt Rock', 'rock-alternativo')
    # Saving its own name again (another field edited) isn't a duplicate.
    assert admin.patch(f'/generes/{genere.id}/update/', {'name': 'Alt Rock', 'description': ''}, format='json').status_code == 200
    assert admin.patch('/generes/999999/update/', {'name': 'X'}, format='json').status_code == 404


def test_genere_delete_only_takes_it_off_its_records(admin):
    rock, pop = Genere.objects.create(name='Rock'), Genere.objects.create(name='Pop')
    both = _record('Metrópolis', generes=[rock, pop])
    only_rock = _record('Dark Side', generes=[rock])

    listed = {g['name']: g['records_count'] for g in admin.get('/generes/').json()}
    assert listed == {'Pop': 1, 'Rock': 2}

    resp = admin.delete(f'/generes/{rock.id}/delete/')
    assert resp.status_code == 200 and resp.json() == {'deleted': rock.id, 'records_count': 2}
    # Records survive: one keeps its other genre, the other is left without one.
    assert [g.name for g in both.generes.all()] == ['Pop']
    assert list(only_rock.generes.all()) == []
    assert Record.objects.count() == 2


# ── Formats ──────────────────────────────────────────────────────────────


def test_format_rename_keeps_slug_so_shipping_weights_hold(admin):
    resp = admin.post('/categories/create/', {'name': 'CD'}, format='json')
    assert resp.status_code == 201
    cd = Category.objects.get(pk=resp.json()['id'])
    record = _record('Metrópolis', category=cd)
    assert record_unit_weight_grams(record) == 85

    assert admin.patch(f'/categories/{cd.id}/update/', {'name': 'Disco Compacto'}, format='json').status_code == 200
    record.refresh_from_db()
    # Slug 'cd' drives the 85 g shipping weight and /catalogo?category=cd.
    assert (record.category.name, record.category.slug) == ('Disco Compacto', 'cd')
    assert record_unit_weight_grams(record) == 85
    assert admin.post('/categories/create/', {'name': 'disco compacto'}, format='json').status_code == 400


def test_format_delete_moves_records_or_refuses(admin):
    lp, cd = Category.objects.create(name='LP'), Category.objects.create(name='CD')
    kept = [_record('Dark Side', category=lp), _record('The Wall', category=lp)]

    blocked = admin.delete(f'/categories/{lp.id}/delete/')
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'category_in_use'
    assert '2 disco(s)' in blocked.json()['error']['message']
    for bad_target in (lp.id, 999999, 'abc'):
        resp = admin.delete(f'/categories/{lp.id}/delete/', {'reassign_to': bad_target}, format='json')
        assert resp.status_code == 400 and resp.json()['error']['code'] == 'invalid_reassign_target'
    assert Category.objects.filter(pk=lp.id).exists()

    resp = admin.delete(f'/categories/{lp.id}/delete/', {'reassign_to': cd.id}, format='json')
    assert resp.status_code == 200
    assert (resp.json()['reassigned'], resp.json()['reassigned_to']['slug']) == (2, 'cd')
    assert {r.category_id for r in Record.objects.filter(pk__in=[r.pk for r in kept])} == {cd.id}
    assert not Category.objects.filter(pk=lp.id).exists()

    # Unused formats just go.
    assert admin.delete(f'/categories/{cd.id}/delete/').status_code == 409  # now has the 2 records
    empty = Category.objects.create(name='Casete')
    assert admin.delete(f'/categories/{empty.id}/delete/').json() == {
        'deleted': empty.id, 'reassigned': 0, 'reassigned_to': None,
    }


def test_deleting_a_used_format_directly_never_deletes_records(db):
    """Record.category was CASCADE: a delete from Django admin wiped its records."""
    lp = Category.objects.create(name='LP')
    _record('Dark Side', category=lp)
    with pytest.raises(ProtectedError):
        lp.delete()
    assert Record.objects.count() == 1


# ── Permissions ──────────────────────────────────────────────────────────


def test_catalog_endpoints_need_permission(db, api_client):
    genere, category = Genere.objects.create(name='Rock'), Category.objects.create(name='LP')
    calls = [
        ('post', '/generes/create/', {'name': 'Jazz'}),
        ('patch', f'/generes/{genere.id}/update/', {'name': 'Roll'}),
        ('delete', f'/generes/{genere.id}/delete/', None),
        ('post', '/categories/create/', {'name': '10"'}),
        ('patch', f'/categories/{category.id}/update/', {'name': 'Vinilo'}),
        ('delete', f'/categories/{category.id}/delete/', None),
    ]
    customer = _client('catcustomer')
    for method, url, body in calls:
        assert getattr(api_client, method)(url, body, format='json').status_code == 401
        assert getattr(customer, method)(url, body, format='json').status_code == 403

    # A role with the tab but only genre editing can't touch formats.
    editor = _client('cateditor', 'access_admin_panel', 'tab_add_record', 'change_genere')
    assert editor.patch(f'/generes/{genere.id}/update/', {'name': 'Roll'}, format='json').status_code == 200
    assert editor.post('/generes/create/', {'name': 'Jazz'}, format='json').status_code == 403
    assert editor.patch(f'/categories/{category.id}/update/', {'name': 'Vinilo'}, format='json').status_code == 403
    # Listing stays public: the catalog filters use it.
    assert api_client.get('/generes/').status_code == 200 and api_client.get('/categories/').status_code == 200


def test_roles_offer_format_and_genre_actions_under_agregar_disco(admin):
    tabs = admin.get('/roles/catalog/').json()['tabs']
    tab = next(t for t in tabs if t['id'] == 'add-record')
    assert {a['codename'] for a in tab['actions']} >= {
        'add_category', 'change_category', 'delete_category', 'add_genere', 'change_genere', 'delete_genere',
    }
    assert 'manage-catalog' not in {t['id'] for t in tabs}  # no separate tab


# ── Records in several genres ────────────────────────────────────────────


def test_record_create_and_edit_with_several_genres(admin, api_client):
    rock, pop, indie = (Genere.objects.create(name=n) for n in ('Rock', 'Pop', 'Indie'))
    resp = admin.post('/records/create/', {
        'title': 'Metrópolis', 'price': '300.00', 'stock': 1, 'featured': True, 'generes': [rock.id, pop.id],
    }, format='json')
    assert resp.status_code == 201
    record = Record.objects.get(pk=resp.json()['id'])
    assert sorted(g['name'] for g in resp.json()['generes']) == ['Pop', 'Rock']

    public = api_client.get(f'/records/{record.slug}/').json()
    assert sorted(g['slug'] for g in public['generes']) == ['pop', 'rock']
    assert 'genere' not in public

    # Other edits leave the genres alone; sending them replaces the set.
    admin.patch(f'/records/{record.id}/update/', {'stock': 3}, format='json')
    assert record.generes.count() == 2
    admin.patch(f'/records/{record.id}/update/', {'generes': [indie.id]}, format='json')
    assert [g.name for g in record.generes.all()] == ['Indie']
    admin.patch(f'/records/{record.id}/update/', {'generes': []}, format='json')
    assert record.generes.count() == 0
    bad = admin.patch(f'/records/{record.id}/update/', {'generes': [999999]}, format='json')
    assert bad.status_code == 400


def test_filter_and_search_return_a_multi_genre_record_once(db, api_client):
    rock, alt = Genere.objects.create(name='Rock'), Genere.objects.create(name='Rock Alternativo')
    lp = Category.objects.create(name='LP')
    both = Record.objects.create(title='OK Computer', price=Decimal('900'), stock=1, featured=True, category=lp)
    both.generes.set([rock, alt])
    one = Record.objects.create(title='Kid A', price=Decimal('900'), stock=1, featured=True, category=lp)
    one.generes.set([rock])

    listed = api_client.get('/records/', {'genere': 'rock'}).json()
    assert sorted(r['id'] for r in listed['results']) == sorted([both.id, one.id])
    assert listed['count'] == 2
    assert [r['id'] for r in api_client.get('/records/', {'genere': 'rock-alternativo'}).json()['results']] == [both.id]

    # 'rock' matches both of OK Computer's genres: still one row, right count.
    found = api_client.get('/search/', {'query': 'rock'}).json()
    assert sorted(r['id'] for r in found['results']) == sorted([both.id, one.id]) and found['count'] == 2
    # Tokens across fields: genre + format.
    assert [r['id'] for r in api_client.get('/search/', {'query': 'alternativo lp'}).json()['results']] == [both.id]
