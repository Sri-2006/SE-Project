import csv
import hashlib
import io
import uuid
from datetime import date
from django.db import transaction, IntegrityError
from django.utils import timezone
from operations.models import Inventory, IntegrationEvent, Sale, AuditLog, Recommendation
from .workflows import adjust_stock, update_order, WorkflowError

def number(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError('Quantities must be whole, non-negative numbers.')
    result = int(value)
    if result > 1000000:
        raise ValueError('Quantity exceeds 1,000,000.')
    return result

def process_event(payload, actor):
    event_id = str(payload.get('event_id', '')) if isinstance(payload, dict) else ''
    if not event_id or len(event_id) > 100:
        IntegrationEvent.objects.create(event_id=f'invalid-{uuid.uuid4().hex}', payload=payload,
            status='rejected', error='A unique event_id of at most 100 characters is required.')
        raise WorkflowError('A unique event_id of at most 100 characters is required.')
    previous = IntegrationEvent.objects.filter(event_id=event_id).first()
    if previous:
        if previous.payload != payload:
            raise WorkflowError('This event ID already exists with a different payload.')
        if previous.status == 'rejected':
            raise WorkflowError(previous.error + ' Submit corrected data with a new event ID.')
        return False
    try:
        with transaction.atomic():
            IntegrationEvent.objects.create(event_id=event_id, payload=payload, status='accepted')
            if payload.get('type') == 'inventory':
                item = Inventory.objects.get(product__sku=payload['sku'], warehouse__name=payload['warehouse'])
                adjust_stock(item.pk, number(payload['quantity']), number(payload.get('reserved', item.reserved)), f'Integration event {event_id}', actor)
            elif payload.get('type') == 'shipment':
                from operations.models import PurchaseOrder
                order = PurchaseOrder.objects.get(reference=payload['order_reference'])
                received = number(payload['received_quantity']) if 'received_quantity' in payload else None
                update_order(order.pk, payload['status'], received, actor, payload.get('carrier', order.carrier), payload.get('tracking_reference', order.tracking_reference))
            else:
                raise ValueError('Event type must be inventory or shipment.')
            AuditLog.objects.create(actor=actor, action='Integration event accepted', target=event_id)
        return True
    except IntegrityError:
        existing = IntegrationEvent.objects.filter(event_id=event_id).first()
        if existing and existing.status == 'accepted' and existing.payload == payload:
            return False
        error = 'Duplicate event or database constraint conflict.'
    except (ValueError, KeyError, Inventory.DoesNotExist) as exc:
        error = f'Invalid event: {exc}'
    except Exception as exc:
        from django.core.exceptions import ObjectDoesNotExist
        if not isinstance(exc, ObjectDoesNotExist):
            raise
        error = 'Referenced order or inventory record does not exist.'
    IntegrationEvent.objects.get_or_create(event_id=event_id, defaults={'payload': payload, 'status': 'rejected', 'error': error})
    raise WorkflowError(error)

def import_csv(upload, kind, actor):
    if upload.size > 2 * 1024 * 1024:
        raise WorkflowError('CSV files must be smaller than 2 MB.')
    content = upload.read()
    digest = hashlib.sha256(content).hexdigest()[:24]
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
        required = {'sku', 'warehouse', 'quantity'} | ({'date'} if kind == 'sales' else set())
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError('Required columns: ' + ', '.join(sorted(required)))
        validated, seen = [], set()
        for line, row in enumerate(reader, 2):
            if line > 10001:
                raise ValueError('A maximum of 10,000 rows is supported.')
            item = Inventory.objects.get(product__sku=row['sku'].strip(), warehouse__name=row['warehouse'].strip())
            quantity = number(row['quantity'].strip())
            sales_date = date.fromisoformat(row['date']) if kind == 'sales' else None
            if sales_date and sales_date >= timezone.localdate():
                raise ValueError(f'Row {line}: use complete sales days before today.')
            key = (item.pk, sales_date)
            if key in seen:
                raise ValueError(f'Row {line}: duplicate product/location/date in this file.')
            seen.add(key)
            if kind == 'inventory' and quantity < item.reserved:
                raise ValueError(f'Row {line}: on-hand quantity is below reserved stock.')
            validated.append((item, quantity, sales_date, line))
        if not validated:
            raise ValueError('The CSV has no data rows.')
        changed = 0
        with transaction.atomic():
            # Match workflow lock order so imports cannot race approvals or receipts.
            list(Inventory.objects.select_for_update().filter(pk__in=[entry[0].pk for entry in validated]).order_by('pk'))
            for item, quantity, sales_date, line in validated:
                if kind == 'sales':
                    Sale.objects.update_or_create(inventory=item, date=sales_date, defaults={'quantity': quantity})
                    changed += 1
                else:
                    changed += process_event({'event_id': f'csv-{digest}-{line}', 'type': 'inventory', 'sku': item.product.sku,
                        'warehouse': item.warehouse.name, 'quantity': quantity}, actor)
            if kind == 'sales':
                Recommendation.objects.filter(inventory_id__in=[entry[0].pk for entry in validated], status='pending').update(
                    status='superseded', decided_at=timezone.now(), decision_note='Sales history updated. Run planning again.')
            AuditLog.objects.create(actor=actor, action=f'{kind.title()} CSV imported', target=f'{len(validated)} rows', details={'changed': changed, 'file_hash': digest})
        return changed, len(validated)
    except (ValueError, UnicodeDecodeError, Inventory.DoesNotExist, KeyError, csv.Error, AttributeError) as exc:
        message = 'Unknown product SKU or warehouse. Create its inventory record first.' if isinstance(exc, Inventory.DoesNotExist) else str(exc)
        IntegrationEvent.objects.create(event_id=f'csv-rejected-{uuid.uuid4().hex}', payload={'kind': kind, 'hash': digest}, status='rejected', error=message)
        raise WorkflowError(message) from exc

