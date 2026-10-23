from django.contrib import admin
from django.shortcuts import redirect
from .permissions import role, MANAGEMENT
from .models import Supplier, Product, Warehouse, Inventory, Sale, Forecast, Recommendation, PurchaseOrder, AuditLog, IntegrationEvent, StockMovement, Alert

admin.site.site_header = 'SupplyPilot administration'
admin.site.site_title = 'SupplyPilot'

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'category', 'supplier', 'unit_cost']
    search_fields = ['sku', 'name']

admin.site.register([Supplier, Warehouse])

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Inventory)
class InventoryAdmin(ReadOnlyAdmin):
    list_display = ['product', 'warehouse', 'quantity', 'reserved']
    search_fields = ['product__sku', 'product__name', 'warehouse__name']

    def has_add_permission(self, request):
        return role(request.user) in MANAGEMENT

    def has_change_permission(self, request, obj=None):
        return role(request.user) in MANAGEMENT

    def add_view(self, request, form_url='', extra_context=None):
        return redirect('catalog_new', kind='inventory')

    def change_view(self, request, object_id, form_url='', extra_context=None):
        return redirect('inventory_detail', pk=object_id)

for model in [Sale, Forecast, Recommendation, PurchaseOrder, AuditLog, IntegrationEvent, StockMovement, Alert]:
    admin.site.register(model, ReadOnlyAdmin)

