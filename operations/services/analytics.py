import math
import warnings
from datetime import timedelta
import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import SimpleExpSmoothing
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from operations.models import Forecast, Inventory, Recommendation, PurchaseOrder, Alert, AuditLog

class DataIssue(ValueError):
    pass

def supplier_metrics(supplier):
    orders = list(supplier.orders.filter(status='delivered'))
    count = len(orders)
    if not count:
        return {'score': None, 'level': 'unknown', 'on_time': None, 'fulfillment': None, 'deliveries': 0, 'disruptions': 0}
    on_time = sum(o.delivered_date is not None and o.delivered_date <= o.expected_date for o in orders) / count
    fulfillment = sum(o.received_quantity for o in orders) / sum(o.quantity for o in orders)
    disruptions = sum(o.disrupted for o in orders)
    score = round(100 * (.5 * (1 - on_time) + .3 * (1 - fulfillment) + .2 * disruptions / count))
    return {'score': score, 'level': 'high' if score >= 35 else ('medium' if score >= 15 else 'low'),
            'on_time': round(on_time * 100, 1), 'fulfillment': round(fulfillment * 100, 1),
            'deliveries': count, 'disruptions': disruptions}

def _predict(values, horizon, name):
    if name == '7-day moving average':
        return np.repeat(np.mean(values[-7:]), horizon)
    if name == 'Weekly seasonal baseline':
        return np.resize(values[-7:], horizon)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return np.asarray(SimpleExpSmoothing(values, initialization_method='estimated').fit().forecast(horizon))

def build_forecast(inventory, horizon=30):
    if horizon not in (7, 30, 90):
        raise DataIssue('Choose a 7, 30, or 90-day horizon.')
    rows = list(inventory.sales.order_by('date').values('date', 'quantity'))
    if len(rows) < 28:
        raise DataIssue('Insufficient data: at least 28 consecutive days of sales are required. Include explicit zero-sales days.')
    frame = pd.DataFrame(rows)
    dates = pd.to_datetime(frame['date'])
    if (dates.diff().dropna().dt.days != 1).any():
        raise DataIssue('Sales history contains missing dates. Import complete daily records, including zero-sales days.')
    if rows[-1]['date'] < timezone.localdate() - timedelta(days=1):
        raise DataIssue('Sales history is stale. Import sales through yesterday before generating a forecast.')
    values = frame['quantity'].to_numpy(dtype=float)
    holdout = min(14, len(values) // 4)
    train, validation = values[:-holdout], values[-holdout:]
    scores = {}
    for name in ('7-day moving average', 'Weekly seasonal baseline', 'Exponential smoothing'):
        try:
            pred = _predict(train, holdout, name)
            error = float(np.mean(np.abs(validation - pred)))
            if np.isfinite(error):
                scores[name] = error
        except (ValueError, FloatingPointError):
            continue
    if not scores:
        raise DataIssue('Unable to fit a forecast to this history.')
    model = min(scores, key=scores.get)
    predictions = np.maximum(0, _predict(values, horizon, model))
    points = [{'date': (rows[-1]['date'] + timedelta(days=i + 1)).isoformat(), 'quantity': round(float(v), 2)} for i, v in enumerate(predictions)]
    history = [{'date': row['date'].isoformat(), 'quantity': row['quantity']} for row in rows[-60:]]
    return Forecast.objects.create(inventory=inventory, horizon=horizon, model_name=model, history=history,
        points=points, mae=round(scores[model], 2), daily_mean=float(np.mean(predictions)), daily_std=float(np.std(values[-28:], ddof=1)))

@transaction.atomic
def plan_inventory(inventory, actor=None):
    inventory = Inventory.objects.select_for_update().select_related('product__supplier', 'warehouse').get(pk=inventory.pk)
    forecast = build_forecast(inventory)
    supplier = inventory.product.supplier
    risk = supplier_metrics(supplier)
    safety = math.ceil(1.65 * forecast.daily_std * math.sqrt(supplier.lead_time_days))
    reorder = math.ceil(forecast.daily_mean * supplier.lead_time_days + safety)
    inventory.safety_stock = safety
    inventory.reorder_point = reorder
    inventory.save(update_fields=['safety_stock', 'reorder_point', 'updated_at'])
    on_order = inventory.orders.exclude(status__in=['delivered', 'cancelled']).aggregate(total=Sum('quantity'))['total'] or 0
    position = inventory.available + on_order
    previous = Recommendation.objects.filter(inventory=inventory, status='pending')
    previous.update(status='superseded', decision_note='Replaced by a new planning run.', decided_at=timezone.now())
    if position > reorder or forecast.daily_mean == 0:
        return None
    quantity = min(1000000, max(1, math.ceil(forecast.daily_mean * (supplier.lead_time_days + 14) + safety - position)))
    days = inventory.available / forecast.daily_mean if forecast.daily_mean else 0
    priority = 'high' if days < supplier.lead_time_days or (risk['score'] or 0) >= 35 else 'medium'
    reason = f'{inventory.available:,} units available cover about {days:.1f} days. Supplier lead time is {supplier.lead_time_days} days. Replenish to cover lead time plus a 14-day review period.'
    rec = Recommendation.objects.create(inventory=inventory, forecast=forecast, priority=priority, quantity=quantity, reason=reason,
        evidence={'available': inventory.available, 'on_order': on_order, 'position': position, 'reorder_point': reorder,
                  'safety_stock': safety, 'daily_demand': round(forecast.daily_mean, 2), 'lead_time': supplier.lead_time_days,
                  'risk_score': risk['score'], 'risk_level': risk['level'], 'days_cover': round(days, 1),
                  'supplier_id': supplier.pk, 'unit_cost': str(inventory.product.unit_cost)})
    AuditLog.objects.create(actor=actor, action='Recommendation generated', target=f'REC-{rec.pk:04}', details=rec.evidence)
    return rec

def _write_alert(key, **defaults):
    alert, created = Alert.objects.get_or_create(key=key, defaults=defaults)
    if not created:
        if not alert.active:
            alert.read_by.clear()
        for name, value in defaults.items():
            setattr(alert, name, value)
        alert.save(update_fields=list(defaults))


def refresh_alerts():
    active_keys = []
    for item in Inventory.objects.select_related('product', 'warehouse'):
        if item.available <= item.reorder_point:
            key = f'stock-{item.pk}'
            active_keys.append(key)
            _write_alert(key, **{'title': f'Low stock · {item.product.name}',
                'message': f'{item.warehouse.name}: {item.available} available; reorder point {item.reorder_point}.',
                'severity': 'high' if item.available == 0 else 'medium', 'url': f'/inventory/{item.pk}/', 'active': True})
    for order in PurchaseOrder.objects.exclude(status__in=['delivered', 'cancelled', 'draft']).select_related('inventory__product'):
        if order.status == 'delayed' or order.expected_date < timezone.localdate():
            key = f'order-{order.pk}'
            active_keys.append(key)
            _write_alert(key, **{'title': f'Shipment delayed · {order.reference}',
                'message': f'{order.inventory.product.name} was expected on {order.expected_date:%d %b %Y}.',
                'severity': 'high', 'url': f'/orders/{order.pk}/', 'active': True})
    Alert.objects.exclude(key__in=active_keys).update(active=False)

