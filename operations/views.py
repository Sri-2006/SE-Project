import csv
import hashlib
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, F, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from .models import *
from .forms import *
from .permissions import allowed, ALL_ROLES, MANAGEMENT, role
from .services.analytics import supplier_metrics, build_forecast, plan_inventory, refresh_alerts, DataIssue
from .services.workflows import adjust_stock, decide, update_order, WorkflowError, TRANSITIONS
from .services.imports import import_csv, process_event

class SignInView(LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True
    def throttle_key(self):
        return hashlib.sha256((self.request.POST.get('username', '').lower() + '|' + self.request.META.get('REMOTE_ADDR', '')).encode()).hexdigest()
    def post(self, request, *args, **kwargs):
        attempt = LoginAttempt.objects.filter(key=self.throttle_key()).first()
        if attempt and attempt.failures >= 5 and attempt.last_attempt > timezone.now() - timedelta(minutes=15):
            form = self.get_form()
            form.is_valid()
            form.add_error(None, 'Too many sign-in attempts. Please try again in 15 minutes.')
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)
    def form_invalid(self, form):
        key = self.throttle_key()
        attempt, _ = LoginAttempt.objects.get_or_create(key=key)
        if attempt.last_attempt < timezone.now() - timedelta(minutes=15):
            attempt.failures = 0
        attempt.failures += 1
        attempt.save()
        return super().form_invalid(form)
    def form_valid(self, form):
        LoginAttempt.objects.filter(key=self.throttle_key()).delete()
        return super().form_valid(form)

def page(request, template, **context):
    context.setdefault('section', template.split('.')[0])
    return render(request, template, context)

@allowed(*ALL_ROLES)
def dashboard(request):
    stock = list(Inventory.objects.select_related('product__supplier', 'warehouse'))
    low = [i for i in stock if i.health != 'healthy']
    total = sum(i.available for i in stock)
    value = sum(i.quantity * i.product.unit_cost for i in stock)
    daily = list(Sale.objects.filter(date__gte=timezone.localdate() - timedelta(days=30)).values('date').annotate(total=Sum('quantity')).order_by('date'))
    chart = {'labels': [r['date'].strftime('%d %b') for r in daily], 'actual': [r['total'] for r in daily]}
    recs = Recommendation.objects.filter(status='pending').select_related('inventory__product', 'inventory__warehouse')
    return page(request, 'dashboard.html', title='Overview', stock_count=len(stock), total=total, stock_value=value,
        low_count=len(low), low_items=low[:5], recommendation_count=recs.count(), recommendations=recs[:4],
        delayed_count=PurchaseOrder.objects.filter(Q(status='delayed') | Q(expected_date__lt=timezone.localdate(), status__in=['ordered', 'in_transit'])).count(),
        chart=chart, activities=AuditLog.objects.select_related('actor')[:6], alerts=Alert.objects.filter(active=True)[:3],
        healthy_count=len(stock) - len(low), warehouse_count=Warehouse.objects.count())

@allowed(*ALL_ROLES)
def inventory(request):
    items = Inventory.objects.select_related('product__supplier', 'warehouse')
    query = request.GET.get('q', '').strip()
    if query:
        items = items.filter(Q(product__name__icontains=query) | Q(product__sku__icontains=query))
    location = request.GET.get('warehouse', '')
    if location.isdigit():
        items = items.filter(warehouse_id=int(location))
    health = request.GET.get('health', '')
    if health == 'low':
        items = items.filter(quantity__lte=F('reserved') + F('reorder_point'))
    if health == 'healthy':
        items = items.filter(quantity__gt=F('reserved') + F('reorder_point'))
    context = {'items': Paginator(items, 15).get_page(request.GET.get('page')), 'query': query, 'location': location,
               'health': health, 'warehouses': Warehouse.objects.all(), 'title': 'Inventory', 'section': 'inventory'}
    return render(request, 'partials/inventory_table.html' if request.headers.get('HX-Request') else 'inventory.html', context)

@allowed(*ALL_ROLES)
def inventory_detail(request, pk):
    item = get_object_or_404(Inventory.objects.select_related('product__supplier', 'warehouse'), pk=pk)
    form = StockForm(request.POST or None, initial={'quantity': item.quantity, 'reserved': item.reserved})
    if request.method == 'POST' and form.is_valid():
        try:
            adjust_stock(pk, actor=request.user, **form.cleaned_data)
            messages.success(request, 'Inventory updated. The adjustment is recorded in the stock ledger.')
            return redirect('inventory_detail', pk=pk)
        except WorkflowError as exc:
            form.add_error(None, str(exc))
    return page(request, 'inventory_detail.html', title=item.product.name, section='inventory', item=item, form=form,
                movements=StockMovement.objects.filter(inventory=item).select_related('actor')[:20])

@allowed(*ALL_ROLES)
def forecasting(request):
    items = Inventory.objects.select_related('product', 'warehouse')
    selected = request.POST.get('inventory') or request.GET.get('inventory')
    item = get_object_or_404(items, pk=selected) if selected and selected.isdigit() else items.first()
    raw_horizon = request.POST.get('horizon') or request.GET.get('horizon', '30')
    horizon = int(raw_horizon) if raw_horizon in ('7', '30', '90') else 30
    error = ''
    if request.method == 'POST' and item:
        try:
            result = build_forecast(item, horizon)
            AuditLog.objects.create(actor=request.user, action='Forecast generated', target=str(item), details={'horizon': horizon, 'model': result.model_name})
            messages.success(request, 'Forecast generated and evaluated against recent held-out sales.')
            return redirect(f'/forecasting/?inventory={item.pk}&horizon={horizon}')
        except DataIssue as exc:
            error = str(exc)
    result = item.forecasts.filter(horizon=horizon).first() if item else None
    chart = None
    if result:
        chart = {'labels': [p['date'] for p in result.history + result.points],
                 'actual': [p['quantity'] for p in result.history] + [None] * len(result.points),
                 'forecast': [None] * (len(result.history) - 1) + [result.history[-1]['quantity']] + [p['quantity'] for p in result.points]}
    return page(request, 'forecasting.html', title='Demand forecasting', items=items, item=item, horizon=horizon,
                forecast=result, chart=chart, error=error, forecast_total=round(sum(p['quantity'] for p in result.points)) if result else 0)

@allowed(*ALL_ROLES)
def suppliers(request):
    rows = [{'supplier': supplier, 'metrics': supplier_metrics(supplier)} for supplier in Supplier.objects.all()]
    return page(request, 'suppliers.html', title='Supplier intelligence', rows=rows)

@allowed(*ALL_ROLES)
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    return page(request, 'supplier_detail.html', title=supplier.name, section='suppliers', supplier=supplier,
                metrics=supplier_metrics(supplier), orders=supplier.orders.select_related('inventory__product')[:20])

@allowed(*ALL_ROLES)
def recommendations(request):
    state = request.GET.get('status', 'pending')
    rows = Recommendation.objects.select_related('inventory__product', 'inventory__warehouse', 'decided_by')
    if state in Recommendation.Status.values:
        rows = rows.filter(status=state)
    elif state != 'all':
        state = 'pending'
        rows = rows.filter(status=state)
    return page(request, 'recommendations.html', title='Decision center', recommendations=Paginator(rows, 12).get_page(request.GET.get('page')),
                state=state, pending_count=Recommendation.objects.filter(status='pending').count(), statuses=Recommendation.Status.choices)

@allowed(*ALL_ROLES)
@require_POST
def run_planning(request):
    success, skipped, errors = 0, 0, []
    for item in Inventory.objects.select_related('product', 'warehouse'):
        try:
            if plan_inventory(item, request.user):
                success += 1
        except DataIssue as exc:
            skipped += 1
            errors.append(f'{item.product.sku} / {item.warehouse.name}: {exc}')
    refresh_alerts()
    messages.success(request, f'Planning complete: {success} recommendations generated; {skipped} locations skipped for insufficient or stale data.')
    if errors:
        messages.warning(request, errors[0] + (' See sales imports for the other affected locations.' if len(errors) > 1 else ''))
    return redirect('recommendations')

@allowed(*ALL_ROLES)
def recommendation_detail(request, pk):
    rec = get_object_or_404(Recommendation.objects.select_related('inventory__product__supplier', 'inventory__warehouse', 'forecast', 'decided_by'), pk=pk)
    form = DecisionForm(request.POST or None, initial={'quantity': rec.quantity})
    if role(request.user) == 'Planner':
        form.fields['action'].choices = [('approved', 'Approve'), ('rejected', 'Reject')]
        form.fields.pop('quantity')
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            decide(pk, actor=request.user, **form.cleaned_data)
            messages.success(request, 'Decision recorded.' + (' A draft purchase order is ready for review.' if form.cleaned_data['action'] != 'rejected' else ''))
            return redirect('recommendation_detail', pk=pk)
        except WorkflowError as exc:
            form.add_error(None, str(exc))
            status = 409
    return render(request, 'recommendation_detail.html', {'title': f'REC-{rec.pk:04}', 'section': 'recommendations', 'rec': rec, 'form': form}, status=status)

@allowed(*ALL_ROLES)
def orders(request):
    state = request.GET.get('status', 'active')
    rows = PurchaseOrder.objects.select_related('inventory__product', 'inventory__warehouse', 'supplier')
    if state == 'active':
        rows = rows.exclude(status__in=['delivered', 'cancelled'])
    elif state in PurchaseOrder.Status.values:
        rows = rows.filter(status=state)
    return page(request, 'orders.html', title='Orders & shipments', orders=Paginator(rows, 15).get_page(request.GET.get('page')), state=state, statuses=PurchaseOrder.Status.choices)

@allowed(*ALL_ROLES)
def order_detail(request, pk):
    order = get_object_or_404(PurchaseOrder.objects.select_related('inventory__product', 'inventory__warehouse', 'supplier'), pk=pk)
    form = ShipmentForm(request.POST or None, initial={'received_quantity': order.quantity, 'carrier': order.carrier, 'tracking_reference': order.tracking_reference})
    form.fields['status'].choices = [(s, label) for s, label in PurchaseOrder.Status.choices if s in TRANSITIONS[order.status]]
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            update_order(pk, actor=request.user, received=form.cleaned_data['received_quantity'], status=form.cleaned_data['status'],
                         carrier=form.cleaned_data['carrier'], tracking=form.cleaned_data['tracking_reference'])
            messages.success(request, 'Order updated.' + (' Received units have been added to inventory.' if form.cleaned_data['status'] == 'delivered' else ''))
            return redirect('order_detail', pk=pk)
        except WorkflowError as exc:
            form.add_error(None, str(exc))
            status = 409
    return render(request, 'order_detail.html', {'title': order.reference, 'section': 'orders', 'order': order, 'form': form,
                'can_transition': bool(TRANSITIONS[order.status])}, status=status)

@allowed(*ALL_ROLES)
def create_order(request):
    form = OrderForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            order = form.save(commit=False)
            Inventory.objects.select_for_update().get(pk=order.inventory_id)
            order.reference = f'PO-{uuid.uuid4().hex[:8].upper()}'
            order.save()
            AuditLog.objects.create(actor=request.user, action='Purchase order drafted', target=order.reference)
        messages.success(request, 'Draft purchase order created.')
        return redirect('order_detail', pk=order.pk)
    return page(request, 'form.html', title='Create purchase order', section='orders', form=form, back_url='/orders/')

@allowed(*ALL_ROLES)
def alerts(request):
    rows = Alert.objects.filter(active=True)
    read_ids = set(rows.filter(read_by=request.user).values_list('id', flat=True))
    return page(request, 'alerts.html', title='Alerts & activity', alerts=rows, read_ids=read_ids, activities=AuditLog.objects.select_related('actor')[:30])

@allowed(*ALL_ROLES)
@require_POST
def read_alert(request, pk):
    alert = get_object_or_404(Alert, pk=pk)
    alert.read_by.add(request.user)
    return redirect('alerts')

def report_data(request):
    end = timezone.localdate()
    start = end - timedelta(days=30)
    try:
        start = date.fromisoformat(request.GET.get('start', start.isoformat()))
        end = date.fromisoformat(request.GET.get('end', end.isoformat()))
        if start > end:
            raise ValueError
    except ValueError:
        raise WorkflowError('Select a valid date range with the start before the end.')
    sales = Sale.objects.filter(date__range=(start, end)).values('inventory__product__sku', 'inventory__product__name', 'inventory__warehouse__name').annotate(units=Sum('quantity')).order_by('-units')
    orders = PurchaseOrder.objects.filter(delivered_date__range=(start, end), status='delivered')
    delivered = list(orders)
    return {'start': start, 'end': end, 'rows': list(sales), 'units': sum(r['units'] for r in sales),
            'deliveries': len(delivered), 'on_time': round(100 * sum(o.delivered_date <= o.expected_date for o in delivered) / len(delivered), 1) if delivered else None,
            'decisions': Recommendation.objects.filter(decided_at__date__range=(start, end), status__in=['approved', 'rejected', 'overridden']).count()}

@allowed(*MANAGEMENT)
def reports(request):
    try:
        context = report_data(request)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect('reports')
    return page(request, 'reports.html', title='Performance reports', **context)

@allowed(*MANAGEMENT)
def export_report(request):
    try:
        data = report_data(request)
    except WorkflowError as exc:
        return HttpResponse(str(exc), status=400)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="supplypilot-{data["start"]}-{data["end"]}.csv"'
    writer = csv.writer(response)
    writer.writerow(['SKU', 'Product', 'Warehouse', 'Units sold', 'Period start', 'Period end'])
    for row in data['rows']:
        cells = [row['inventory__product__sku'], row['inventory__product__name'], row['inventory__warehouse__name'], row['units'], str(data['start']), str(data['end'])]
        writer.writerow(["'" + str(c) if str(c).lstrip().startswith(('=', '+', '-', '@')) else c for c in cells])
    return response

@allowed(*ALL_ROLES)
def imports(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            changed, total = import_csv(form.cleaned_data['file'], form.cleaned_data['kind'], request.user)
            refresh_alerts()
            messages.success(request, f'Import complete: {changed} of {total} rows applied. Duplicate inventory events are skipped.')
            return redirect('imports')
        except WorkflowError as exc:
            form.add_error(None, str(exc))
    return page(request, 'imports.html', title='Data workspace', form=form, events=IntegrationEvent.objects.all()[:20])

@allowed(*ALL_ROLES)
@require_POST
def event_api(request):
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        IntegrationEvent.objects.create(event_id=f'malformed-{uuid.uuid4().hex}', payload={}, status='rejected', error='Malformed JSON')
        return JsonResponse({'error': 'Malformed JSON.'}, status=400)
    try:
        changed = process_event(payload, request.user)
        return JsonResponse({'status': 'accepted' if changed else 'duplicate'}, status=201 if changed else 200)
    except WorkflowError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

@allowed(*MANAGEMENT)
def catalog_form(request, kind, pk=None):
    types = {'product': (Product, ProductForm, 'inventory'), 'supplier': (Supplier, SupplierForm, 'suppliers'),
             'warehouse': (Warehouse, WarehouseForm, 'inventory'), 'inventory': (Inventory, InventoryForm, 'inventory')}
    if kind not in types:
        return not_found(request, None)
    if kind == 'inventory' and pk:
        return redirect('inventory_detail', pk=pk)
    model, form_type, destination = types[kind]
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = form_type(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj = form.save()
            AuditLog.objects.create(actor=request.user, action=f'{kind.title()} saved', target=str(obj))
            if kind == 'inventory':
                StockMovement.objects.create(inventory=obj, delta=obj.quantity, balance=obj.quantity, reason='Opening stock', actor=request.user)
        refresh_alerts()
        messages.success(request, f'{kind.title()} saved.')
        return redirect(destination)
    return page(request, 'form.html', title=f'{"Edit" if pk else "Add"} {kind}', section=destination, form=form, back_url=f'/{destination}/')

@require_GET
def health(request):
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok', 'application': 'supplypilot'})
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)

def forbidden(request, exception):
    return render(request, 'error.html', {'code': 403, 'message': 'Your role does not have access to this action.'}, status=403)

def not_found(request, exception):
    return render(request, 'error.html', {'code': 404, 'message': 'This page or record could not be found.'}, status=404)

def server_error(request):
    return render(request, 'error.html', {'code': 500, 'message': 'We could not complete this request. Please retry; contact your administrator if it continues.'}, status=500)

