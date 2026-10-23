from django import forms
from django.core.exceptions import ValidationError
from .models import Supplier, Product, Warehouse, Inventory, PurchaseOrder

class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'})

class SupplierForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'city', 'contact_email', 'lead_time_days']

class ProductForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = ['sku', 'name', 'category', 'unit_cost', 'supplier']

class WarehouseForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ['name', 'city']

class InventoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Inventory
        fields = ['product', 'warehouse', 'quantity', 'reserved']
    def clean(self):
        data = super().clean()
        if data.get('reserved', 0) > data.get('quantity', 0):
            raise ValidationError('Reserved stock cannot exceed on-hand stock.')
        return data

class StockForm(StyledFormMixin, forms.Form):
    quantity = forms.IntegerField(min_value=0, max_value=1000000, label='On-hand units')
    reserved = forms.IntegerField(min_value=0, max_value=1000000, label='Reserved units')
    reason = forms.CharField(max_length=240, widget=forms.Textarea(attrs={'rows': 2}))

class DecisionForm(StyledFormMixin, forms.Form):
    action = forms.ChoiceField(choices=[('approved', 'Approve'), ('rejected', 'Reject'), ('overridden', 'Override quantity')])
    note = forms.CharField(required=False, max_length=1000, widget=forms.Textarea(attrs={'rows': 3}), label='Decision note')
    quantity = forms.IntegerField(required=False, min_value=1, max_value=1000000, label='Override quantity (only for overrides)')

class OrderForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ['inventory', 'supplier', 'quantity', 'unit_cost', 'expected_date', 'carrier', 'tracking_reference']
        widgets = {'expected_date': forms.DateInput(attrs={'type': 'date'})}

class ShipmentForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=PurchaseOrder.Status.choices)
    received_quantity = forms.IntegerField(required=False, min_value=0, max_value=1000000, label='Units received (on delivery)')
    carrier = forms.CharField(required=False, max_length=100)
    tracking_reference = forms.CharField(required=False, max_length=80)

class ImportForm(StyledFormMixin, forms.Form):
    kind = forms.ChoiceField(choices=[('inventory', 'Inventory balances'), ('sales', 'Daily sales history')])
    file = forms.FileField(label='CSV file', widget=forms.ClearableFileInput(attrs={'accept': '.csv'}))

