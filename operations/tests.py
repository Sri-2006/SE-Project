import io
import json
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from .models import *
from .services.analytics import build_forecast, plan_inventory, supplier_metrics, DataIssue, refresh_alerts
from .services.workflows import decide, adjust_stock, update_order, WorkflowError
from .services.imports import import_csv, process_event

class SupplyPilotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.planner = User.objects.create_user('planner-test', password='Testing-pass-123!')
        cls.manager = User.objects.create_user('manager-test', password='Testing-pass-123!')
        cls.outsider = User.objects.create_user('outsider', password='Testing-pass-123!')
        for user, group in [(cls.planner, 'Planner'), (cls.manager, 'Manager')]:
            user.groups.add(Group.objects.get_or_create(name=group)[0])
        cls.supplier = Supplier.objects.create(name='Test supplier', city='Pune', lead_time_days=7)
        cls.warehouse = Warehouse.objects.create(name='Warehouse A', city='Mumbai')
        cls.product = Product.objects.create(sku='TEST-1', name='Test product', category='Test', unit_cost='100.00', supplier=cls.supplier)
        cls.item = Inventory.objects.create(product=cls.product, warehouse=cls.warehouse, quantity=10, reserved=2)
        today = timezone.localdate()
        Sale.objects.bulk_create([Sale(inventory=cls.item, date=today-timedelta(days=i), quantity=10+i%7) for i in range(1, 57)])
        cls.rec = plan_inventory(cls.item, cls.manager)
        cls.item.refresh_from_db()

    def event(self, event_id='evt-1', **changes):
        return {'event_id': event_id, 'type': 'inventory', 'sku': 'TEST-1', 'warehouse': 'Warehouse A', 'quantity': 40, **changes}

    def upload(self, value):
        return SimpleUploadedFile('data.csv', value.encode(), content_type='text/csv')

    def test_valid_login_redirects(self):
        response = self.client.post('/login/', {'username': self.planner.username, 'password': 'Testing-pass-123!'})
        self.assertRedirects(response, '/')

    def test_invalid_login_denied(self):
        response = self.client.post('/login/', {'username': self.planner.username, 'password': 'wrong'})
        self.assertContains(response, 'Please enter a correct username and password')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_is_throttled(self):
        for _ in range(5):
            self.client.post('/login/', {'username': self.planner.username, 'password': 'wrong'})
        response = self.client.post('/login/', {'username': self.planner.username, 'password': 'Testing-pass-123!'})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_anonymous_pages_require_login(self):
        for path in ['/', '/inventory/', '/forecasting/', '/suppliers/', '/orders/', '/recommendations/', '/reports/', '/imports/', '/alerts/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)

    def test_unassigned_user_denied(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get('/').status_code, 403)

    def test_planner_cannot_read_manager_report_or_manage_catalog(self):
        self.client.force_login(self.planner)
        for path in ['/reports/', '/reports/export/', '/catalog/product/new/']:
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_manager_all_pages_render(self):
        self.client.force_login(self.manager)
        paths = ['/', '/inventory/', f'/inventory/{self.item.pk}/', '/forecasting/', '/suppliers/', f'/suppliers/{self.supplier.pk}/',
                 '/recommendations/', f'/recommendations/{self.rec.pk}/', '/orders/', '/orders/new/', '/alerts/', '/reports/', '/imports/',
                 '/catalog/product/new/', '/catalog/supplier/new/', '/catalog/warehouse/new/', '/catalog/inventory/new/', '/account/password/']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_csrf_protects_actions_and_integration(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.planner)
        self.assertEqual(browser.post('/api/events/', data=json.dumps(self.event()), content_type='application/json').status_code, 403)
        self.assertEqual(browser.post(f'/recommendations/{self.rec.pk}/', {'action': 'approved'}).status_code, 403)

    def test_get_cannot_trigger_planning_or_logout(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get('/recommendations/run/').status_code, 405)
        self.assertEqual(self.client.get('/logout/').status_code, 405)

    def test_inventory_search_and_htmx(self):
        self.client.force_login(self.planner)
        response = self.client.get('/inventory/?q=TEST-1', HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Test product')
        self.assertNotContains(response, '<!doctype')
        self.assertContains(self.client.get('/inventory/?q=missing'), 'No inventory matches')

    def test_stock_zero_and_maximum_boundaries(self):
        item = adjust_stock(self.item.pk, 0, 0, 'Zero stock', self.planner)
        self.assertEqual(item.health, 'critical')
        item = adjust_stock(self.item.pk, 1000000, 2, 'Maximum', self.planner)
        self.assertEqual(item.quantity, 1000000)
        with self.assertRaises(WorkflowError):
            adjust_stock(self.item.pk, 1000001, 0, 'Too large', self.planner)

    def test_reserved_cannot_exceed_stock(self):
        with self.assertRaises(WorkflowError):
            adjust_stock(self.item.pk, 10, 11, 'Invalid', self.planner)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10)

    def test_stock_adjustment_writes_ledger(self):
        adjust_stock(self.item.pk, 30, 2, 'Physical count', self.planner)
        self.assertEqual(StockMovement.objects.get().delta, 20)
        self.assertTrue(AuditLog.objects.filter(action='Stock adjusted').exists())

    def test_database_rejects_invalid_stock(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inventory.objects.filter(pk=self.item.pk).update(reserved=100)

    def test_forecasts_have_correct_horizons_and_no_negative_units(self):
        for horizon in (7, 30, 90):
            forecast = build_forecast(self.item, horizon)
            self.assertEqual(len(forecast.points), horizon)
            self.assertEqual(forecast.points[0]['date'], timezone.localdate().isoformat())
            self.assertTrue(all(p['quantity'] >= 0 for p in forecast.points))
            self.assertGreaterEqual(forecast.mae, 0)

    def test_forecast_rejects_insufficient_data(self):
        self.item.sales.all().delete()
        with self.assertRaisesMessage(DataIssue, 'Insufficient data'):
            build_forecast(self.item)

    def test_forecast_rejects_missing_days(self):
        self.item.sales.filter(date=timezone.localdate()-timedelta(days=10)).delete()
        with self.assertRaisesMessage(DataIssue, 'missing dates'):
            build_forecast(self.item)

    def test_forecast_rejects_stale_data(self):
        self.item.sales.filter(date=timezone.localdate()-timedelta(days=1)).delete()
        with self.assertRaisesMessage(DataIssue, 'stale'):
            build_forecast(self.item)

    def test_forecast_rejects_invalid_horizon(self):
        with self.assertRaises(DataIssue):
            build_forecast(self.item, -5)

    def test_missing_forecast_data_has_visible_explanation(self):
        self.client.force_login(self.planner)
        self.item.sales.all().delete()
        response = self.client.post('/forecasting/', {'inventory': self.item.pk, 'horizon': 30})
        self.assertContains(response, 'Insufficient data')
        self.assertContains(response, 'previously saved forecast')

    def test_supplier_without_deliveries_is_unscored(self):
        self.assertIsNone(supplier_metrics(self.supplier)['score'])

    def test_supplier_risk_uses_observed_delivery_metrics(self):
        today = timezone.localdate()
        PurchaseOrder.objects.create(reference='HIST', inventory=self.item, supplier=self.supplier, quantity=100, unit_cost=100,
            expected_date=today-timedelta(days=2), delivered_date=today, received_quantity=50, disrupted=True, status='delivered')
        metrics = supplier_metrics(self.supplier)
        self.assertEqual(metrics['score'], 85)
        self.assertEqual(metrics['on_time'], 0)
        self.assertEqual(metrics['fulfillment'], 50)

    def test_recommendation_contains_supporting_evidence(self):
        self.assertGreater(self.rec.quantity, 0)
        self.assertIn('safety_stock', self.rec.evidence)
        self.assertIn('lead time', self.rec.reason)

    def test_rerun_supersedes_previous_pending_recommendation(self):
        new_rec = plan_inventory(self.item, self.manager)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, 'superseded')
        self.assertEqual(Recommendation.objects.filter(status='pending', inventory=self.item).count(), 1)
        self.assertNotEqual(new_rec.pk, self.rec.pk)

    def test_approve_creates_one_draft_order(self):
        decide(self.rec.pk, 'approved', self.planner)
        order = PurchaseOrder.objects.get(recommendation=self.rec)
        self.assertEqual(order.status, 'draft')
        self.assertEqual(order.quantity, self.rec.quantity)

    def test_duplicate_approval_does_not_duplicate_order(self):
        decide(self.rec.pk, 'approved', self.planner)
        with self.assertRaises(WorkflowError):
            decide(self.rec.pk, 'approved', self.planner)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_rejection_requires_reason_and_creates_no_order(self):
        with self.assertRaises(WorkflowError):
            decide(self.rec.pk, 'rejected', self.planner)
        decide(self.rec.pk, 'rejected', self.planner, note='Demand confirmed lower')
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_manager_override_records_quantity(self):
        decide(self.rec.pk, 'overridden', self.manager, note='Use supplier carton size', quantity=300)
        self.assertEqual(PurchaseOrder.objects.get().quantity, 300)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, 'overridden')
        self.assertEqual(self.rec.decision_note, 'Use supplier carton size')

    def test_planner_cannot_override(self):
        with self.assertRaises(PermissionDenied):
            decide(self.rec.pk, 'overridden', self.planner, note='No authority', quantity=20)

    def test_changed_inventory_blocks_stale_approval(self):
        adjust_stock(self.item.pk, 400, 0, 'New receipt', self.planner)
        with self.assertRaisesMessage(WorkflowError, 'Inventory changed'):
            decide(self.rec.pk, 'approved', self.planner)

    def test_failed_order_creation_rolls_back_decision(self):
        with patch('operations.services.workflows.PurchaseOrder.objects.create', side_effect=RuntimeError('simulated interruption')):
            with self.assertRaises(RuntimeError):
                decide(self.rec.pk, 'approved', self.manager)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, 'pending')
        self.assertIsNone(self.rec.decided_at)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_full_replenishment_workflow(self):
        self.client.force_login(self.manager)
        response = self.client.post(f'/recommendations/{self.rec.pk}/', {'action': 'approved', 'note': 'Validated forecast'})
        self.assertEqual(response.status_code, 302)
        order = PurchaseOrder.objects.get(recommendation=self.rec)
        for status in ['ordered', 'in_transit', 'delivered']:
            response = self.client.post(f'/orders/{order.pk}/', {'status': status, 'received_quantity': order.quantity, 'carrier': 'Test carrier', 'tracking_reference': 'TRACK1'})
            self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10 + order.quantity)
        self.assertEqual(StockMovement.objects.filter(reason=f'Received {order.reference}').count(), 1)

    def test_duplicate_delivery_cannot_inflate_stock(self):
        decide(self.rec.pk, 'approved', self.manager)
        order = PurchaseOrder.objects.get()
        update_order(order.pk, 'ordered', None, self.manager)
        update_order(order.pk, 'delivered', order.quantity, self.manager)
        with self.assertRaises(WorkflowError):
            update_order(order.pk, 'delivered', order.quantity, self.manager)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10 + order.quantity)

    def test_delay_creates_alert(self):
        decide(self.rec.pk, 'approved', self.manager)
        order = PurchaseOrder.objects.get()
        update_order(order.pk, 'ordered', None, self.manager)
        update_order(order.pk, 'delayed', None, self.manager)
        self.assertTrue(Alert.objects.filter(key=f'order-{order.pk}', active=True).exists())

    def test_alert_read_state_is_per_user(self):
        refresh_alerts()
        alert = Alert.objects.filter(active=True).first()
        self.client.force_login(self.planner)
        self.client.post(f'/alerts/{alert.pk}/read/')
        self.assertTrue(alert.read_by.filter(pk=self.planner.pk).exists())
        self.assertFalse(alert.read_by.filter(pk=self.manager.pk).exists())

    def test_duplicate_integration_event_is_idempotent(self):
        self.assertTrue(process_event(self.event(), self.planner))
        self.assertFalse(process_event(self.event(), self.planner))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 40)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_reused_event_id_with_different_data_rejected(self):
        process_event(self.event(), self.planner)
        with self.assertRaises(WorkflowError):
            process_event(self.event(quantity=80), self.planner)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 40)

    def test_invalid_event_quarantined_and_stock_unchanged(self):
        with self.assertRaises(WorkflowError):
            process_event(self.event(quantity=-1), self.planner)
        self.assertEqual(IntegrationEvent.objects.get().status, 'rejected')
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10)

    def test_invalid_event_shape_logs_rejection(self):
        with self.assertRaises(WorkflowError):
            process_event(['bad'], self.planner)
        self.assertEqual(IntegrationEvent.objects.get().status, 'rejected')

    def test_api_accepts_valid_and_rejects_malformed_events(self):
        self.client.force_login(self.planner)
        response = self.client.post('/api/events/', data=json.dumps(self.event()), content_type='application/json')
        self.assertEqual(response.status_code, 201)
        response = self.client.post('/api/events/', data='{bad', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(IntegrationEvent.objects.filter(status='rejected').exists())

    def test_atomic_csv_import_rejects_all_rows_on_error(self):
        content = 'sku,warehouse,quantity\nTEST-1,Warehouse A,99\nUNKNOWN,Warehouse A,40\n'
        with self.assertRaises(WorkflowError):
            import_csv(self.upload(content), 'inventory', self.planner)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10)
        self.assertEqual(IntegrationEvent.objects.get().status, 'rejected')

    def test_replayed_csv_is_skipped(self):
        content = 'sku,warehouse,quantity\nTEST-1,Warehouse A,99\n'
        self.assertEqual(import_csv(self.upload(content), 'inventory', self.planner), (1, 1))
        self.assertEqual(import_csv(self.upload(content), 'inventory', self.planner), (0, 1))

    def test_sales_import_upserts_daily_history(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        content = f'sku,warehouse,date,quantity\nTEST-1,Warehouse A,{yesterday},0\n'
        import_csv(self.upload(content), 'sales', self.planner)
        self.assertEqual(self.item.sales.get(date=yesterday).quantity, 0)

    def test_sales_import_rejects_future_dates(self):
        content = f'sku,warehouse,date,quantity\nTEST-1,Warehouse A,{timezone.localdate()},10\n'
        with self.assertRaises(WorkflowError):
            import_csv(self.upload(content), 'sales', self.planner)

    def test_csv_duplicate_rows_rejected(self):
        content = 'sku,warehouse,quantity\nTEST-1,Warehouse A,10\nTEST-1,Warehouse A,20\n'
        with self.assertRaises(WorkflowError):
            import_csv(self.upload(content), 'inventory', self.planner)

    def test_csv_export_is_manager_only_and_has_data(self):
        self.client.force_login(self.manager)
        response = self.client.get('/reports/export/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn(b'TEST-1', response.content)

    def test_csv_export_neutralizes_formula_cells(self):
        self.product.name = '=1+1'
        self.product.save()
        self.client.force_login(self.manager)
        self.assertIn(b"'=1+1", self.client.get('/reports/export/').content)

    def test_unknown_records_return_404(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get('/inventory/999999/').status_code, 404)

    def test_health_checks_database(self):
        self.assertEqual(self.client.get('/health/').json(), {'status': 'ok', 'application': 'supplypilot'})

    def test_new_open_order_blocks_stale_recommendation(self):
        PurchaseOrder.objects.create(reference='NEW-DRAFT', inventory=self.item, supplier=self.supplier, quantity=50, unit_cost=100, expected_date=timezone.localdate()+timedelta(days=5))
        with self.assertRaisesMessage(WorkflowError, 'Open orders changed'):
            decide(self.rec.pk, 'approved', self.manager)

    def test_supplier_changes_block_stale_recommendation(self):
        self.supplier.lead_time_days = 21
        self.supplier.save()
        with self.assertRaisesMessage(WorkflowError, 'Supplier or product details changed'):
            decide(self.rec.pk, 'approved', self.manager)

    def test_sales_import_supersedes_pending_recommendations(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        import_csv(self.upload(f'sku,warehouse,date,quantity\nTEST-1,Warehouse A,{yesterday},0\n'), 'sales', self.manager)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, 'superseded')

    def test_inventory_edit_route_preserves_ledger_workflow(self):
        self.client.force_login(self.manager)
        response = self.client.post(f'/catalog/inventory/{self.item.pk}/edit/', {'quantity': 999})
        self.assertRedirects(response, f'/inventory/{self.item.pk}/')
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10)

    def test_reopened_alert_is_unread_again(self):
        refresh_alerts()
        alert = Alert.objects.get(key=f'stock-{self.item.pk}')
        alert.read_by.add(self.manager)
        adjust_stock(self.item.pk, 10000, 0, 'Replenished', self.manager)
        adjust_stock(self.item.pk, 0, 0, 'New stockout', self.manager)
        alert.refresh_from_db()
        self.assertTrue(alert.active)
        self.assertFalse(alert.read_by.filter(pk=self.manager.pk).exists())


from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase

@skipUnless(connection.vendor == 'postgresql', 'Row-lock concurrency is verified on PostgreSQL.')
class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        SupplyPilotTests.setUpTestData.__func__(self.__class__)

    def test_concurrent_approvals_create_one_order(self):
        def approve(_):
            close_old_connections()
            try:
                actor = User.objects.get(pk=self.manager.pk)
                decide(self.rec.pk, 'approved', actor)
                return 'approved'
            except WorkflowError:
                return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(approve, range(2)))
        self.assertCountEqual(results, ['approved', 'conflict'])
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_concurrent_receipts_add_stock_once(self):
        decide(self.rec.pk, 'approved', self.manager)
        order = PurchaseOrder.objects.get()
        update_order(order.pk, 'ordered', None, self.manager)
        def receive(_):
            close_old_connections()
            try:
                actor = User.objects.get(pk=self.manager.pk)
                update_order(order.pk, 'delivered', order.quantity, actor)
                return 'delivered'
            except WorkflowError:
                return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(receive, range(2)))
        self.assertCountEqual(results, ['delivered', 'conflict'])
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10 + order.quantity)

    def test_concurrent_duplicate_events_are_idempotent(self):
        payload = {'event_id': 'concurrent-1', 'type': 'inventory', 'sku': 'TEST-1', 'warehouse': 'Warehouse A', 'quantity': 50}
        def ingest(_):
            close_old_connections()
            try:
                return process_event(payload, User.objects.get(pk=self.manager.pk))
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(ingest, range(2)))
        self.assertCountEqual(results, [True, False])
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 50)
        self.assertEqual(StockMovement.objects.count(), 1)
