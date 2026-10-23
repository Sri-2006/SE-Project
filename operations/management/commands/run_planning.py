from django.core.management.base import BaseCommand
from operations.models import Inventory
from operations.services.analytics import plan_inventory, refresh_alerts, DataIssue

class Command(BaseCommand):
    help = 'Recompute forecasts, replenishment recommendations and alerts. Suitable for a scheduled daily job.'
    def handle(self, *args, **kwargs):
        generated = 0
        for item in Inventory.objects.all():
            try:
                generated += bool(plan_inventory(item))
            except DataIssue as exc:
                self.stdout.write(f'Skipped {item}: {exc}')
        refresh_alerts()
        self.stdout.write(self.style.SUCCESS(f'{generated} recommendations generated.'))
