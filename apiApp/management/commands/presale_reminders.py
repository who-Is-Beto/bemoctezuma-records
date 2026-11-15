"""Daily: remind buyers whose presale balance is due in 30 or 7 days.

Run once a day (Railway cron service): `python manage.py presale_reminders`.
Only for orders whose every version arrived (the deadline can't pass before that).
Never expires orders: the owner does that from Administración.
"""
from django.core.management.base import BaseCommand

from apiApp.models import PreSaleOrder
from apiApp.services import presales as svc
from apiApp.services.emailing import send_presale_email

REMINDER_DAYS = (30, 7)


class Command(BaseCommand):
    help = "Email presale buyers whose deadline to pay the rest is 30 or 7 days away."

    def handle(self, *args, **options):
        sent = 0
        orders = PreSaleOrder.objects.select_related('user').prefetch_related('items__option').filter(
            status__in=('apartado', 'pagado'),
        )
        for order in orders:
            if svc.last_arrival(order) is None:  # some version is still on its way
                continue
            days = svc.days_left(order)
            if days not in REMINDER_DAYS or days in (order.reminders_sent or []):
                continue
            if svc.balance(order) <= 0:
                continue
            if send_presale_email(order, 'reminder'):
                order.reminders_sent = [*(order.reminders_sent or []), days]
                order.save(update_fields=['reminders_sent'])
                sent += 1
        self.stdout.write(f"Recordatorios enviados: {sent}")
