from django.contrib import admin
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

for model in [Inventory, Sale, Forecast, Recommendation, PurchaseOrder, AuditLog, IntegrationEvent, StockMovement, Alert]:
    admin.site.register(model, ReadOnlyAdmin)

