import math
import random
import secrets
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from operations.models import *
from operations.services.analytics import plan_inventory, refresh_alerts

class Command(BaseCommand):
    help = 'Create an explicitly synthetic demo dataset once, without overwriting existing business data.'

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo seeding is disabled when DJANGO_DEBUG=0.')
        if Product.objects.exists() or User.objects.filter(username__in=['planner', 'manager', 'admin']).exists():
            self.stdout.write('Existing data/accounts found; demo seeding skipped.')
            return
        rng = random.Random(42)
        password = secrets.token_urlsafe(12)
        for username, display, group in [('planner', 'Aarav', 'Planner'), ('manager', 'Srikunj', 'Manager'), ('admin', 'Admin', 'Administrator')]:
            user = User.objects.create_user(username=username, password=password, first_name=display,
                email=f'{username}@supplypilot.example', is_staff=username == 'admin', is_superuser=username == 'admin')
            user.groups.add(Group.objects.get_or_create(name=group)[0])
        actor = User.objects.get(username='manager')
        warehouses = [Warehouse.objects.create(name=name, city=city) for name, city in [('Mumbai Central', 'Mumbai'), ('Bengaluru Hub', 'Bengaluru'), ('Delhi North', 'New Delhi')]]
        suppliers = [Supplier.objects.create(name=name, city=city, lead_time_days=lead, contact_email=f'ops{i}@supplier.example')
            for i, (name, city, lead) in enumerate([('Vertex Components', 'Pune', 7), ('Northstar Electronics', 'Noida', 12), ('Evergreen Packaging', 'Ahmedabad', 5), ('Atlas Industrial', 'Chennai', 10)])]
        products = [('Wireless Headphones', 'Electronics', 1850), ('USB-C Power Adapter', 'Electronics', 620), ('Mechanical Keyboard', 'Electronics', 2400),
                    ('Precision Bearings', 'Industrial', 340), ('Thermal Labels', 'Packaging', 85), ('Corrugated Boxes', 'Packaging', 42),
                    ('Smart Sensors', 'Industrial', 1250), ('LED Display Panel', 'Electronics', 3200), ('Cable Assembly', 'Industrial', 180),
                    ('Protective Wrap', 'Packaging', 95), ('Control Module', 'Industrial', 2800), ('Compact Charger', 'Electronics', 460)]
        stocks = []
        today = timezone.localdate()
        for i, (name, category, cost) in enumerate(products):
            supplier = suppliers[2 if category == 'Packaging' else i % 4]
            product = Product.objects.create(sku=f'SKU-{1001+i}', name=name, category=category, unit_cost=Decimal(cost), supplier=supplier)
            for w in range(2):
                warehouse = warehouses[(i+w) % 3]
                daily = 5 + (i * 3 + w * 7) % 23
                quantity = (0 if i == 0 and w == 0 else daily * (3 if i % 3 == 0 else 30))
                item = Inventory.objects.create(product=product, warehouse=warehouse, quantity=quantity, reserved=min(quantity, daily), reorder_point=daily * supplier.lead_time_days)
                stocks.append(item)
                Sale.objects.bulk_create([Sale(inventory=item, date=today - timedelta(days=90-j), quantity=max(0, round(daily * (1 + .22 * math.sin(j * 2 * math.pi / 7)) + rng.gauss(0, 2)))) for j in range(90)])
                StockMovement.objects.create(inventory=item, delta=quantity, balance=quantity, reason='Synthetic demo opening balance', actor=actor)
        for i, supplier in enumerate(suppliers):
            item = next(s for s in stocks if s.product.supplier_id == supplier.pk)
            for j in range(12):
                expected = today - timedelta(days=5 + 3*j)
                late = j < (7 if i == 1 else 2 if i == 3 else 1)
                PurchaseOrder.objects.create(reference=f'PO-H{i+1}{j+1:03}', inventory=item, supplier=supplier, quantity=200,
                    unit_cost=item.product.unit_cost, status='delivered', expected_date=expected,
                    delivered_date=expected + timedelta(days=2 if late else -1), received_quantity=150 if late else 200, disrupted=late)
        for j, item in enumerate(stocks[1:9]):
            PurchaseOrder.objects.create(reference=f'PO-2026-{j+101}', inventory=item, supplier=item.product.supplier, quantity=120 + j*25,
                unit_cost=item.product.unit_cost, status=['in_transit', 'ordered', 'delayed', 'draft'][j % 4],
                expected_date=today + timedelta(days=-2 if j % 4 == 2 else j+2), carrier=['Blue Dart', 'Delhivery', 'DHL'][j % 3],
                tracking_reference=f'SP{620000+j}', disrupted=j % 4 == 2)
        for item in stocks:
            plan_inventory(item, actor)
        refresh_alerts()
        AuditLog.objects.create(actor=actor, action='Demo workspace created', target='24 inventory positions', details={'synthetic': True})
        credentials = f'SupplyPilot LOCAL DEMO accounts\n\nmanager / {password}\nplanner / {password}\nadmin / {password}\n\nSynthetic data only. Change passwords before sharing access.\n'
        transaction.on_commit(lambda: (settings.BASE_DIR / '.demo-credentials.txt').write_text(credentials, encoding='utf-8'))
        self.stdout.write(self.style.SUCCESS('Demo created: 12 products, 3 warehouses, 4 suppliers, 2,160 daily sales. Credentials: .demo-credentials.txt'))

