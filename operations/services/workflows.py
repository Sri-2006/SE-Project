import uuid
from datetime import timedelta
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from operations.models import Inventory, Recommendation, PurchaseOrder, StockMovement, AuditLog
from operations.permissions import role, ALL_ROLES
from .analytics import refresh_alerts

class WorkflowError(ValueError):
    pass

@transaction.atomic
def adjust_stock(item_id, quantity, reserved, reason, actor):
    if role(actor) not in ALL_ROLES:
        raise PermissionDenied
    item = Inventory.objects.select_for_update().get(pk=item_id)
    if not reason.strip() or not 0 <= reserved <= quantity <= 1000000:
        raise WorkflowError('Provide a reason and quantities between 0 and 1,000,000. Reserved stock cannot exceed on-hand stock.')
    before = item.quantity
    item.quantity, item.reserved = quantity, reserved
    item.save()
    StockMovement.objects.create(inventory=item, delta=quantity - before, balance=quantity, reason=reason[:240], actor=actor)
    AuditLog.objects.create(actor=actor, action='Stock adjusted', target=str(item), details={'before': before, 'after': quantity, 'reserved': reserved, 'reason': reason})
    refresh_alerts()
    return item

@transaction.atomic
def decide(rec_id, action, actor, note='', quantity=None):
    if role(actor) not in ALL_ROLES:
        raise PermissionDenied
    if action == 'overridden' and role(actor) not in ('Manager', 'Administrator'):
        raise PermissionDenied
    if action not in ('approved', 'rejected', 'overridden'):
        raise WorkflowError('Invalid decision.')
    if action in ('rejected', 'overridden') and not note.strip():
        raise WorkflowError('Add a reason for rejecting or overriding this recommendation.')
    inventory_id = Recommendation.objects.values_list('inventory_id', flat=True).get(pk=rec_id)
    stock = Inventory.objects.select_for_update().get(pk=inventory_id)
    rec = Recommendation.objects.select_for_update().select_related('inventory__product__supplier').get(pk=rec_id)
    if rec.status != 'pending':
        raise WorkflowError('This recommendation has already been decided or superseded. Refresh to see its current status.')
    if action == 'overridden' and (quantity is None or not 1 <= quantity <= 1000000):
        raise WorkflowError('Override quantity must be between 1 and 1,000,000.')
    if action != 'rejected' and (stock.available != rec.evidence['available'] or stock.reorder_point != rec.evidence['reorder_point']):
        raise WorkflowError('Inventory changed since this recommendation. Run planning again before approval.')
    on_order = stock.orders.exclude(status__in=['delivered', 'cancelled']).aggregate(total=Sum('quantity'))['total'] or 0
    if action != 'rejected' and on_order != rec.evidence['on_order']:
        raise WorkflowError('Open orders changed since this recommendation. Run planning again before approval.')
    if action != 'rejected' and (rec.inventory.product.supplier_id != rec.evidence.get('supplier_id', rec.inventory.product.supplier_id)
            or rec.inventory.product.supplier.lead_time_days != rec.evidence['lead_time']
            or str(rec.inventory.product.unit_cost) != rec.evidence.get('unit_cost', str(rec.inventory.product.unit_cost))):
        raise WorkflowError('Supplier or product details changed. Run planning again before approval.')
    if action != 'rejected' and rec.forecast.history[-1]['date'] < (timezone.localdate() - timedelta(days=1)).isoformat():
        raise WorkflowError('This forecast is stale. Import current sales and run planning before approval.')
    changed = Recommendation.objects.filter(pk=rec.pk, status='pending').update(status=action, decided_by=actor, decided_at=timezone.now(), decision_note=note)
    if not changed:
        raise WorkflowError('Another user has already decided this recommendation.')
    if action != 'rejected':
        PurchaseOrder.objects.create(reference=f'PO-{uuid.uuid4().hex[:8].upper()}', inventory=rec.inventory,
            supplier=rec.inventory.product.supplier, recommendation=rec,
            quantity=quantity if action == 'overridden' else rec.quantity,
            unit_cost=rec.inventory.product.unit_cost,
            expected_date=timezone.localdate() + timedelta(days=rec.inventory.product.supplier.lead_time_days))
    AuditLog.objects.create(actor=actor, action=f'Recommendation {action}', target=f'REC-{rec.pk:04}', details={'note': note, 'quantity': quantity or rec.quantity})
    return rec

TRANSITIONS = {'draft': ['ordered', 'cancelled'], 'ordered': ['in_transit', 'delayed', 'delivered', 'cancelled'],
               'in_transit': ['delayed', 'delivered'], 'delayed': ['in_transit', 'delivered', 'cancelled'], 'delivered': [], 'cancelled': []}

@transaction.atomic
def update_order(order_id, status, received, actor, carrier='', tracking=''):
    if role(actor) not in ALL_ROLES:
        raise PermissionDenied
    inventory_id = PurchaseOrder.objects.values_list('inventory_id', flat=True).get(pk=order_id)
    item = Inventory.objects.select_for_update().get(pk=inventory_id)
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    if status not in TRANSITIONS.get(order.status, []):
        raise WorkflowError(f'Cannot move an order from {order.get_status_display()} to {status}.')
    previous = order.status
    if status == 'delivered':
        if received is None or not 0 <= received <= order.quantity:
            raise WorkflowError('Received quantity must be between zero and the ordered quantity.')
        if item.quantity + received > 1000000:
            raise WorkflowError('This receipt would exceed the maximum stock quantity.')
        order.received_quantity = received
        order.delivered_date = timezone.localdate()
        item.quantity += received
        item.save()
        StockMovement.objects.create(inventory=item, delta=received, balance=item.quantity, reason=f'Received {order.reference}', actor=actor)
    order.status = status
    order.disrupted = order.disrupted or status == 'delayed'
    order.carrier, order.tracking_reference = carrier, tracking
    order.save()
    AuditLog.objects.create(actor=actor, action='Order status updated', target=order.reference, details={'before': previous, 'after': status, 'received': received})
    refresh_alerts()
    return order
