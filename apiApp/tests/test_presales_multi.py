"""One order can hold versions of several presales: one box, one payment."""
from decimal import Decimal as D

import pytest
from django.core import mail

from apiApp.models import PreSaleOrder
from apiApp.serilizers.presales import presale_order_payload, presale_totals
from apiApp.services import presales as svc
from apiApp.tests.presale_factories import make_option, make_order, make_presale
from apiApp.tests.test_presales_checkout import client  # noqa: F401
from apiApp.tests.test_presales_lines_api import post_reserve


@pytest.fixture
def blue(db):
    return make_option(make_presale(title='Kind of Blue', artist='Miles Davis'), name='Negro')


@pytest.fixture
def road(db):
    return make_option(make_presale(title='Abbey Road', artist='The Beatles'), name='Picture', price=D('600'))


def test_reserve_across_presales_is_one_order(client, blue, road):
    res, create = post_reserve(client, [{'option_id': blue.id, 'quantity': 1}, {'option_id': road.id, 'quantity': 1}])
    assert res.status_code == 200, res.json()
    order = PreSaleOrder.objects.get()
    assert {i.option.presale_id for i in order.items.all()} == {blue.presale_id, road.presale_id}
    names = [line['price_data']['product_data']['name'] for line in create.call_args.kwargs['line_items']]
    assert names[0].startswith('Kind of Blue — Negro') and names[1].startswith('Abbey Road — Picture')


def test_payload_and_labels_name_every_presale(blue, road):
    order = make_order(lines=[(blue, 1), (road, 2)])
    assert svc.order_title(order) == 'Kind of Blue + Abbey Road'
    assert svc.lines_label(order) == 'Kind of Blue · Negro ×1, Abbey Road · Picture ×2'
    data = presale_order_payload(order)
    assert [p['title'] for p in data['presales']] == ['Kind of Blue', 'Abbey Road']
    assert [i['presale_title'] for i in data['items']] == ['Kind of Blue', 'Abbey Road']


def test_each_presale_counts_only_its_share(blue, road):
    order = make_order(lines=[(blue, 1), (road, 2)])  # 800 + 1200
    svc.record_payment(order, D('1000'), 'cash')
    blue_totals, road_totals = presale_totals(blue.presale), presale_totals(road.presale)
    assert blue_totals['reserved'] == 1 and road_totals['reserved'] == 2
    assert blue_totals['collected'] == '400.00' and road_totals['collected'] == '600.00'


def test_email_lists_both_records(blue, road):
    from apiApp.services.emailing import send_presale_email
    order = make_order(lines=[(blue, 1), (road, 1)])
    mail.outbox.clear()
    send_presale_email(order, 'reserved')
    body = mail.outbox[0].alternatives[0][0]
    assert 'Kind of Blue + Abbey Road' in body and 'Abbey Road · Picture' in body
