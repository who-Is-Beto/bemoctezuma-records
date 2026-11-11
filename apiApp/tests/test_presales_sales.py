from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from apiApp.models import Owner
from apiApp.services import presales as svc
from apiApp.services.sales import estimated_stripe_fee, sales_csv_rows, sales_metrics
from apiApp.tests.presale_factories import make_option, make_order

D = Decimal


def channel(metrics, name):
    return next(c for c in metrics['channels'] if c['channel'] == name)


def method(metrics, name):
    return next((m for m in metrics['payment_methods'] if m['method'] == name), None)


@pytest.mark.django_db
def test_paid_presale_counts_on_paid_day():
    order = make_order(make_option(cost_price=D('450')))
    svc.record_payment(order, D('800'), 'cash')
    today = timezone.localdate()
    presale = channel(sales_metrics(today, today), 'presale')
    assert presale['count'] == 1 and presale['gross'] == '800.00'
    assert presale['cost'] == '450.00' and presale['profit'] == '350.00'
    assert sales_metrics(today + timedelta(days=1), today + timedelta(days=1))['summary']['count'] == 0


@pytest.mark.django_db
def test_deposit_only_does_not_count():
    order = make_order()
    svc.record_payment(order, D('300'), 'cash')
    assert channel(sales_metrics(), 'presale')['count'] == 0


@pytest.mark.django_db
def test_voided_payment_removes_from_metrics(user):
    order = make_order()
    payment = svc.record_payment(order, D('800'), 'cash')
    svc.void_payment(payment, by=user, reason='Error')
    assert channel(sales_metrics(), 'presale')['count'] == 0


@pytest.mark.django_db
def test_cancelled_after_paid_does_not_count(user):
    order = make_order()
    svc.record_payment(order, D('800'), 'cash')
    svc.cancel(order, by=user, reason='Reembolso')
    assert channel(sales_metrics(), 'presale')['count'] == 0


@pytest.mark.django_db
def test_method_split_proportional():
    order = make_order(item_price=D('750'), discount_applied=D('50'))
    svc.record_payment(order, D('300'), 'cash')
    svc.record_payment(order, D('450'), 'stripe', stripe_session_id='cs_x')
    metrics = sales_metrics()
    assert method(metrics, 'cash')['gross'] == '300.00'
    assert method(metrics, 'stripe')['gross'] == '450.00'
    assert channel(metrics, 'presale')['commission'] == str(estimated_stripe_fee(D('450')))


@pytest.mark.django_db
def test_presale_shipping_is_reported_apart():
    order = make_order(delivery='shipping', shipping_cost=D('150'))
    svc.record_payment(order, D('950'), 'cash', shipping_amount=D('150'))
    metrics = sales_metrics()
    assert channel(metrics, 'presale')['gross'] == '800.00'
    assert metrics['summary']['shipping'] == '150.00'


@pytest.mark.django_db
def test_timeline_has_presale_series():
    order = make_order()
    svc.record_payment(order, D('800'), 'cash')
    today = timezone.localdate()
    point = sales_metrics(today, today)['timeline']['points'][0]
    assert point['presale'] == '800.00'


@pytest.mark.django_db
def test_presale_csv_row():
    owner = Owner.objects.create(name='Juan', email='juan@x.com')
    order = make_order(make_option(owner=owner, cost_price=D('400')))
    svc.record_payment(order, D('500'), 'cash')
    svc.record_payment(order, D('300'), 'transfer')
    rows = sales_csv_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row[2] == 'Preventa' and row[4] == order.code
    assert row[5] == 'Efectivo + Transferencia'
    assert row[10] == 'Juan' and row[14] == '800.00' and row[18] == '400.00'


@pytest.mark.django_db
def test_consignor_owner_emailed_once_when_paid():
    owner = Owner.objects.create(name='Juan', email='juan@x.com')
    order = make_order(make_option(owner=owner))
    svc.record_payment(order, D('300'), 'cash')
    assert mail.outbox == []
    svc.record_payment(order, D('500'), 'cash')
    assert [m.to for m in mail.outbox] == [['juan@x.com']]
    order.refresh_from_db()
    assert order.owner_notified is True
