from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q, F

class Warehouse(models.Model):
    name = models.CharField(max_length=100, unique=True)
    city = models.CharField(max_length=100)
    class Meta:
        ordering = ['name']
    def __str__(self):
        return self.name

class Supplier(models.Model):
    name = models.CharField(max_length=100, unique=True)
    contact_email = models.EmailField(blank=True)
    city = models.CharField(max_length=100)
    lead_time_days = models.PositiveIntegerField(default=7, validators=[MinValueValidator(1), MaxValueValidator(365)])
    class Meta:
        ordering = ['name']
        constraints = [models.CheckConstraint(condition=Q(lead_time_days__gte=1, lead_time_days__lte=365), name='supplier_lead_bounds')]
    def __str__(self):
        return self.name

class Product(models.Model):
    sku = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=60)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='products')
    class Meta:
        ordering = ['name']
        constraints = [models.CheckConstraint(condition=Q(unit_cost__gte=0), name='product_nonnegative_cost')]
    def __str__(self):
        return f'{self.sku} · {self.name}'

class Inventory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='inventory')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(1000000)])
    reserved = models.PositiveIntegerField(default=0)
    reorder_point = models.PositiveIntegerField(default=0)
    safety_stock = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['product__name', 'warehouse__name']
        constraints = [models.UniqueConstraint(fields=['product', 'warehouse'], name='unique_stock_location'),
                       models.CheckConstraint(condition=Q(quantity__gte=F('reserved'), quantity__lte=1000000), name='stock_quantity_bounds')]
    @property
    def available(self):
        return self.quantity - self.reserved
    @property
    def health(self):
        return 'critical' if self.available == 0 else ('low' if self.available <= self.reorder_point else 'healthy')
    def __str__(self):
        return f'{self.product.name} / {self.warehouse.name}'

class Sale(models.Model):
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name='sales')
    date = models.DateField()
    quantity = models.PositiveIntegerField(validators=[MaxValueValidator(1000000)])
    class Meta:
        ordering = ['date']
        constraints = [models.UniqueConstraint(fields=['inventory', 'date'], name='unique_daily_sale'),
                       models.CheckConstraint(condition=Q(quantity__lte=1000000), name='sale_quantity_bound')]

class Forecast(models.Model):
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name='forecasts')
    horizon = models.PositiveIntegerField()
    model_name = models.CharField(max_length=80)
    history = models.JSONField(default=list)
    points = models.JSONField(default=list)
    mae = models.FloatField()
    daily_mean = models.FloatField()
    daily_std = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['inventory', 'horizon', '-created_at'])]

class Recommendation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        OVERRIDDEN = 'overridden', 'Overridden'
        SUPERSEDED = 'superseded', 'Superseded'
    inventory = models.ForeignKey(Inventory, on_delete=models.PROTECT)
    forecast = models.ForeignKey(Forecast, on_delete=models.PROTECT)
    priority = models.CharField(max_length=12, choices=[('high', 'High'), ('medium', 'Medium'), ('low', 'Low')])
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    reason = models.TextField()
    evidence = models.JSONField(default=dict)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['inventory'], condition=Q(status='pending'), name='one_pending_per_stock')]

class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ORDERED = 'ordered', 'Ordered'
        IN_TRANSIT = 'in_transit', 'In transit'
        DELAYED = 'delayed', 'Delayed'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'
    reference = models.CharField(max_length=40, unique=True)
    inventory = models.ForeignKey(Inventory, on_delete=models.PROTECT, related_name='orders')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='orders')
    recommendation = models.OneToOneField(Recommendation, null=True, blank=True, on_delete=models.PROTECT, related_name='order')
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(1000000)])
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    expected_date = models.DateField()
    delivered_date = models.DateField(null=True, blank=True)
    received_quantity = models.PositiveIntegerField(default=0)
    disrupted = models.BooleanField(default=False)
    carrier = models.CharField(max_length=100, blank=True)
    tracking_reference = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=1, quantity__lte=1000000, unit_cost__gte=0), name='order_positive_quantity_cost'),
                       models.CheckConstraint(condition=Q(received_quantity__lte=F('quantity')), name='order_received_bound')]
    @property
    def value(self):
        return self.quantity * self.unit_cost

class StockMovement(models.Model):
    inventory = models.ForeignKey(Inventory, on_delete=models.PROTECT)
    delta = models.IntegerField()
    balance = models.PositiveIntegerField()
    reason = models.CharField(max_length=240)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']

class Alert(models.Model):
    key = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=160)
    message = models.TextField()
    severity = models.CharField(max_length=12, default='medium')
    url = models.CharField(max_length=200, default='/')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_by = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True)
    class Meta:
        ordering = ['-created_at']

class IntegrationEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    payload = models.JSONField()
    status = models.CharField(max_length=16, choices=[('accepted', 'Accepted'), ('rejected', 'Rejected')])
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']

class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    target = models.CharField(max_length=160)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    last_attempt = models.DateTimeField(auto_now=True)

