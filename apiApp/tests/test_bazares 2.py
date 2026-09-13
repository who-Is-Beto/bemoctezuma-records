"""Tests for every process related to Bazares (bazar-pickup feature).

Covered processes:
- Bazar model: slug auto-generation (collision loop + truncation).
- Public catalog: GET /bazares/ (upcoming only, soonest first, no auth).
- Admin CRUD: GET /bazares/all/, POST /bazares/create/,
  PATCH /bazares/<id>/update/, DELETE /bazares/<id>/delete/ (all admin-only),
  including the SET_NULL contract that keeps order history when a bazar dies.
- Checkout gate: create-checkout-session with shipped_to='bazar' validates
  bazar_id (missing/unknown/past) and travels to Stripe as metadata.
- Fulfillment: webhook-style fulfill_checkout resolves pickup_bazar from
  metadata and tolerates a bazar deleted after payment.
- Order API surface: OrderSerializer exposes nested pickup_bazar.
- Emails: order-created/notification context includes the pickup details and
  the templates render the "Recoges en bazar" block.
"""
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apiApp.models import Bazar, Cart, CartItem, Order, Record
from apiApp.services import _order_email_context


# ── Fixtures & helpers ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _gate_off(settings):
    """Bazar tests are not about the email-verification gate; disable it for
    determinism since the local .env may enable it."""
    settings.REQUIRE_EMAIL_VERIFICATION = False


@pytest.fixture(autouse=True)
def _locmem_emails(settings):
    """fulfill_checkout sends order emails; keep them out of any real SMTP."""
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    mail.outbox.clear()


def _bazar_payload(**overrides):
    payload = {
        'name': 'Bazar La Lagunilla',
        'date': str(timezone.localdate() + timedelta(days=7)),
        'schedule': '10:00 am - 6:00 pm',
        'address': 'Av. Oceanía 120, Col. Moctezuma, CDMX',
        'google_maps_url': 'https://maps.app.goo.gl/lagunilla',
    }
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}


def _make_bazar(name='Bazar La Lagunilla', days_ahead=7, **extra):
    data = {
        'date': timezone.localdate() + timedelta(days=days_ahead),
        'schedule': '10:00 am - 6:00 pm',
        'address': 'Av. Oceanía 120, Col. Moctezuma, CDMX',
        'google_maps_url': 'https://maps.app.goo.gl/lagunilla',
    }
    data.update(extra)
    return Bazar.objects.create(name=name, **data)


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(
        username='bazaruser', email='bazar@example.com', password='UserPass123!',
    )


@pytest.fixture
def admin(db):
    User = get_user_model()
    return User.objects.create_user(
        username='bazaradmin', email='bazaradmin@example.com',
        password='AdminPass123!', role='ADMIN',
    )


@pytest.fixture
def cart_with_record(db, user):
    cart = Cart.objects.create(user=user)
    record = Record.objects.create(
        title='Disco De Prueba', price=Decimal('500.00'), stock=5,
    )
    CartItem.objects.create(cart=cart, record=record, quantity=1)
    return cart


def _fake_stripe_session():
    return {
        'id': 'cs_test_bazar',
        'url': 'https://checkout.stripe.com/test',
        'client_reference_id': 'cart-code',
    }


# ── Model: slug generation ───────────────────────────────────────────────


@pytest.mark.django_db
class TestBazarModel:
    def test_slug_generated_from_name(self, db):
        bazar = _make_bazar(name='Bazar Del Centro')
        assert bazar.slug == 'bazar-del-centro'

    def test_slug_collision_gets_counter_suffix(self, db):
        first = _make_bazar(name='Bazar Repetido')
        second = _make_bazar(name='Bazar Repetido')
        third = _make_bazar(name='Bazar Repetido')
        assert {first.slug, second.slug, third.slug} == {
            'bazar-repetido', 'bazar-repetido-1', 'bazar-repetido-2',
        }

    def test_slug_truncated_to_field_max_length(self, db):
        # Name fits its own varchar(200) but slugifies far past the slug
        # column's default max_length (50).
        long_name = f"Bazar {'x' * 180}"
        assert len(long_name) <= 200
        bazar = _make_bazar(name=long_name)
        max_len = Bazar._meta.get_field('slug').max_length
        assert len(bazar.slug) <= max_len


# ── Public list: GET /bazares/ ───────────────────────────────────────────


@pytest.mark.django_db
class TestBazarPublicList:
    def test_public_no_auth_required(self, api_client, db):
        resp = api_client.get(reverse('bazar-list'))
        assert resp.status_code == 200

    def test_only_upcoming_soonest_first(self, api_client, db):
        past = _make_bazar(name='Bazar Pasado', days_ahead=-3)
        today = _make_bazar(name='Bazar Hoy', days_ahead=0)
        far = _make_bazar(name='Bazar Lejano', days_ahead=30)
        near = _make_bazar(name='Bazar Cercano', days_ahead=2)

        resp = api_client.get(reverse('bazar-list'))

        names = [b['name'] for b in resp.data]
        assert past.name not in names          # already happened → hidden
        assert names == [today.name, near.name, far.name]  # soonest first
        # Today still counts as upcoming (checkout allows pickups later today).
        assert today.name in names

    def test_serialized_fields(self, api_client, db):
        _make_bazar()
        resp = api_client.get(reverse('bazar-list'))
        bazar = resp.data[0]
        for field in ('id', 'name', 'slug', 'image_url', 'date',
                      'schedule', 'address', 'google_maps_url', 'created_at'):
            assert field in bazar
        assert bazar['image_url'] is None  # no image uploaded


# ── Admin CRUD ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBazarAdminCrud:
    def test_admin_list_requires_authentication(self, api_client, db):
        resp = api_client.get(reverse('admin-list-bazares'))
        assert resp.status_code == 401

    def test_admin_list_forbidden_for_customer(self, api_client, user):
        api_client.force_authenticate(user=user)
        resp = api_client.get(reverse('admin-list-bazares'))
        assert resp.status_code == 403
        assert resp.data['error']['code'] == 'forbidden'

    def test_admin_list_includes_past_newest_first(self, api_client, admin):
        old = _make_bazar(name='Viejo', days_ahead=-10)
        new_past = _make_bazar(name='Pasado Reciente', days_ahead=-1)
        future = _make_bazar(name='Futuro', days_ahead=5)

        api_client.force_authenticate(user=admin)
        resp = api_client.get(reverse('admin-list-bazares'))

        assert resp.status_code == 200
        names = [b['name'] for b in resp.data]
        assert names == [future.name, new_past.name, old.name]

    def test_create_as_admin_returns_201_with_slug(self, api_client, admin):
        api_client.force_authenticate(user=admin)
        resp = api_client.post(
            reverse('bazar-create'), _bazar_payload(), format='json'
        )
        assert resp.status_code == 201, resp.content
        assert resp.data['slug'] == 'bazar-la-lagunilla'
        assert Bazar.objects.filter(name='Bazar La Lagunilla').exists()

    def test_create_missing_required_fields_rejected(self, api_client, admin):
        api_client.force_authenticate(user=admin)
        resp = api_client.post(
            reverse('bazar-create'),
            _bazar_payload(address=None, google_maps_url=None),
            format='json',
        )
        assert resp.status_code == 400
        assert not Bazar.objects.exists()

    def test_create_forbidden_for_customer(self, api_client, user):
        api_client.force_authenticate(user=user)
        resp = api_client.post(
            reverse('bazar-create'), _bazar_payload(), format='json'
        )
        assert resp.status_code == 403
        assert not Bazar.objects.exists()

    def test_create_accepts_image_upload(self, api_client, admin, tmp_path, settings):
        """Multipart create with an image; MEDIA_ROOT redirected so the repo's
        media/ directory is never touched."""
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        settings.MEDIA_ROOT = tmp_path
        buffer = BytesIO()
        Image.new('RGB', (4, 4), color=(240, 140, 60)).save(buffer, format='PNG')
        image = SimpleUploadedFile('flyer.png', buffer.getvalue(), content_type='image/png')

        api_client.force_authenticate(user=admin)
        payload = _bazar_payload()
        resp = api_client.post(
            reverse('bazar-create'),
            {**payload, 'image': image},
            format='multipart',
        )
        assert resp.status_code == 201, resp.content
        created = Bazar.objects.get(name=payload['name'])
        assert created.image.name.startswith('bazares/')
        assert resp.data['image_url'] is not None

    def test_update_partial_as_admin(self, api_client, admin):
        bazar = _make_bazar()
        api_client.force_authenticate(user=admin)
        resp = api_client.patch(
            reverse('admin-update-bazar', args=[bazar.id]),
            {'schedule': '11:00 am - 5:00 pm'},
            format='json',
        )
        assert resp.status_code == 200, resp.content
        assert resp.data['schedule'] == '11:00 am - 5:00 pm'
        bazar.refresh_from_db()
        assert bazar.schedule == '11:00 am - 5:00 pm'
        assert bazar.address == 'Av. Oceanía 120, Col. Moctezuma, CDMX'  # untouched

    def test_update_allows_backdating_past_dates(self, api_client, admin):
        """Admins may backfill bazares that already happened (documented
        serializer decision); only checkout rejects past dates."""
        bazar = _make_bazar(days_ahead=5)
        api_client.force_authenticate(user=admin)
        yesterday = str(timezone.localdate() - timedelta(days=1))
        resp = api_client.patch(
            reverse('admin-update-bazar', args=[bazar.id]),
            {'date': yesterday},
            format='json',
        )
        assert resp.status_code == 200
        bazar.refresh_from_db()
        assert str(bazar.date) == yesterday

    def test_update_404_unknown(self, api_client, admin):
        api_client.force_authenticate(user=admin)
        resp = api_client.patch(
            reverse('admin-update-bazar', args=[99999]),
            {'schedule': 'x'}, format='json',
        )
        assert resp.status_code == 404
        assert resp.data['error']['code'] == 'bazar_not_found'

    def test_update_forbidden_for_customer(self, api_client, user):
        bazar = _make_bazar()
        api_client.force_authenticate(user=user)
        resp = api_client.patch(
            reverse('admin-update-bazar', args=[bazar.id]),
            {'schedule': 'x'}, format='json',
        )
        assert resp.status_code == 403

    def test_delete_as_admin_removes_bazar(self, api_client, admin):
        bazar = _make_bazar()
        api_client.force_authenticate(user=admin)
        resp = api_client.delete(reverse('admin-delete-bazar', args=[bazar.id]))
        assert resp.status_code == 200
        assert not Bazar.objects.filter(pk=bazar.id).exists()

    def test_delete_404_unknown(self, api_client, admin):
        api_client.force_authenticate(user=admin)
        resp = api_client.delete(reverse('admin-delete-bazar', args=[99999]))
        assert resp.status_code == 404
        assert resp.data['error']['code'] == 'bazar_not_found'

    def test_delete_forbidden_for_customer(self, api_client, user):
        bazar = _make_bazar()
        api_client.force_authenticate(user=user)
        resp = api_client.delete(reverse('admin-delete-bazar', args=[bazar.id]))
        assert resp.status_code == 403
        assert Bazar.objects.filter(pk=bazar.id).exists()

    def test_delete_keeps_order_history_pickup_set_null(self, api_client, admin):
        """SET_NULL contract: deleting a bazar never destroys orders."""
        bazar = _make_bazar()
        order = Order.objects.create(
            stripe_checkout_session_id='cs_test_deleted_bazar',
            amount=Decimal('500.00'),
            currency='mxn',
            user_email='buyer@example.com',
            shipped_to='bazar',
            status='paid',
            pickup_bazar=bazar,
        )
        api_client.force_authenticate(user=admin)
        resp = api_client.delete(reverse('admin-delete-bazar', args=[bazar.id]))
        assert resp.status_code == 200
        order.refresh_from_db()
        assert order.pickup_bazar is None
        assert order.shipped_to == 'bazar'  # history intact


# ── Checkout with shipped_to='bazar' ─────────────────────────────────────


@pytest.mark.django_db
class TestCheckoutBazarGate:
    def _post_checkout(self, api_client, cart, bazar_id=None, omit_id=False):
        body = {'cart_code': cart.cart_code, 'shipped_to': 'bazar'}
        if not omit_id:
            body['bazar_id'] = bazar_id if bazar_id is not None else ''
        return api_client.post(reverse('create-checkout-session'), body, format='json')

    def test_missing_bazar_id_rejected(self, api_client, user, cart_with_record):
        api_client.force_authenticate(user=user)
        with mock.patch('stripe.checkout.Session.create') as stripe_create:
            resp = self._post_checkout(api_client, cart_with_record, omit_id=True)
        assert resp.status_code == 400
        assert resp.data['error']['code'] == 'missing_bazar'
        stripe_create.assert_not_called()

    def test_unknown_bazar_id_rejected(self, api_client, user, cart_with_record):
        api_client.force_authenticate(user=user)
        with mock.patch('stripe.checkout.Session.create') as stripe_create:
            resp = self._post_checkout(api_client, cart_with_record, bazar_id=99999)
        assert resp.status_code == 400
        assert resp.data['error']['code'] == 'invalid_bazar'
        stripe_create.assert_not_called()

    def test_non_numeric_bazar_id_rejected(self, api_client, user, cart_with_record):
        api_client.force_authenticate(user=user)
        with mock.patch('stripe.checkout.Session.create') as stripe_create:
            resp = self._post_checkout(api_client, cart_with_record, bazar_id='not-a-number')
        assert resp.status_code == 400
        assert resp.data['error']['code'] == 'invalid_bazar'
        stripe_create.assert_not_called()

    def test_past_bazar_rejected(self, api_client, user, cart_with_record):
        past = _make_bazar(name='Bazar Ayer', days_ahead=-1)
        api_client.force_authenticate(user=user)
        with mock.patch('stripe.checkout.Session.create') as stripe_create:
            resp = self._post_checkout(api_client, cart_with_record, bazar_id=past.id)
        assert resp.status_code == 400
        assert resp.data['error']['code'] == 'bazar_in_past'
        stripe_create.assert_not_called()

    def test_today_bazar_allowed(self, api_client, user, cart_with_record):
        """Same-day pickup is valid: only strictly past dates are blocked."""
        today = _make_bazar(name='Bazar Hoy', days_ahead=0)
        api_client.force_authenticate(user=user)
        fake_session = _fake_stripe_session()
        with mock.patch('apiApp.views.get_shipping_quotes') as quote_mock, \
             mock.patch('stripe.checkout.Session.create', return_value=fake_session) as stripe_create:
            resp = self._post_checkout(api_client, cart_with_record, bazar_id=today.id)
        assert resp.status_code == 200, resp.content
        assert resp.data['session_id'] == 'cs_test_bazar'
        kwargs = stripe_create.call_args.kwargs
        assert kwargs['metadata']['shipped_to'] == 'bazar'
        assert kwargs['metadata']['bazar_id'] == str(today.id)
        quote_mock.assert_not_called()

    def test_future_bazar_travels_without_shipping_line_item(self, api_client, user, cart_with_record):
        bazar = _make_bazar(days_ahead=12)
        api_client.force_authenticate(user=user)
        fake_session = _fake_stripe_session()
        with mock.patch('apiApp.views.get_shipping_quotes') as quote_mock, \
             mock.patch('stripe.checkout.Session.create', return_value=fake_session) as stripe_create:
            resp = self._post_checkout(api_client, cart_with_record, bazar_id=bazar.id)
        assert resp.status_code == 200, resp.content
        kwargs = stripe_create.call_args.kwargs
        # No shipping cost/quote for bazar pickup — the customer pays items only.
        assert 'shipping_cost' not in kwargs['metadata']
        assert all(
            'Envío' not in li['price_data']['product_data']['name']
            for li in kwargs['line_items']
        )
        quote_mock.assert_not_called()

    def test_empty_cart_still_rejected_after_valid_bazar(self, api_client, user):
        empty_cart = Cart.objects.create(user=user)
        bazar = _make_bazar()
        api_client.force_authenticate(user=user)
        resp = self._post_checkout(api_client, empty_cart, bazar_id=bazar.id)
        assert resp.status_code == 400
        assert resp.data['error']['code'] == 'cart_empty'


# ── Fulfillment (webhook / fallback path) ────────────────────────────────


@pytest.mark.django_db
class TestBazarFulfillment:
    def _fulfill(self, cart, bazar_id=None, session_id='cs_test_fulfill_bazar'):
        from apiApp.views import fulfill_checkout

        metadata = {
            'cart_code': cart.cart_code,
            'shipped_to': 'bazar',
        }
        if bazar_id is not None:
            metadata['bazar_id'] = str(bazar_id)
        session = {
            'id': session_id,
            'amount_total': 50000,  # 500.00 mxn — products only, no shipping line
            'currency': 'mxn',
            'customer_email': 'buyer@example.com',
            'metadata': metadata,
        }
        # The Stripe line-item lookup must stay offline in tests; an exception
        # there makes fulfillment fall back to the cart snapshot (same behavior
        # as a network hiccup in prod).
        with mock.patch(
            'stripe.checkout.Session.list_line_items',
            side_effect=RuntimeError('offline tests'),
        ):
            fulfill_checkout(session, cart.cart_code)
        return Order.objects.get(stripe_checkout_session_id=session_id)

    def test_order_created_with_pickup_bazar(self, cart_with_record):
        bazar = _make_bazar()
        order = self._fulfill(cart_with_record, bazar_id=bazar.id)
        assert order.shipped_to == 'bazar'
        assert order.pickup_bazar_id == bazar.id
        assert order.status == 'paid'
        assert order.amount == Decimal('500.00')
        # Bazar pickups have nothing to track — never the home placeholder.
        assert order.shipping_link == ''
        assert order.shipping_cost is None
        # Items came from the cart snapshot; cart emptied afterwards.
        assert order.order_items.count() == 1
        item = order.order_items.first()
        assert item.record.title == 'Disco De Prueba'
        assert item.price == Decimal('500.00')
        assert cart_with_record.cart_items.count() == 0

    def test_bazar_deleted_after_payment_still_fulfills(self, cart_with_record):
        """The customer paid while the bazar existed but it vanished before the
        webhook arrived: the order must survive with pickup_bazar cleared."""
        bazar = _make_bazar()
        bazar_id = bazar.id
        bazar.delete()
        order = self._fulfill(cart_with_record, bazar_id=bazar_id)
        assert order.pickup_bazar is None
        assert order.shipped_to == 'bazar'

    def test_garbage_bazar_metadata_does_not_break_fulfillment(self, cart_with_record):
        order = self._fulfill(cart_with_record, bazar_id='not-a-number',
                              session_id='cs_test_fulfill_garbage')
        assert order.pickup_bazar is None
        assert order.order_items.count() == 1


# ── Orders API surface ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestOrderSerializerPickupBazar:
    def test_nested_pickup_bazar_in_orders_api(self, api_client, user):
        bazar = _make_bazar()
        Order.objects.create(
            stripe_checkout_session_id='cs_test_serializer_bazar',
            amount=Decimal('300.00'),
            currency='mxn',
            user_email=user.email,
            shipped_to='bazar',
            status='paid',
            pickup_bazar=bazar,
        )
        api_client.force_authenticate(user=user)
        resp = api_client.get(reverse('get-user-orders'))
        assert resp.status_code == 200
        pickup = resp.data[0]['pickup_bazar']
        assert pickup is not None
        assert pickup['id'] == bazar.id
        assert pickup['name'] == bazar.name
        assert pickup['date'] == bazar.date.isoformat()
        assert pickup['schedule'] == bazar.schedule
        assert pickup['address'] == bazar.address
        assert pickup['google_maps_url'] == bazar.google_maps_url

    def test_home_order_has_null_pickup_bazar(self, api_client, user):
        Order.objects.create(
            stripe_checkout_session_id='cs_test_serializer_home',
            amount=Decimal('300.00'),
            currency='mxn',
            user_email=user.email,
            shipped_to='home',
            status='paid',
        )
        api_client.force_authenticate(user=user)
        resp = api_client.get(reverse('get-user-orders'))
        assert resp.data[0]['pickup_bazar'] is None


# ── Emails ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBazarEmails:
    def _bazar_order(self, session_id, with_bazar=True):
        record = Record.objects.create(title='Vinilo Bazar', price=Decimal('250.00'), stock=3)
        order = Order.objects.create(
            stripe_checkout_session_id=session_id,
            amount=Decimal('250.00'),
            currency='mxn',
            user_email='buyer@example.com',
            shipped_to='bazar' if with_bazar else 'store',
            status='paid',
            pickup_bazar=_make_bazar() if with_bazar else None,
        )
        from apiApp.models import OrderItem
        OrderItem.objects.create(order=order, record=record, quantity=1, price=Decimal('250.00'))
        return order

    def test_email_context_contains_pickup_details(self, db):
        order = self._bazar_order('cs_test_ctx_bazar')
        context = _order_email_context(order)
        bazar = order.pickup_bazar
        expected_date = bazar.date.strftime('%d/%m/%Y')
        assert context['pickup_bazar'] == {
            'name': bazar.name,
            'date_str': expected_date,
            'schedule': bazar.schedule,
            'address': bazar.address,
            'google_maps_url': bazar.google_maps_url,
        }
        assert context['shipped_label'] == 'Recoges en bazar'

    def test_email_context_without_bazar_is_none(self, db):
        order = self._bazar_order('cs_test_ctx_store', with_bazar=False)
        context = _order_email_context(order)
        assert context['pickup_bazar'] is None

    def test_order_created_email_renders_bazar_block(self, db):
        from apiApp.services import send_order_created_email

        order = self._bazar_order('cs_test_mail_bazar')
        send_order_created_email(order)

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        html_body = message.alternatives[0][0]
        assert 'Recoger en bazar' in html_body
        assert order.pickup_bazar.name in html_body
        assert order.pickup_bazar.address in html_body
        assert order.pickup_bazar.google_maps_url in html_body

    def test_seller_notification_email_renders_bazar_block(self, db, settings):
        from apiApp.services import send_order_notification_email

        settings.SELLER_NOTIFY_EMAILS = ['seller@moctezuma.test']
        order = self._bazar_order('cs_test_mail_seller')
        send_order_notification_email(order)

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ['seller@moctezuma.test']
        html_body = message.alternatives[0][0]
        assert 'recoge en bazar' in html_body.lower()
        assert order.pickup_bazar.name in html_body
